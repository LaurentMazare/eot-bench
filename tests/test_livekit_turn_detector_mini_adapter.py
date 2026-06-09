from __future__ import annotations

import time

import numpy as np

from eot_harness import livekit_turn_detector_mini_adapter as module
from eot_harness.livekit_turn_detector_mini_adapter import LiveKitTurnDetectorMiniAdapter


class FakeEOT:
    def __init__(self) -> None:
        self.calls: list[np.ndarray] = []
        self.closed = False

    def predict(self, pcm: np.ndarray) -> float:
        self.calls.append(pcm.copy())
        return float(len(pcm)) / 10.0

    def close(self) -> None:
        self.closed = True


def test_livekit_turn_detector_mini_adapter_defaults(monkeypatch) -> None:
    fake_model = FakeEOT()
    monkeypatch.setattr(module, "_load_local_eot_model", lambda: fake_model)
    monkeypatch.setattr(module, "_local_inference_version", lambda: "0.2.5")

    adapter = LiveKitTurnDetectorMiniAdapter()

    assert adapter.score_point == 0.2
    assert adapter.display_name == "LiveKit Turn Detector v1-mini"
    assert adapter.model_id == "turn-detector-v1-mini"
    assert adapter.revision == "livekit-local-inference"
    assert adapter.sample_rate == 16000
    assert adapter.audio_sec == 1.2
    assert adapter.max_audio_sec == 1.2
    assert adapter.max_samples == 19200
    assert adapter.num_workers >= 1
    assert adapter.adapter_id == "livekit/turn-detector-v1-mini-livekit-local-inference-0.2.5"
    adapter.close()


def test_livekit_turn_detector_mini_adapter_predicts_pcm16_tail(monkeypatch) -> None:
    monkeypatch.setenv(module.LIVEKIT_TURN_DETECTOR_MINI_WORKERS_ENV, "1")
    fake_model = FakeEOT()
    monkeypatch.setattr(module, "_load_local_eot_model", lambda: fake_model)
    monkeypatch.setattr(module, "_local_inference_version", lambda: "0.2.5")

    adapter = LiveKitTurnDetectorMiniAdapter(sample_rate=10, audio_sec=0.3)
    scores = adapter.predict_batch(
        [
            {
                "audio": {
                    "array": np.asarray([-1.5, -0.5, 0.0, 0.5, 1.5], dtype=np.float32),
                    "sampling_rate": 10,
                },
                "messages": [{"role": "user", "content": "ignored"}],
            }
        ]
    )

    assert scores == [0.3]
    assert len(fake_model.calls) == 1
    assert fake_model.calls[0].dtype == np.int16
    assert fake_model.calls[0].flags.c_contiguous
    np.testing.assert_array_equal(fake_model.calls[0], np.asarray([0, 16383, 32767], dtype=np.int16))
    adapter.close()
    assert fake_model.closed is True


def test_livekit_turn_detector_mini_adapter_can_predict_in_parallel(monkeypatch) -> None:
    monkeypatch.setenv(module.LIVEKIT_TURN_DETECTOR_MINI_WORKERS_ENV, "2")
    fake_models: list[FakeEOT] = []

    class SlowFakeEOT(FakeEOT):
        def predict(self, pcm: np.ndarray) -> float:
            time.sleep(0.02)
            return super().predict(pcm)

    def fake_load_model():
        model = SlowFakeEOT()
        fake_models.append(model)
        return model

    monkeypatch.setattr(module, "_load_local_eot_model", fake_load_model)
    monkeypatch.setattr(module, "_local_inference_version", lambda: "0.2.5")

    adapter = LiveKitTurnDetectorMiniAdapter(sample_rate=10, audio_sec=0.3)
    batch = [
        {
            "audio": {
                "array": np.asarray([0.0, 0.25, 0.5, 0.75], dtype=np.float32),
                "sampling_rate": 10,
            },
            "messages": [],
        }
        for _ in range(4)
    ]

    scores = adapter.predict_batch(batch)

    assert scores == [0.3, 0.3, 0.3, 0.3]
    assert adapter.num_workers == 2
    assert len(fake_models) == 2
    assert sum(len(model.calls) for model in fake_models) == 4
    adapter.close()
    assert all(model.closed for model in fake_models)


def test_prepare_pcm16_tail_resamples_and_mixes_channels(monkeypatch) -> None:
    captured = {}

    def fake_resample_audio(array, orig_sr, new_sr):
        captured["array"] = array.copy()
        captured["orig_sr"] = orig_sr
        captured["new_sr"] = new_sr
        return np.asarray([0.25, -0.25], dtype=np.float32)

    monkeypatch.setattr(module, "resample_audio", fake_resample_audio)

    pcm = module._prepare_pcm16_tail(
        {
            "audio": {
                "array": np.asarray([[1.0, -1.0], [0.5, 0.0]], dtype=np.float32),
                "sampling_rate": 20,
            }
        },
        sample_rate=10,
        max_samples=10,
    )

    np.testing.assert_array_equal(captured["array"], np.asarray([0.0, 0.25], dtype=np.float32))
    assert captured["orig_sr"] == 20
    assert captured["new_sr"] == 10
    np.testing.assert_array_equal(pcm, np.asarray([8191, -8191], dtype=np.int16))
