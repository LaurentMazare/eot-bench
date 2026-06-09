from __future__ import annotations

import numpy as np
import pytest

from eot_harness.ultravad_adapter import UltraVADAdapter, ultravad_output_position


class FakeTensor:
    def __init__(self, values, *, shape=None):
        self._values = list(values)
        self.shape = shape or (len(self._values),)

    def numel(self):
        return len(self._values)

    def item(self):
        if len(self._values) != 1:
            raise RuntimeError("item() requires exactly one element")
        return self._values[0]

    def __getitem__(self, idx):
        return FakeTensor([self._values[idx]])

    def to(self, device):
        self.device = device
        return self

    def float(self):
        return self


class FakeLogits:
    def __init__(self, shape):
        self.shape = shape


class _FakeScalar:
    def __init__(self, value):
        self.value = float(value)

    def item(self):
        return self.value


class _FakeTensor1D:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=np.float32)

    def float(self):
        return self

    def __getitem__(self, idx):
        return _FakeScalar(self.values[idx])


class _FakeLogits3D:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=np.float32)
        self.shape = self.values.shape

    def __getitem__(self, idx):
        return _FakeTensor1D(self.values[idx])


class _FakeTorch:
    class _Context:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    def inference_mode(self):
        return self._Context()

    def softmax(self, logits, dim=-1):
        del dim
        values = np.asarray(logits.values, dtype=np.float32)
        shifted = values - values.max()
        probs = np.exp(shifted) / np.exp(shifted).sum()
        return _FakeTensor1D(probs)


class _FakeParameter:
    device = "cpu"


class _FakeModel:
    def parameters(self):
        yield _FakeParameter()

    def eval(self):
        return self

    def forward(self, **kwargs):
        del kwargs["return_dict"]
        logits = _FakeLogits3D([[[0.0, 1.0, 3.0]]])
        return type("Output", (), {"logits": logits})()


class _FakeTokenizer:
    unk_token_id = -1

    def convert_tokens_to_ids(self, token):
        if token == "<|eot_id|>":
            return 2
        return self.unk_token_id


class _FakePipe:
    def __init__(self):
        self.model = _FakeModel()
        self.tokenizer = _FakeTokenizer()
        self.last_item = None

    def preprocess(self, item):
        self.last_item = item
        return {
            "audio_token_start_idx": FakeTensor([0]),
            "audio_token_len": FakeTensor([1]),
            "input_values": FakeTensor([0.0]),
        }


def test_ultravad_output_position_uses_reference_formula_for_single_chunk():
    model_inputs = {
        "audio_token_start_idx": FakeTensor([10]),
        "audio_token_len": FakeTensor([3]),
    }
    assert ultravad_output_position(model_inputs, FakeLogits((1, 32, 100))) == (0, 12)


def test_ultravad_output_position_uses_last_chunk_for_chunked_inputs():
    model_inputs = {
        "audio_token_start_idx": FakeTensor([10, 20]),
        "audio_token_len": FakeTensor([3, 4]),
    }
    assert ultravad_output_position(model_inputs, FakeLogits((2, 64, 100))) == (1, 23)


def test_ultravad_output_position_uses_last_audio_span_when_logits_are_single_sequence():
    model_inputs = {
        "audio_token_start_idx": FakeTensor([10, 20]),
        "audio_token_len": FakeTensor([3, 4]),
    }
    assert ultravad_output_position(model_inputs, FakeLogits((1, 64, 100))) == (0, 23)


def test_ultravad_output_position_rejects_mismatched_chunk_metadata():
    model_inputs = {
        "audio_token_start_idx": FakeTensor([10, 20]),
        "audio_token_len": FakeTensor([3]),
    }
    with pytest.raises(ValueError, match="Unexpected UltraVAD token position field shapes|shape does not match"):
        ultravad_output_position(model_inputs, FakeLogits((2, 64, 100)))


def test_ultravad_adapter_rejects_batched_predict(monkeypatch):
    monkeypatch.setattr("eot_harness.ultravad_adapter._load_ultravad_pipeline", lambda **_: _FakePipe())
    adapter = UltraVADAdapter()
    assert adapter.display_name == "ultraVAD"
    assert adapter.score_point == 0.2
    with pytest.raises(ValueError, match="batch_size=1"):
        adapter.predict_batch(
            [
                {"audio": {"array": np.zeros(1600, dtype=np.float32), "sampling_rate": 16000}, "messages": []},
                {"audio": {"array": np.zeros(1600, dtype=np.float32), "sampling_rate": 16000}, "messages": []},
            ],
        )


def test_ultravad_adapter_scores_eot_probability(monkeypatch):
    fake_pipe = _FakePipe()
    monkeypatch.setattr("eot_harness.ultravad_adapter._import_torch", lambda: _FakeTorch())
    monkeypatch.setattr("eot_harness.ultravad_adapter._load_ultravad_pipeline", lambda **_: fake_pipe)
    adapter = UltraVADAdapter()

    scores = adapter.predict_batch(
        [
            {
                "audio": {"array": np.zeros(1600, dtype=np.float32), "sampling_rate": 16000},
                "messages": [
                    {"role": "assistant", "content": "older"},
                    {"role": "user", "content": "ignored"},
                    {"role": "assistant", "content": "latest assistant"},
                ],
            },
        ],
    )

    assert len(scores) == 1
    assert 0.0 < scores[0] < 1.0
    assert scores[0] > 0.8
    assert fake_pipe.last_item is not None
    assert fake_pipe.last_item["turns"] == [{"role": "assistant", "content": "latest assistant"}]
