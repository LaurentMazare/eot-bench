from __future__ import annotations

# TODO: this is wrong, change to use smart_turn.

import asyncio
import json
from typing import Any
from urllib.parse import urlencode

from .io import DEFAULT_INFERENCE_INTERVAL
from .languages import normalize_language_code, row_language, supports_language_code
from .streaming_stt import (
    DEFAULT_CHUNK_MS,
    SAMPLE_RATE,
    build_event_prediction_rows,
    chunk_size_bytes,
    import_websockets,
    prepare_pcm16_audio,
    resolve_api_key,
)

DEFAULT_CONCURRENCY = 4
XAI_STT_LANGUAGE_MAP = {
    "ar": "ar",
    "de": "de",
    "en": "en",
    "es": "es",
    "fr": "fr",
    "hi": "hi",
    "id": "id",
    "it": "it",
    "ja": "ja",
    "ko": "ko",
    "nl": "nl",
    "pt": "pt",
    "tr": "tr",
}


class XAIStreamingSTTAdapter:
    """Replay full turns through xAI streaming STT and score utterance-final events."""

    display_name = "xAI STT"
    def __init__(
        self,
        *,
        chunk_ms: int = DEFAULT_CHUNK_MS,
        concurrency: int = DEFAULT_CONCURRENCY,
        endpointing_ms: int = 10,
        interim_results: bool = True,
    ) -> None:
        if chunk_ms <= 0:
            raise ValueError("chunk_ms must be positive")
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if not 0 <= int(endpointing_ms) <= 5000:
            raise ValueError("endpointing_ms must be between 0 and 5000")

        self.chunk_ms = int(chunk_ms)
        self.concurrency = int(concurrency)
        self.endpointing_ms = int(endpointing_ms)
        self.interim_results = bool(interim_results)

    @property
    def adapter_id(self) -> str:
        return "xai/stt-streaming"

    def supports_language(self, lang_code: str) -> bool:
        return supports_language_code(lang_code, set(XAI_STT_LANGUAGE_MAP))

    async def predict_turn(
        self,
        row: dict[str, Any],
        *,
        inference_interval: float = DEFAULT_INFERENCE_INTERVAL,
    ) -> dict[str, Any]:
        return await self._replay_turn(
            row,
            resolve_api_key("XAI_API_KEY"),
            inference_interval=inference_interval,
        )

    async def _replay_turn(
        self,
        row: dict[str, Any],
        api_key: str,
        *,
        inference_interval: float,
    ) -> dict[str, Any]:
        websockets = import_websockets()
        audio_bytes, total_audio_sec = prepare_pcm16_audio(row, sample_rate=SAMPLE_RATE)
        chunk_size = chunk_size_bytes(sample_rate=SAMPLE_RATE, chunk_ms=self.chunk_ms)
        url = f"wss://api.x.ai/v1/stt?{urlencode(self._connection_params(language=row_language(row)))}"
        events: list[dict[str, Any]] = []

        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            first = json.loads(await ws.recv())
            if first.get("type") == "error":
                raise RuntimeError(f"xAI STT error for row {row['id']!r}: {first}")
            if first.get("type") != "transcript.created":
                raise RuntimeError(f"Unexpected xAI STT initial event for row {row['id']!r}: {first}")

            recv_task = asyncio.create_task(_recv_xai_events(ws, row_id=row["id"], events=events))
            for start in range(0, len(audio_bytes), chunk_size):
                await ws.send(audio_bytes[start : start + chunk_size])
                await asyncio.sleep(self.chunk_ms / 1000.0)
            await ws.send(json.dumps({"type": "audio.done"}))
            try:
                await asyncio.wait_for(recv_task, timeout=15.0)
            except asyncio.TimeoutError as exc:
                recv_task.cancel()
                raise RuntimeError(f"Timed out waiting for xAI STT done on {row['id']!r}.") from exc

        return {
            "id": row["id"],
            "audio_sec": total_audio_sec,
            "events": events,
            "prediction_rows": build_event_prediction_rows(
                row,
                events,
                inference_interval=inference_interval,
            ),
        }

    def _connection_params(self, *, language: str) -> dict[str, str]:
        return {
            "sample_rate": str(SAMPLE_RATE),
            "encoding": "pcm",
            "interim_results": str(self.interim_results).lower(),
            "endpointing": str(self.endpointing_ms),
            "language": _xai_language(language),
        }


def _xai_language(lang_code: str) -> str:
    language = normalize_language_code(lang_code)
    try:
        return XAI_STT_LANGUAGE_MAP[language]
    except KeyError as exc:
        raise ValueError(f"Unsupported xAI STT language: {language!r}") from exc


async def _recv_xai_events(ws, *, row_id: Any, events: list[dict[str, Any]]) -> None:
    async for message in ws:
        data = json.loads(message)
        msg_type = data.get("type")
        if msg_type == "error":
            raise RuntimeError(f"xAI STT error for row {row_id!r}: {data}")
        if msg_type == "transcript.done":
            return
        if msg_type != "transcript.partial":
            continue
        event = _xai_endpoint_event(data)
        if event is not None:
            events.append(event)


def _xai_endpoint_event(data: dict[str, Any]) -> dict[str, Any] | None:
    if not (bool(data.get("is_final")) and bool(data.get("speech_final"))):
        return None

    timestamp = data.get("timestamp")
    if timestamp is None:
        start = float(data.get("start") or 0.0)
        duration = float(data.get("duration") or 0.0)
        timestamp = start + duration

    return {
        "event": "UtteranceFinal",
        "timestamp": float(timestamp),
        "p_eot": 1.0,
        "transcript": data.get("text", ""),
        "start": data.get("start"),
        "duration": data.get("duration"),
    }
