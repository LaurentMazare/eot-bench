from __future__ import annotations

import json
from argparse import Namespace

import pandas as pd
import pytest

from eot_harness.cli import _run_compute_metrics
from eot_harness.metrics import compute_metrics_from_predictions, max_score_table, official_score_table


def _predictions_df() -> pd.DataFrame:
    rows = []

    for span_index in range(10):
        span_id = f"hold-{span_index}"
        score = 0.6 if span_index == 0 else 0.1
        rows.extend(
            [
                {
                    "id": span_id,
                    "span_index": 0,
                    "timestamp": 0.0,
                    "silence_dur": 0.0,
                    "p_eot": 0.0,
                    "label": "hold",
                },
                {
                    "id": span_id,
                    "span_index": 0,
                    "timestamp": 0.5,
                    "silence_dur": 0.5,
                    "p_eot": score,
                    "label": "hold",
                },
                {
                    "id": span_id,
                    "span_index": 0,
                    "timestamp": 1.0,
                    "silence_dur": 1.0,
                    "p_eot": score,
                    "label": "hold",
                },
            ],
        )

    rows.extend(
        [
            {
                "id": "eot-fast",
                "span_index": 0,
                "timestamp": 0.0,
                "silence_dur": 0.0,
                "p_eot": 0.0,
                "label": "eot",
            },
            {
                "id": "eot-fast",
                "span_index": 0,
                "timestamp": 0.5,
                "silence_dur": 0.5,
                "p_eot": 0.7,
                "label": "eot",
            },
            {
                "id": "eot-fast",
                "span_index": 0,
                "timestamp": 1.0,
                "silence_dur": 1.0,
                "p_eot": 0.7,
                "label": "eot",
            },
            {
                "id": "eot-slow",
                "span_index": 0,
                "timestamp": 0.0,
                "silence_dur": 0.0,
                "p_eot": 0.0,
                "label": "eot",
            },
            {
                "id": "eot-slow",
                "span_index": 0,
                "timestamp": 0.5,
                "silence_dur": 0.5,
                "p_eot": 0.4,
                "label": "eot",
            },
            {
                "id": "eot-slow",
                "span_index": 0,
                "timestamp": 1.0,
                "silence_dur": 1.0,
                "p_eot": 0.8,
                "label": "eot",
            },
        ],
    )
    return pd.DataFrame(rows)


def test_compute_metrics_from_predictions_returns_expected_operating_points() -> None:
    tradeoff, summary = compute_metrics_from_predictions(
        _predictions_df(),
        score_point_s=0.5,
        delay_values=[0.5, 1.5],
    )

    row_070 = tradeoff.loc[
        (tradeoff["policy_type"] == "model")
        & (tradeoff["threshold"] == 0.7)
        & (tradeoff["action_delay"] == 0.5)
        & (tradeoff["timeout"] == 1.5)
    ].iloc[0]
    assert row_070["cutoff_rate"] == 0.0
    assert row_070["mean_latency"] == 1.5
    assert row_070["timeout_rate"] == 1.0

    row_060 = tradeoff.loc[
        (tradeoff["policy_type"] == "model")
        & (tradeoff["threshold"] == 0.6)
        & (tradeoff["action_delay"] == 0.5)
        & (tradeoff["timeout"] == 1.5)
    ].iloc[0]
    assert row_060["cutoff_rate"] == 0.0
    assert row_060["mean_latency"] == 1.0
    assert row_060["timeout_rate"] == 0.5

    vad_050 = tradeoff.loc[
        (tradeoff["policy_type"] == "vad")
        & (tradeoff["action_delay"] == 0.5)
        & (tradeoff["timeout"] == 0.5)
    ].iloc[0]
    assert vad_050["cutoff_rate"] == 1.0
    assert vad_050["mean_latency"] == 0.5

    op_2 = summary["operating_points"]["2pct"]
    op_5 = summary["operating_points"]["5pct"]
    op_10 = summary["operating_points"]["10pct"]
    assert op_2["mean_latency"] == 1.0
    assert op_5["mean_latency"] == 1.0
    assert op_10["mean_latency"] == 0.5
    assert op_2["cutoff_rate"] == 0.0
    assert op_2["action_delay"] == 0.5
    assert op_2["timeout"] == 1.5
    assert summary["score_point_s"] == 0.5
    assert summary["n_spans"] == 12
    assert summary["n_hold_spans"] == 10
    assert summary["n_eot_spans"] == 2
    assert summary["auc"] > 0.9
    assert summary["ap"] > 0.8
    assert summary["sweep_action_delays"] == [0.5, 1.5]
    assert summary["sweep_timeouts"] == [0.5, 1.5]


def test_run_compute_metrics_writes_expected_artifacts(tmp_path) -> None:
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir()
    pred_path = pred_dir / "predictions.parquet"
    _predictions_df().to_parquet(pred_path, index=False)
    (pred_dir / "manifest.json").write_text(
        json.dumps(
            {
                "adapter_id": "stub-adapter",
                "repo_id": "livekit/eot-bench-data",
                "subset": "panels_eval_small",
            },
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "metrics"
    output_dir.mkdir()
    (output_dir / "report.html").write_text("old report", encoding="utf-8")
    _run_compute_metrics(
        Namespace(
            predictions=str(pred_path),
            output_dir=str(output_dir),
            score_point=0.5,
            min_hold_span_duration=0.2,
            max_hold_span_duration=5.0,
        ),
    )

    assert (output_dir / "tradeoff.parquet").exists()
    assert (output_dir / "summary.json").exists()
    assert not (output_dir / "report.html").exists()

    summary = json.loads((output_dir / "summary.json").read_text())
    assert summary["score_point_s"] == 0.5
    assert summary["operating_points"]["5pct"]["cutoff_rate"] <= 0.05
    tradeoff = pd.read_parquet(output_dir / "tradeoff.parquet")
    assert {"policy_type", "is_pareto", "action_delay", "timeout"} <= set(tradeoff.columns)


def test_run_compute_metrics_uses_manifest_score_point_when_cli_omitted(tmp_path) -> None:
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir()
    pred_path = pred_dir / "predictions.parquet"
    _predictions_df().to_parquet(pred_path, index=False)
    (pred_dir / "manifest.json").write_text(json.dumps({"adapter_id": "stub-adapter", "score_point": 1.0}), encoding="utf-8")

    output_dir = tmp_path / "metrics"
    _run_compute_metrics(
        Namespace(
            predictions=str(pred_path),
            output_dir=str(output_dir),
            score_point=None,
            min_hold_span_duration=0.2,
            max_hold_span_duration=5.0,
        ),
    )

    summary = json.loads((output_dir / "summary.json").read_text())
    assert summary["score_point_s"] == 1.0


def test_run_compute_metrics_uses_max_scores_without_score_point(tmp_path) -> None:
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir()
    pred_path = pred_dir / "predictions.parquet"
    _predictions_df().to_parquet(pred_path, index=False)

    output_dir = tmp_path / "metrics"
    _run_compute_metrics(
        Namespace(
            predictions=str(pred_path),
            output_dir=str(output_dir),
            score_point=None,
            min_hold_span_duration=0.2,
            max_hold_span_duration=5.0,
        ),
    )

    summary = json.loads((output_dir / "summary.json").read_text())
    assert summary["score_mode"] == "max"
    assert summary["score_point_s"] is None


def test_delay_discards_early_evidence() -> None:
    df = pd.DataFrame(
        [
            {"id": "hold-early", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.9, "label": "hold"},
            {"id": "hold-early", "span_index": 0, "timestamp": 0.5, "silence_dur": 0.5, "p_eot": 0.1, "label": "hold"},
            {"id": "hold-early", "span_index": 0, "timestamp": 1.0, "silence_dur": 1.0, "p_eot": 0.1, "label": "hold"},
            {"id": "eot-early", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.9, "label": "eot"},
            {"id": "eot-early", "span_index": 0, "timestamp": 0.5, "silence_dur": 0.5, "p_eot": 0.1, "label": "eot"},
            {"id": "eot-early", "span_index": 0, "timestamp": 1.0, "silence_dur": 1.0, "p_eot": 0.1, "label": "eot"},
        ],
    )

    tradeoff, summary = compute_metrics_from_predictions(
        df,
        score_point_s=0.5,
        thresholds=[0.9],
        delay_values=[0.5, 1.5],
    )
    row = tradeoff.loc[
        (tradeoff["policy_type"] == "model")
        & (tradeoff["threshold"] == 0.9)
        & (tradeoff["action_delay"] == 0.5)
        & (tradeoff["timeout"] == 1.5)
    ].iloc[0]

    assert row["cutoff_rate"] == 0.0
    assert row["mean_latency"] == 1.5
    assert row["timeout_rate"] == 1.0
    assert summary["auc"] == 0.5
    assert summary["ap"] == 0.5


def test_max_score_mode_uses_first_threshold_crossing_for_policy() -> None:
    df = pd.DataFrame(
        [
            {"id": "hold-short", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "hold"},
            {"id": "hold-short", "span_index": 0, "timestamp": 0.2, "silence_dur": 0.2, "p_eot": 0.9, "label": "hold"},
            {"id": "hold-long", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "hold"},
            {"id": "hold-long", "span_index": 0, "timestamp": 0.3, "silence_dur": 0.3, "p_eot": 0.8, "label": "hold"},
            {"id": "hold-long", "span_index": 0, "timestamp": 1.0, "silence_dur": 1.0, "p_eot": 0.8, "label": "hold"},
            {"id": "eot", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "eot"},
            {"id": "eot", "span_index": 0, "timestamp": 0.4, "silence_dur": 0.4, "p_eot": 0.95, "label": "eot"},
            {"id": "eot", "span_index": 0, "timestamp": 1.0, "silence_dur": 1.0, "p_eot": 0.95, "label": "eot"},
        ],
    )

    scored = max_score_table(df)
    assert scored.set_index("id").loc["eot", "p_eot"] == 0.95

    tradeoff, summary = compute_metrics_from_predictions(
        df,
        score_point_s=None,
        thresholds=[0.7],
        action_delays=[0.5],
        timeouts=[1.0],
    )
    row = tradeoff.loc[tradeoff["policy_type"] == "model"].iloc[0]

    assert row["score_mode"] == "max"
    assert pd.isna(row["score_point_s"])
    assert row["cutoff_rate"] == 0.5
    assert row["mean_latency"] == 0.5
    assert row["detect_rate"] == 1.0
    assert summary["score_mode"] == "max"
    assert summary["score_point_s"] is None


def test_score_point_is_decoupled_from_action_delay() -> None:
    df = pd.DataFrame(
        [
            {"id": "hold", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "hold"},
            {"id": "hold", "span_index": 0, "timestamp": 0.2, "silence_dur": 0.2, "p_eot": 0.8, "label": "hold"},
            {"id": "hold", "span_index": 0, "timestamp": 0.5, "silence_dur": 0.5, "p_eot": 0.1, "label": "hold"},
            {"id": "hold", "span_index": 0, "timestamp": 1.0, "silence_dur": 1.0, "p_eot": 0.1, "label": "hold"},
            {"id": "eot", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "eot"},
            {"id": "eot", "span_index": 0, "timestamp": 0.2, "silence_dur": 0.2, "p_eot": 0.8, "label": "eot"},
            {"id": "eot", "span_index": 0, "timestamp": 0.5, "silence_dur": 0.5, "p_eot": 0.1, "label": "eot"},
            {"id": "eot", "span_index": 0, "timestamp": 1.0, "silence_dur": 1.0, "p_eot": 0.1, "label": "eot"},
        ],
    )

    tradeoff, summary = compute_metrics_from_predictions(
        df,
        score_point_s=0.2,
        thresholds=[0.7],
        action_delays=[0.5],
        timeouts=[1.5],
    )
    row = tradeoff.loc[tradeoff["policy_type"] == "model"].iloc[0]

    assert row["score_point_s"] == 0.2
    assert row["action_delay"] == 0.5
    assert row["cutoff_rate"] == 1.0
    assert row["mean_latency"] == 0.5
    assert row["detect_rate"] == 1.0
    assert summary["auc"] == 0.5


def test_action_delay_cannot_be_below_min_hold_span_duration() -> None:
    with pytest.raises(ValueError, match="min_hold_span_duration"):
        compute_metrics_from_predictions(
            _predictions_df(),
            score_point_s=None,
            action_delays=[0.1],
            timeouts=[1.0],
            min_hold_span_duration=0.2,
        )


def test_missing_official_score_rules_are_strict() -> None:
    df = pd.DataFrame(
        [
            {"id": "short-hold", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "hold"},
            {"id": "short-hold", "span_index": 0, "timestamp": 0.1, "silence_dur": 0.1, "p_eot": 0.9, "label": "hold"},
            {"id": "hold", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "hold"},
            {"id": "hold", "span_index": 0, "timestamp": 0.2, "silence_dur": 0.2, "p_eot": 0.1, "label": "hold"},
            {"id": "eot", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "eot"},
            {"id": "eot", "span_index": 0, "timestamp": 0.2, "silence_dur": 0.2, "p_eot": 0.8, "label": "eot"},
        ],
    )

    scored = official_score_table(df, score_point_s=0.2)
    assert set(scored["id"]) == {"hold", "eot"}

    missing_eot = df[df["id"] != "eot"].copy()
    missing_eot = pd.concat(
        [
            missing_eot,
            pd.DataFrame(
                [
                    {"id": "eot", "span_index": 0, "timestamp": 0.0, "silence_dur": 0.0, "p_eot": 0.0, "label": "eot"},
                    {"id": "eot", "span_index": 0, "timestamp": 0.1, "silence_dur": 0.1, "p_eot": 0.8, "label": "eot"},
                ],
            ),
        ],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="missing official score for true EoT"):
        official_score_table(missing_eot, score_point_s=0.2)
