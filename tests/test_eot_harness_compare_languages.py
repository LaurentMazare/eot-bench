from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pandas as pd

from eot_harness.cli import _run_compare_languages


def _write_model_metrics(
    span_set_root: Path,
    language: str,
    run_name: str,
    *,
    lat_at_5pct: float,
    lat_at_10pct: float,
    display_name: str,
) -> None:
    span_set_dir = span_set_root / language
    span_set_dir.mkdir(parents=True, exist_ok=True)
    (span_set_dir / "span_set_manifest.json").write_text(json.dumps({"dataset": {"language": language}}))

    run_dir = span_set_dir / run_name
    metrics_dir = run_dir / "metrics"
    metrics_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"display_name": display_name}), encoding="utf-8")
    (metrics_dir / "summary.json").write_text(
        json.dumps(
            {
                "min_hold_span_duration": 0.2,
                "max_hold_span_duration": 5.0,
                "n_spans": 10,
            },
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "policy_type": "model",
                "threshold": 0.5,
                "action_delay": 0.3,
                "timeout": 1.0,
                "cutoff_rate": 0.08,
                "mean_latency": lat_at_10pct,
                "detect_rate": 0.9,
            },
            {
                "policy_type": "model",
                "threshold": 0.6,
                "action_delay": 0.5,
                "timeout": 1.0,
                "cutoff_rate": 0.04,
                "mean_latency": lat_at_5pct,
                "detect_rate": 0.8,
            },
        ],
    ).to_parquet(metrics_dir / "tradeoff.parquet", index=False)


def test_compare_languages_writes_heatmap_and_summary(tmp_path: Path) -> None:
    span_set_root = tmp_path / "livekit__eot-bench-data__validation__min_silence_100ms"
    _write_model_metrics(span_set_root, "de", "alpha_adapter__1111111111", lat_at_5pct=0.50, lat_at_10pct=0.30, display_name="Alpha")
    _write_model_metrics(span_set_root, "de", "beta_adapter__2222222222", lat_at_5pct=0.70, lat_at_10pct=0.40, display_name="Beta")
    _write_model_metrics(span_set_root, "en", "alpha_adapter__1111111111", lat_at_5pct=0.45, lat_at_10pct=0.25, display_name="Alpha")
    _write_model_metrics(span_set_root, "en", "beta_adapter__2222222222", lat_at_5pct=0.60, lat_at_10pct=0.35, display_name="Beta")
    stale_output_dir = span_set_root / "language_comparison"
    stale_output_dir.mkdir()
    (stale_output_dir / "report.html").write_text("old report", encoding="utf-8")

    output_dir = _run_compare_languages(
        Namespace(
            span_set_root=str(span_set_root),
            output_dir=None,
            min_hold_span_duration=0.2,
            max_hold_span_duration=5.0,
        ),
    )

    assert output_dir == span_set_root / "language_comparison"
    expected_heatmaps = {
        "heatmap_best_cutoff_rate_at_0_3s_latency.png",
        "heatmap_best_cutoff_rate_at_0_6s_latency.png",
        "heatmap_best_mean_latency_at_5pct_cutoff.png",
        "heatmap_best_mean_latency_at_10pct_cutoff.png",
    }
    for filename in expected_heatmaps:
        assert (output_dir / filename).exists()
    assert (output_dir / "metrics.parquet").exists()
    assert (output_dir / "report.md").exists()
    assert not (output_dir / "report.html").exists()
    metrics_df = pd.read_parquet(output_dir / "metrics.parquet")
    assert set(metrics_df.columns) == {
        "language",
        "model",
        "best_cutoff_rate_at_0_3s_latency",
        "best_cutoff_rate_at_0_6s_latency",
        "best_mean_latency_at_5pct_cutoff",
        "best_mean_latency_at_10pct_cutoff",
        "run_dir",
        "metrics_dir",
        "n_spans",
    }
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["metrics"] == [
        "best_cutoff_rate_at_0_3s_latency",
        "best_cutoff_rate_at_0_6s_latency",
        "best_mean_latency_at_5pct_cutoff",
        "best_mean_latency_at_10pct_cutoff",
    ]
    assert summary["languages"] == ["de", "en"]
    assert summary["models"] == ["Alpha", "Beta", "VAD baseline"]
    assert summary["metric_titles"]["best_cutoff_rate_at_0_3s_latency"] == "Best cutoff rate @ 0.3s latency"
    assert summary["values"]["best_cutoff_rate_at_0_3s_latency"]["de"]["Alpha"] == 0.08
    assert summary["values"]["best_cutoff_rate_at_0_6s_latency"]["en"]["Alpha"] == 0.04
    assert summary["values"]["best_mean_latency_at_5pct_cutoff"]["de"]["Beta"] == 0.70
    assert summary["values"]["best_mean_latency_at_10pct_cutoff"]["en"]["Beta"] == 0.35
    assert summary["artifacts"]["heatmaps"] == {
        "best_cutoff_rate_at_0_3s_latency": "heatmap_best_cutoff_rate_at_0_3s_latency.png",
        "best_cutoff_rate_at_0_6s_latency": "heatmap_best_cutoff_rate_at_0_6s_latency.png",
        "best_mean_latency_at_5pct_cutoff": "heatmap_best_mean_latency_at_5pct_cutoff.png",
        "best_mean_latency_at_10pct_cutoff": "heatmap_best_mean_latency_at_10pct_cutoff.png",
    }
    report_markdown = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "## Best cutoff rate @ 0.3s latency" in report_markdown
    assert "## Best cutoff rate @ 0.6s latency" in report_markdown
    assert "## Best mean latency @ 5% cutoff" in report_markdown
    assert "## Best mean latency @ 10% cutoff" in report_markdown
    assert "AUC" not in report_markdown
