from __future__ import annotations

import numpy as np
import pytest

from eot_harness.livekit_text_adapter import LiveKitTextTurnDetectorAdapter


class _FakeTokenizer:
    def __init__(self) -> None:
        self.padding_side = None
        self.seen_templates: list[list[dict]] = []

    def apply_chat_template(self, chat, add_generation_prompt=False, add_special_tokens=False, tokenize=False):
        self.seen_templates.append(chat)
        return "|".join(message["content"] for message in chat) + "<|im_end|>"

    def __call__(self, text, add_special_tokens=False, return_tensors="np", max_length=128, truncation=True):
        ids = np.arange(1, min(len(text), max_length) + 1, dtype=np.int64)
        if ids.size == 0:
            ids = np.array([0], dtype=np.int64)
        return {"input_ids": ids.reshape(1, -1)}


class _FakeSession:
    def run(self, _unused, feeds):
        input_ids = feeds["input_ids"]
        logits = input_ids[:, -1:].astype(np.float32) / 100.0
        return [logits]


def test_livekit_text_adapter_uses_full_message_history(monkeypatch) -> None:
    monkeypatch.setattr(
        "eot_harness.livekit_text_adapter._download_onnx_path",
        lambda **_: "/tmp/fake-model.onnx",
    )
    monkeypatch.setattr(
        "eot_harness.livekit_text_adapter._load_tokenizer",
        lambda **_: _FakeTokenizer(),
    )
    monkeypatch.setattr(
        "eot_harness.livekit_text_adapter._make_ort_session",
        lambda *args, **kwargs: _FakeSession(),
    )

    with pytest.warns(DeprecationWarning, match="LiveKit text adapters are deprecated"):
        adapter = LiveKitTextTurnDetectorAdapter()
    assert not hasattr(adapter, "score_point")
    scores = adapter.predict_batch(
        [
            {
                "audio": {"array": np.zeros(1600, dtype=np.float32), "sampling_rate": 16000},
                "messages": [
                    {"role": "assistant", "content": "hello"},
                    {"role": "user", "content": "old text"},
                    {"role": "assistant", "content": "follow up"},
                    {"role": "user", "content": "Current, TEXT!"},
                ],
            },
            {
                "audio": {"array": np.zeros(1600, dtype=np.float32), "sampling_rate": 16000},
                "messages": [
                    {"role": "assistant", "content": "   "},
                ],
            },
        ],
    )

    assert scores[0] > 0.0
    assert scores[1] == 0.0
    assert adapter.tokenizer.seen_templates == [
        [
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "old text"},
            {"role": "assistant", "content": "follow up"},
            {"role": "user", "content": "current text"},
        ]
    ]
