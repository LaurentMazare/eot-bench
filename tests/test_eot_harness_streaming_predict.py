from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pandas as pd
from datasets import Dataset

from eot_harness.cli import _run_predict_streaming


class StubStreamingAdapter:
    adapter_id = "stub-streaming-adapter"
    display_name = "Stub Streaming Adapter"
    score_point = 0.2
    concurrency = 2

    async def predict_turn(self, row, *, inference_interval):
        return {
            "id": row["id"],
            "audio_sec": 1.25,
            "events": [
                {
                    "event": "Update",
                    "audio_window_end": 1.0,
                    "end_of_turn_confidence": 0.25,
                },
            ],
            "prediction_rows": [
                {
                    "id": row["id"],
                    "span_index": 0,
                    "timestamp": 1.0,
                    "silence_dur": 0.0,
                    "p_eot": 0.25,
                    "label": "hold",
                },
                {
                    "id": row["id"],
                    "span_index": 1,
                    "timestamp": 2.0,
                    "silence_dur": 0.0,
                    "p_eot": 0.75,
                    "label": "eot",
                },
            ],
        }


class ErrorStreamingAdapter(StubStreamingAdapter):
    adapter_id = "error-streaming-adapter"

    async def predict_turn(self, row, *, inference_interval):
        if row["id"] == "bad-turn":
            raise RuntimeError("provider failed")
        return await super().predict_turn(row, inference_interval=inference_interval)


class EnglishOnlyStreamingAdapter(StubStreamingAdapter):
    adapter_id = "english-only-streaming-adapter"

    def supports_language(self, lang_code):
        return str(lang_code).strip().lower() == "en"


def _row(row_id: str):
    return {
        "id": row_id,
        "language": "en",
        "audio": {
            "array": [0.0] * 100,
            "sampling_rate": 10,
        },
        "silence_spans": [
            {"start": 1.0, "end": 1.2},
            {"start": 2.0, "end": 2.2},
        ],
        "messages": [],
        "words": [],
    }


def _row_with_language(row_id: str, language: str):
    row = _row(row_id)
    row["language"] = language
    return row


def test_run_predict_streaming_writes_predictions_events_and_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "eot_harness.cli.load_hf_dataset",
        lambda *args, **kwargs: Dataset.from_list([_row("turn-1"), _row("turn-2")]),
    )

    args = Namespace(
        repo_id="livekit/eot-bench-data",
        subset="panels_eval_small",
        split="validation",
        adapter="tests.test_eot_harness_streaming_predict:StubStreamingAdapter",
        output_dir=str(tmp_path),
        inference_interval=0.1,
        concurrency=None,
        model=None,
        chunk_ms=None,
        eot_threshold=None,
        skip_unsupported_languages=False,
        skip_errors=False,
    )

    run_dirs = _run_predict_streaming(args)
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    predictions_path = run_dir / "predictions.parquet"
    events_path = run_dir / "events.parquet"
    manifest_path = run_dir / "manifest.json"
    summary_path = run_dir / "summary.json"

    assert run_dir.parent.name == "en"
    assert predictions_path.exists()
    assert events_path.exists()
    assert manifest_path.exists()
    assert summary_path.exists()

    predictions_df = pd.read_parquet(predictions_path)
    assert list(predictions_df.columns) == ["id", "language", "span_index", "timestamp", "silence_dur", "p_eot", "label"]
    assert predictions_df["id"].tolist() == ["turn-1", "turn-1", "turn-2", "turn-2"]
    assert predictions_df["language"].tolist() == ["en", "en", "en", "en"]

    events_df = pd.read_parquet(events_path)
    assert events_df["id"].tolist() == ["turn-1", "turn-2"]
    assert events_df["end_of_turn_confidence"].tolist() == [0.25, 0.25]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["adapter_id"] == "stub-streaming-adapter"
    assert manifest["display_name"] == "Stub Streaming Adapter"
    assert manifest["score_point"] == 0.2
    assert manifest["streaming"] is True
    assert manifest["skip_errors"] is False
    assert manifest["concurrency"] == 2
    assert manifest["n_dataset_rows"] == 2
    assert manifest["n_scored_turns"] == 2
    assert manifest["n_skipped_turns"] == 0
    assert manifest["n_rows"] == 4
    assert manifest["n_event_rows"] == 2


def test_run_predict_streaming_can_skip_adapter_errors(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "eot_harness.cli.load_hf_dataset",
        lambda *args, **kwargs: Dataset.from_list([_row("good-turn"), _row("bad-turn")]),
    )

    run_dirs = _run_predict_streaming(
        Namespace(
            repo_id="livekit/eot-bench-data",
            subset="panels_eval_small",
            split="validation",
            adapter="tests.test_eot_harness_streaming_predict:ErrorStreamingAdapter",
            output_dir=str(tmp_path),
            inference_interval=0.1,
            concurrency=None,
            model=None,
            chunk_ms=None,
            eot_threshold=None,
            skip_unsupported_languages=False,
            skip_errors=True,
        ),
    )
    run_dir = run_dirs[0]

    predictions_df = pd.read_parquet(run_dir / "predictions.parquet")
    skipped_df = pd.read_parquet(run_dir / "skipped.parquet")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert predictions_df["id"].tolist() == ["good-turn", "good-turn"]
    assert skipped_df["id"].tolist() == ["bad-turn"]
    assert skipped_df["reason"].str.contains("provider failed").tolist() == [True]
    assert manifest["skip_errors"] is True
    assert manifest["n_scored_turns"] == 1
    assert manifest["n_skipped_turns"] == 1


def test_run_predict_streaming_skips_existing_language_artifact(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "eot_harness.cli.load_hf_dataset",
        lambda *args, **kwargs: Dataset.from_list([_row("turn-1")]),
    )

    args = Namespace(
        repo_id="livekit/eot-bench-data",
        subset="panels_eval_small",
        split="validation",
        adapter="tests.test_eot_harness_streaming_predict:StubStreamingAdapter",
        output_dir=str(tmp_path),
        inference_interval=0.1,
        concurrency=None,
        model=None,
        chunk_ms=None,
        eot_threshold=None,
        skip_unsupported_languages=False,
        skip_errors=False,
        overwrite=False,
    )

    assert _run_predict_streaming(args)
    assert _run_predict_streaming(args) == []


def test_run_predict_streaming_skips_unsupported_languages_before_api_call(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "eot_harness.cli.load_hf_dataset",
        lambda *args, **kwargs: Dataset.from_list(
            [_row_with_language("turn-en", "en"), _row_with_language("turn-de", "de")],
        ),
    )

    run_dirs = _run_predict_streaming(
        Namespace(
            repo_id="livekit/eot-bench-data",
            subset="all",
            split="validation",
            adapter="tests.test_eot_harness_streaming_predict:EnglishOnlyStreamingAdapter",
            output_dir=str(tmp_path),
            inference_interval=0.1,
            concurrency=None,
            model=None,
            chunk_ms=None,
            eot_threshold=None,
            skip_unsupported_languages=False,
            skip_errors=False,
        ),
    )
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    predictions_df = pd.read_parquet(run_dir / "predictions.parquet")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert predictions_df["id"].tolist() == ["turn-en", "turn-en"]
    assert not (run_dir / "skipped.parquet").exists()
    assert manifest["skipped_languages"] == {"de": "unsupported_language"}
    assert manifest["n_dataset_rows"] == 1
    assert manifest["n_scored_turns"] == 1
    assert manifest["n_skipped_turns"] == 0
