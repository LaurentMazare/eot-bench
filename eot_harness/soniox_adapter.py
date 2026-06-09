from __future__ import annotations

import asyncio
import json
from typing import Any

from .io import DEFAULT_INFERENCE_INTERVAL
from .languages import supports_any_benchmark_language, row_language
from .streaming_stt import (
    DEFAULT_CHUNK_MS,
    SAMPLE_RATE,
    build_event_prediction_rows,
    chunk_size_bytes,
    import_websockets,
    prepare_pcm16_audio,
    resolve_api_key,
)

DEFAULT_SONIOX_MODEL = "stt-rt-preview"
DEFAULT_CONCURRENCY = 4


class SonioxStreamingAdapter:
    """Replay full turns through Soniox realtime STT and score endpoint tokens."""

    display_name = "Soniox"
    def __init__(
        self,
        *,
        model: str = DEFAULT_SONIOX_MODEL,
        chunk_ms: int = DEFAULT_CHUNK_MS,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_endpoint_delay_ms: int = 2000,
    ) -> None:
        if not model:
            raise ValueError("model must be a non-empty string")
        if chunk_ms <= 0:
            raise ValueError("chunk_ms must be positive")
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if not 500 <= int(max_endpoint_delay_ms) <= 3000:
            raise ValueError("max_endpoint_delay_ms must be between 500 and 3000")

        self.model = model
        self.chunk_ms = int(chunk_ms)
        self.concurrency = int(concurrency)
        self.max_endpoint_delay_ms = int(max_endpoint_delay_ms)

    @property
    def adapter_id(self) -> str:
        return f"soniox/{self.model}"

    def supports_language(self, lang_code: str) -> bool:
        return supports_any_benchmark_language(lang_code)

    async def predict_turn(
        self,
        row: dict[str, Any],
        *,
        inference_interval: float = DEFAULT_INFERENCE_INTERVAL,
    ) -> dict[str, Any]:
        return await self._replay_turn(
            row,
            resolve_api_key("SONIOX_API_KEY"),
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
        events: list[dict[str, Any]] = []

        async with websockets.connect(
            "wss://stt-rt.soniox.com/transcribe-websocket",
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            await ws.send(json.dumps(self._start_message(api_key, language=row_language(row))))
            recv_task = asyncio.create_task(_recv_soniox_events(ws, row_id=row["id"], events=events))
            for start in range(0, len(audio_bytes), chunk_size):
                await ws.send(audio_bytes[start : start + chunk_size])
                await asyncio.sleep(self.chunk_ms / 1000.0)
            await ws.send("")
            try:
                await asyncio.wait_for(recv_task, timeout=15.0)
            except asyncio.TimeoutError as exc:
                recv_task.cancel()
                raise RuntimeError(f"Timed out waiting for Soniox finish on {row['id']!r}.") from exc

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

    def _start_message(self, api_key: str, *, language: str) -> dict[str, Any]:
        return {
            "api_key": api_key,
            "model": self.model,
            "audio_format": "pcm_s16le",
            "num_channels": 1,
            "sample_rate": SAMPLE_RATE,
            "language_hints": [language],
            "enable_endpoint_detection": True,
            "max_endpoint_delay_ms": self.max_endpoint_delay_ms,
        }


async def _recv_soniox_events(ws, *, row_id: Any, events: list[dict[str, Any]]) -> None:
    async for message in ws:
        data = json.loads(message)
        if data.get("error_code") is not None:
            raise RuntimeError(f"Soniox error for row {row_id!r}: {data}")
        event = _soniox_endpoint_event(data)
        if event is not None:
            events.append(event)
        if data.get("finished"):
            return


def _soniox_endpoint_event(data: dict[str, Any]) -> dict[str, Any] | None:
    tokens = data.get("tokens") or []
    if not any(str(token.get("text") or "") == "<end>" and bool(token.get("is_final")) for token in tokens):
        return None

    timestamp_ms = data.get("final_audio_proc_ms")
    if timestamp_ms is None:
        timestamp_ms = data.get("total_audio_proc_ms")
    if timestamp_ms is None:
        timestamp_ms = max((token.get("end_ms") or 0 for token in tokens), default=0)

    return {
        "event": "Endpoint",
        "timestamp": float(timestamp_ms) / 1000.0,
        "p_eot": 1.0,
        "token": "<end>",
        "final_audio_proc_ms": data.get("final_audio_proc_ms"),
        "total_audio_proc_ms": data.get("total_audio_proc_ms"),
    }
