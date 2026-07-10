#!/usr/bin/env bash
# Run the EoT benchmark against the Gradium streaming STT API and report metrics.
#
# Usage:
#   ./run_gradium.sh                 # default languages: en fr pt es de
#   ./run_gradium.sh en fr           # explicit language list
#   ./run_gradium.sh all             # every benchmark language
#
# The API key is taken from eot_harness/.env_api (GRADIUM_API_KEY) when that
# file exists, otherwise from the environment / eot_harness/.env. Set
# GRADIUM_API_URL to target a non-default server (defaults to
# https://api.gradium.ai/api).

set -euo pipefail
cd "$(dirname "$0")"

if [ "$#" -gt 0 ]; then LANGS=("$@"); else LANGS=(en fr pt es de); fi
# `all` is a single dataset config for prediction; expand it for the metrics stage.
METRIC_LANGS=("${LANGS[@]}")
if [ "${LANGS[*]}" = all ]; then METRIC_LANGS=(ar de en es fr hi id it ja ko nl pt tr zh); fi
CONCURRENCY="${CONCURRENCY:-16}"
OUTPUT_DIR="${OUTPUT_DIR:-output}"
SPAN_SET_ROOT="$OUTPUT_DIR/livekit__eot-bench-data__validation__min_silence_100ms"

if [ -f eot_harness/.env_api ]; then
    GRADIUM_API_KEY=$(uv run python -c "from dotenv import dotenv_values; print(dotenv_values('eot_harness/.env_api').get('GRADIUM_API_KEY') or '')")
    if [ -n "$GRADIUM_API_KEY" ]; then
        export GRADIUM_API_KEY
        echo "[run_gradium] using GRADIUM_API_KEY from eot_harness/.env_api"
    fi
fi

echo "[run_gradium] languages: ${LANGS[*]}; concurrency: $CONCURRENCY"

for lang in "${LANGS[@]}"; do
    echo "[run_gradium] === predict-streaming: $lang ==="
    uv run --with gradium eot-harness predict-streaming \
        --path livekit/eot-bench-data \
        --name "$lang" \
        --split validation \
        --adapter eot_harness.gradium_adapter:GradiumStreamingAdapter \
        --output-dir "$OUTPUT_DIR" \
        --concurrency "$CONCURRENCY" \
        --skip-errors
done

for lang in "${METRIC_LANGS[@]}"; do
    run_dir="$SPAN_SET_ROOT/$lang/gradium_streaming_adapter__default"
    echo "[run_gradium] === compute-metrics: $lang ==="
    uv run eot-harness compute-metrics \
        --predictions "$run_dir/predictions.parquet" \
        --output-dir "$run_dir"
    echo "[run_gradium] === compare-models: $lang ==="
    uv run eot-harness compare-models "$SPAN_SET_ROOT/$lang"
done

echo "[run_gradium] === summary ==="
uv run python - "$SPAN_SET_ROOT" "${METRIC_LANGS[@]}" <<'EOF'
import json
import sys

root, langs = sys.argv[1], sys.argv[2:]
rows = []
for lang in langs:
    summary = json.loads(open(f"{root}/{lang}/gradium_streaming_adapter__default/summary.json").read())
    op5, op10 = summary["operating_points"]["5pct"], summary["operating_points"]["10pct"]
    rows.append((lang, summary["auc"], summary["ap"], op5["mean_latency"], op10["mean_latency"]))

header = f"{'lang':6} {'AUC':>7} {'AP':>7} {'lat@5%':>9} {'lat@10%':>9}"
print(header)
print("-" * len(header))
for lang, auc, ap, lat5, lat10 in rows:
    print(f"{lang:6} {auc:7.3f} {ap:7.3f} {lat5 * 1000:7.0f}ms {lat10 * 1000:7.0f}ms")
if rows:
    n = len(rows)
    print("-" * len(header))
    print(
        f"{'macro':6} {sum(r[1] for r in rows) / n:7.3f} {sum(r[2] for r in rows) / n:7.3f} "
        f"{sum(r[3] for r in rows) / n * 1000:7.0f}ms {sum(r[4] for r in rows) / n * 1000:7.0f}ms"
    )
print(f"\nPer-language reports: {root}/<lang>/comparison/report.md")
EOF
