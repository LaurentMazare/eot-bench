from __future__ import annotations

import asyncio
import base64
import json
from contextlib import suppress
from typing import Any
from urllib.parse import urlencode

from .io import DEFAULT_INFERENCE_INTERVAL
from .languages import supports_any_benchmark_language
from .streaming_stt import (
    DEFAULT_CHUNK_MS,
    build_event_prediction_rows,
    chunk_size_bytes,
    import_websockets,
    prepare_pcm16_audio,
    resolve_api_key,
)

OPENAI_REALTIME_SAMPLE_RATE = 24000
DEFAULT_OPENAI_REALTIME_MODEL = "gpt-realtime-2"
DEFAULT_CONCURRENCY = 4
DEFAULT_POST_AUDIO_WAIT_S = 5.0
VALID_SEMANTIC_VAD_EAGERNESS = {"auto", "low", "medium", "high"}


class OpenAIRealtime2Adapter:
    """Replay full turns through OpenAI Realtime semantic VAD and score speech-stop events."""

    display_name = "OpenAI GPT Realtime 2"

    def __init__(
        self,
        *,
        model: str = DEFAULT_OPENAI_REALTIME_MODEL,
        chunk_ms: int = DEFAULT_CHUNK_MS,
        concurrency: int = DEFAULT_CONCURRENCY,
        eagerness: str = "auto",
        post_audio_wait_s: float = DEFAULT_POST_AUDIO_WAIT_S,
    ) -> None:
        if not model:
            raise ValueError("model must be a non-empty string")
        if chunk_ms <= 0:
            raise ValueError("chunk_ms must be positive")
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if eagerness not in VALID_SEMANTIC_VAD_EAGERNESS:
            raise ValueError(f"eagerness must be one of {sorted(VALID_SEMANTIC_VAD_EAGERNESS)}")
        if post_audio_wait_s <= 0:
            raise ValueError("post_audio_wait_s must be positive")

        self.model = model
        self.chunk_ms = int(chunk_ms)
        self.concurrency = int(concurrency)
        self.eagerness = eagerness
        self.post_audio_wait_s = float(post_audio_wait_s)

    @property
    def adapter_id(self) -> str:
        return f"openai/{self.model}/semantic_vad_{self.eagerness}"

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
            resolve_api_key("OPENAI_API_KEY"),
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
        audio_bytes, total_audio_sec = prepare_pcm16_audio(row, sample_rate=OPENAI_REALTIME_SAMPLE_RATE)
        chunk_size = chunk_size_bytes(sample_rate=OPENAI_REALTIME_SAMPLE_RATE, chunk_ms=self.chunk_ms)
        url = f"wss://api.openai.com/v1/realtime?{urlencode({'model': self.model})}"
        events: list[dict[str, Any]] = []
        session_updated = asyncio.Event()

        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            recv_task = asyncio.create_task(
                _recv_openai_realtime_events(
                    ws,
                    row_id=row["id"],
                    events=events,
                    session_updated=session_updated,
                ),
            )
            await ws.send(json.dumps(self._session_update()))
            recv_task_consumed = False
            try:
                await _wait_for_session_update(session_updated, recv_task, row_id=row["id"])
                await self._send_audio(ws, audio_bytes, chunk_size=chunk_size)
                await asyncio.sleep(self.post_audio_wait_s)
                if recv_task.done():
                    await recv_task
                    recv_task_consumed = True
            except Exception:
                recv_task.cancel()
                with suppress(asyncio.CancelledError):
                    await recv_task
                recv_task_consumed = True
                raise
            finally:
                if not recv_task_consumed and not recv_task.done():
                    recv_task.cancel()
                if not recv_task_consumed:
                    with suppress(asyncio.CancelledError):
                        await recv_task

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

    def _session_update(self) -> dict[str, Any]:
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": OPENAI_REALTIME_SAMPLE_RATE,
                        },
                        "turn_detection": {
                            "type": "semantic_vad",
                            "eagerness": self.eagerness,
                            "create_response": False,
                            "interrupt_response": False,
                        },
                    },
                },
            },
        }

    async def _send_audio(self, ws, audio_bytes: bytes, *, chunk_size: int) -> None:
        for start in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[start : start + chunk_size]
            await ws.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    },
                ),
            )
            await asyncio.sleep(self.chunk_ms / 1000.0)


async def _recv_openai_realtime_events(
    ws,
    *,
    row_id: Any,
    events: list[dict[str, Any]],
    session_updated: asyncio.Event | None = None,
) -> None:
    async for message in ws:
        data = json.loads(message)
        msg_type = data.get("type")
        if msg_type == "error":
            raise RuntimeError(f"OpenAI Realtime error for row {row_id!r}: {data}")
        if msg_type == "session.updated" and session_updated is not None:
            session_updated.set()
            continue
        if msg_type != "input_audio_buffer.speech_stopped":
            continue
        event = _openai_speech_stopped_event(data)
        if event is not None:
            events.append(event)


async def _wait_for_session_update(
    session_updated: asyncio.Event,
    recv_task: asyncio.Task,
    *,
    row_id: Any,
    timeout: float = 10.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not session_updated.is_set():
        if recv_task.done():
            await recv_task
        if loop.time() >= deadline:
            raise RuntimeError(f"Timed out waiting for OpenAI Realtime session.updated on {row_id!r}.")
        await asyncio.sleep(0.05)


def _openai_speech_stopped_event(data: dict[str, Any]) -> dict[str, Any] | None:
    audio_end_ms = data.get("audio_end_ms")
    if audio_end_ms is None:
        return None
    return {
        "event": "SpeechStopped",
        "timestamp": float(audio_end_ms) / 1000.0,
        "p_eot": 1.0,
        "audio_end_ms": audio_end_ms,
        "audio_start_ms": data.get("audio_start_ms"),
        "item_id": data.get("item_id"),
    }
