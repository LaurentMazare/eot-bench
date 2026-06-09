from __future__ import annotations

from typing import Any


BENCHMARK_LANGUAGES = {
    "ar",
    "de",
    "en",
    "es",
    "fr",
    "hi",
    "id",
    "it",
    "ja",
    "ko",
    "nl",
    "pt",
    "tr",
    "zh",
}


def normalize_language_code(lang_code: Any) -> str:
    value = str(lang_code).strip().lower()
    if not value:
        raise ValueError("language code must be non-empty")
    return value


def row_language(row: dict[str, Any]) -> str:
    if "language" not in row or row["language"] is None:
        raise ValueError(f"Dataset row {row.get('id', '<unknown>')!r} is missing required `language`.")
    return normalize_language_code(row["language"])


def supports_any_benchmark_language(lang_code: str) -> bool:
    return normalize_language_code(lang_code) in BENCHMARK_LANGUAGES


def supports_language_code(lang_code: str, supported_languages: set[str] | frozenset[str]) -> bool:
    return normalize_language_code(lang_code) in supported_languages


def adapter_supports_language(adapter: Any, lang_code: str) -> bool:
    supports_language = getattr(adapter, "supports_language", None)
    if callable(supports_language):
        return bool(supports_language(normalize_language_code(lang_code)))
    return True
