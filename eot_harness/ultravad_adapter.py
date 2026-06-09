from __future__ import annotations

import numpy as np

from .languages import supports_any_benchmark_language


DEFAULT_ULTRAVAD_MODEL_ID = "fixie-ai/ultraVAD"


class UltraVADAdapter:
    display_name = "ultraVAD"
    score_point = 0.2

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_ULTRAVAD_MODEL_ID,
        revision: str | None = None,
        sample_rate: int = 16000,
    ) -> None:
        self.model_id = model_id
        self.revision = revision
        self.sample_rate = int(sample_rate)
        self.adapter_id = f"{model_id}-{revision}" if revision else model_id

        self.pipe = _load_ultravad_pipeline(model_id=model_id, revision=revision)
        self.model = self.pipe.model.eval()
        self.model_device = next(self.model.parameters()).device

        eot_token_id = self.pipe.tokenizer.convert_tokens_to_ids("<|eot_id|>")
        if eot_token_id is None or eot_token_id == self.pipe.tokenizer.unk_token_id:
            raise RuntimeError("<|eot_id|> not found in UltraVAD tokenizer")
        self.eot_token_id = int(eot_token_id)

    def supports_language(self, lang_code: str) -> bool:
        return supports_any_benchmark_language(lang_code)

    def predict_batch(self, batch):
        if len(batch) != 1:
            raise ValueError("UltraVAD adapter supports batch_size=1 only.")

        item = batch[0]
        audio = item.get("audio") or {}
        arr = np.asarray(audio["array"], dtype=np.float32)
        sampling_rate = int(audio["sampling_rate"])
        if arr.ndim != 1:
            raise ValueError(f"UltraVAD adapter expects mono audio, got shape={arr.shape}")
        if sampling_rate != self.sample_rate:
            arr = _resample_audio(arr, orig_freq=sampling_rate, new_freq=self.sample_rate)

        model_inputs = self.pipe.preprocess(
            {
                "audio": arr,
                "turns": _assistant_turns(item.get("messages") or []),
                "sampling_rate": self.sample_rate,
            },
        )
        model_inputs = {
            key: (value.to(self.model_device) if hasattr(value, "to") else value)
            for key, value in model_inputs.items()
        }

        torch = _import_torch()
        with torch.inference_mode():
            output = self.model.forward(**model_inputs, return_dict=True)

        logits = output.logits
        batch_idx, audio_pos = ultravad_output_position(model_inputs, logits)
        audio_logits = logits[batch_idx, audio_pos, :]
        audio_probs = torch.softmax(audio_logits.float(), dim=-1)
        prob = float(audio_probs[self.eot_token_id].item())
        return [prob]


def ultravad_output_position(model_inputs: dict, logits) -> tuple[int, int]:
    start_idx = model_inputs.get("audio_token_start_idx")
    token_len = model_inputs.get("audio_token_len")
    if start_idx is None or token_len is None:
        raise ValueError("UltraVAD preprocess output must include audio_token_start_idx and audio_token_len.")
    if not hasattr(start_idx, "numel") or not hasattr(token_len, "numel"):
        raise TypeError("UltraVAD token position fields must be tensors.")
    if not hasattr(logits, "shape") or len(logits.shape) < 2:
        raise TypeError("UltraVAD logits must be a batched tensor.")

    if start_idx.numel() == 1 and token_len.numel() == 1:
        if int(logits.shape[0]) != 1:
            raise ValueError(
                "UltraVAD returned scalar token position fields but batched logits. "
                f"logits shape={tuple(logits.shape)}.",
            )
        token_idx = int(start_idx.item() + token_len.item() - 1)
        return 0, token_idx

    if len(start_idx.shape) == 1 and len(token_len.shape) == 1 and tuple(start_idx.shape) == tuple(token_len.shape):
        batch_size = int(start_idx.shape[0])
        if int(logits.shape[0]) == batch_size:
            batch_idx = batch_size - 1
            token_idx = int(start_idx[batch_idx].item() + token_len[batch_idx].item() - 1)
            return batch_idx, token_idx
        if int(logits.shape[0]) == 1:
            token_idx = max(
                int(start_idx[idx].item() + token_len[idx].item() - 1)
                for idx in range(batch_size)
            )
            return 0, token_idx
        raise ValueError(
            "UltraVAD chunk metadata shape does not match logits batch dimension. "
            f"audio_token_start_idx shape={tuple(start_idx.shape)} "
            f"audio_token_len shape={tuple(token_len.shape)} "
            f"logits shape={tuple(logits.shape)}.",
        )

    raise ValueError(
        "Unexpected UltraVAD token position field shapes. "
        f"audio_token_start_idx shape={tuple(start_idx.shape)} "
        f"audio_token_len shape={tuple(token_len.shape)}.",
    )


def _load_ultravad_pipeline(*, model_id: str, revision: str | None):
    import torch
    import transformers

    use_cuda = torch.cuda.is_available()
    kwargs = {
        "model": model_id,
        "trust_remote_code": True,
        "device_map": "cuda" if use_cuda else "cpu",
        "torch_dtype": torch.float16 if use_cuda else torch.float32,
    }
    if revision:
        kwargs["revision"] = revision
    return transformers.pipeline(**kwargs)


def _assistant_turns(messages: list[dict]) -> list[dict[str, str]]:
    for message in reversed(messages):
        if str(message.get("role", "")).strip() == "assistant":
            content = str(message.get("content") or "").strip()
            if content:
                return [{"role": "assistant", "content": content}]
            return []
    return []


def _import_torch():
    import torch

    return torch


def _resample_audio(array: np.ndarray, *, orig_freq: int, new_freq: int) -> np.ndarray:
    torch = _import_torch()
    import torchaudio.functional as F

    return (
        F.resample(
            torch.from_numpy(np.asarray(array, dtype=np.float32)).unsqueeze(0),
            orig_freq=orig_freq,
            new_freq=new_freq,
        )
        .squeeze(0)
        .cpu()
        .numpy()
    )
