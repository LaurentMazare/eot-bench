from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pandas as pd
from datasets import Dataset

from eot_harness.cli import _run_predict


class StubAdapter:
    adapter_id = "stub-adapter"
    display_name = "Stub Adapter"
    score_point = 0.2

    def predict_batch(self, batch):
        return [0.25 + 0.01 * i for i, _ in enumerate(batch)]


class EnglishOnlyStubAdapter(StubAdapter):
    adapter_id = "english-only-stub-adapter"

    def supports_language(self, lang_code):
        return str(lang_code).strip().lower() == "en"


def _row():
    return {
        "id": "turn-1",
        "language": "en",
        "audio": {
            "array": [0.0] * 100,
            "sampling_rate": 10,
        },
        "silence_spans": [
            {"start": 2.0, "end": 3.0, "label": "hold"},
            {"start": 4.0, "end": 5.0, "label": "eot"},
        ],
        "messages": [
            {"role": "assistant", "content": "Hi"},
        ],
        "words": [
            {"word": "hello", "start": 1.1, "end": 1.4},
            {"word": "there", "start": 1.5, "end": 1.9},
        ],
    }


def _minimal_row():
    return {
        "id": "turn-minimal",
        "language": "en",
        "audio": {
            "array": [0.0] * 100,
            "sampling_rate": 10,
        },
        "silence_spans": [
            {"start": 2.0, "end": 3.0},
            {"start": 4.0, "end": 5.0},
        ],
    }


def test_run_predict_writes_predictions_and_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("eot_harness.cli.load_hf_dataset", lambda *args, **kwargs: Dataset.from_list([_row()]))

    args = Namespace(
        path="livekit/eot-bench-data",
        name="all",
        split="validation",
        revision=None,
        adapter="tests.test_eot_harness_predict:StubAdapter",
        output_dir=str(tmp_path),
        min_silence_span=0.1,
        batch_size=3,
        inference_interval=0.5,
        transcript_lag=0.5,
        overwrite=False,
    )

    run_dir = _run_predict(args)[0]

    span_dir = tmp_path / "livekit__eot-bench-data__validation__min_silence_100ms" / "en"
    predictions_path = run_dir / "predictions.parquet"
    manifest_path = run_dir / "manifest.json"

    assert run_dir.parent == span_dir
    assert run_dir.name.startswith("stub_adapter__")
    assert (span_dir / "span_set.parquet").exists()
    assert (span_dir / "span_set_manifest.json").exists()
    assert predictions_path.exists()
    assert manifest_path.exists()

    span_df = pd.read_parquet(span_dir / "span_set.parquet")
    assert span_df[["id", "language", "span_index"]].to_dict("records") == [
        {"id": "turn-1", "language": "en", "span_index": 0},
        {"id": "turn-1", "language": "en", "span_index": 1},
    ]

    df = pd.read_parquet(predictions_path)
    assert list(df.columns) == ["id", "language", "span_index", "timestamp", "silence_dur", "p_eot", "label"]
    assert df["id"].tolist() == ["turn-1"] * 6
    assert df["language"].tolist() == ["en"] * 6
    assert df["span_index"].tolist() == [0, 0, 0, 1, 1, 1]
    assert df["label"].tolist() == ["hold", "hold", "hold", "eot", "eot", "eot"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["adapter_id"] == "stub-adapter"
    assert manifest["repo_id"] == "livekit/eot-bench-data"
    assert manifest["subset"] == "all"
    assert manifest["path"] == "livekit/eot-bench-data"
    assert manifest["name"] == "all"
    assert manifest["split"] == "validation"
    assert "revision" not in manifest
    assert manifest["language"] == "en"
    assert manifest["dataset"] == {
        "path": "livekit/eot-bench-data",
        "split": "validation",
        "min_silence_span": 0.1,
        "language": "en",
    }
    assert manifest["load_dataset"] == {
        "path": "livekit/eot-bench-data",
        "name": "all",
        "split": "validation",
    }
    assert manifest["model"]["adapter"] == "tests.test_eot_harness_predict:StubAdapter"
    assert manifest["model"]["display_name"] == "Stub Adapter"
    assert manifest["display_name"] == "Stub Adapter"
    assert manifest["model"]["score_point"] == 0.2
    assert manifest["score_point"] == 0.2
    assert manifest["runtime"] == {"batch_size": 3}
    assert manifest["n_rows"] == 6
    assert manifest["n_spans"] == 2


def test_run_predict_accepts_minimal_dataset_row_without_text_fields(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("eot_harness.cli.load_hf_dataset", lambda *args, **kwargs: Dataset.from_list([_minimal_row()]))

    args = Namespace(
        path="livekit/eot-bench-data",
        name="en",
        split="validation",
        revision=None,
        adapter="tests.test_eot_harness_predict:StubAdapter",
        output_dir=str(tmp_path),
        min_silence_span=0.1,
        batch_size=3,
        inference_interval=0.5,
        transcript_lag=0.5,
        overwrite=False,
    )

    run_dir = _run_predict(args)[0]

    df = pd.read_parquet(run_dir / "predictions.parquet")
    assert df["id"].tolist() == ["turn-minimal"] * 6
    assert df["label"].tolist() == ["hold", "hold", "hold", "eot", "eot", "eot"]


def test_run_predict_skips_complete_existing_model_run_without_overwrite(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("eot_harness.cli.load_hf_dataset", lambda *args, **kwargs: Dataset.from_list([_row()]))

    args = Namespace(
        path="livekit/eot-bench-data",
        name="en",
        split="validation",
        revision=None,
        adapter="tests.test_eot_harness_predict:StubAdapter",
        output_dir=str(tmp_path),
        min_silence_span=0.1,
        batch_size=3,
        inference_interval=0.5,
        transcript_lag=0.5,
        overwrite=False,
    )

    first_run_dirs = _run_predict(args)
    assert first_run_dirs
    assert _run_predict(args) == []

    args.overwrite = True
    _run_predict(args)


def test_run_predict_requires_overwrite_for_incomplete_existing_model_run(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("eot_harness.cli.load_hf_dataset", lambda *args, **kwargs: Dataset.from_list([_row()]))

    args = Namespace(
        path="livekit/eot-bench-data",
        name="en",
        split="validation",
        revision=None,
        adapter="tests.test_eot_harness_predict:StubAdapter",
        output_dir=str(tmp_path),
        min_silence_span=0.1,
        batch_size=3,
        inference_interval=0.5,
        transcript_lag=0.5,
        overwrite=False,
    )

    run_dir = _run_predict(args)[0]
    (run_dir / "predictions.parquet").unlink()

    try:
        _run_predict(args)
    except FileExistsError as exc:
        assert "incomplete or stale" in str(exc)
    else:
        raise AssertionError("expected FileExistsError")


def test_run_predict_splits_artifacts_by_row_language(tmp_path: Path, monkeypatch):
    de_row = _row()
    de_row["id"] = "turn-de"
    de_row["language"] = "de"
    monkeypatch.setattr(
        "eot_harness.cli.load_hf_dataset",
        lambda *args, **kwargs: Dataset.from_list([_row(), de_row]),
    )

    args = Namespace(
        path="livekit/eot-bench-data",
        name="all",
        split="validation",
        revision=None,
        adapter="tests.test_eot_harness_predict:StubAdapter",
        output_dir=str(tmp_path),
        min_silence_span=0.1,
        batch_size=3,
        inference_interval=0.5,
        transcript_lag=0.5,
        overwrite=False,
    )

    run_dirs = _run_predict(args)

    root_dir = tmp_path / "livekit__eot-bench-data__validation__min_silence_100ms"
    assert [run_dir.parent.name for run_dir in run_dirs] == ["de", "en"]
    assert (root_dir / "de" / "span_set.parquet").exists()
    assert (root_dir / "en" / "span_set.parquet").exists()
    assert pd.read_parquet(root_dir / "de" / "span_set.parquet")["id"].tolist() == ["turn-de", "turn-de"]
    assert pd.read_parquet(root_dir / "en" / "span_set.parquet")["id"].tolist() == ["turn-1", "turn-1"]


def test_run_predict_skips_unsupported_languages(tmp_path: Path, monkeypatch):
    de_row = _row()
    de_row["id"] = "turn-de"
    de_row["language"] = "de"
    monkeypatch.setattr(
        "eot_harness.cli.load_hf_dataset",
        lambda *args, **kwargs: Dataset.from_list([_row(), de_row]),
    )

    args = Namespace(
        path="livekit/eot-bench-data",
        name="all",
        split="validation",
        revision=None,
        adapter="tests.test_eot_harness_predict:EnglishOnlyStubAdapter",
        output_dir=str(tmp_path),
        min_silence_span=0.1,
        batch_size=3,
        inference_interval=0.5,
        transcript_lag=0.5,
        overwrite=False,
    )

    run_dirs = _run_predict(args)

    root_dir = tmp_path / "livekit__eot-bench-data__validation__min_silence_100ms"
    assert [run_dir.parent.name for run_dir in run_dirs] == ["en"]
    assert (root_dir / "de" / "span_set.parquet").exists()
    assert not any((root_dir / "de").glob("english_only_stub_adapter__*"))
