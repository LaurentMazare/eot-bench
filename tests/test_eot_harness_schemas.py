from __future__ import annotations

from eot_harness.schemas import DATASET_OPTIONAL_FIELDS, DATASET_REQUIRED_FIELDS, PREDICTION_REQUIRED_COLUMNS


def test_global_dataset_schema_requires_only_audio_turn_fields() -> None:
    assert DATASET_REQUIRED_FIELDS == ("id", "language", "audio", "silence_spans")
    assert "messages" in DATASET_OPTIONAL_FIELDS
    assert "words" in DATASET_OPTIONAL_FIELDS
    assert "language" in PREDICTION_REQUIRED_COLUMNS
