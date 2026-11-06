"""Deterministic flagging. Over-selects on purpose; Haiku decides what actually matters.

Secrets come from Yelp detect-secrets, everything else from Microsoft Presidio.
"""
from __future__ import annotations

import functools
from pathlib import Path

HOME = str(Path.home())
# detect-secrets' ad-hoc line scan also reports sub-fragments ("and", "4c") of a real hit;
# every real credential observed is >= 16 chars, so shorter values are fragments, not secrets.
MIN_SECRET_LEN = 16


@functools.cache
def _presidio():
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]}).create_engine()
    return AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])


def strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in strings(v)]
    return []


def secrets(text: str) -> set[str]:
    from detect_secrets.core.scan import scan_line
    from detect_secrets.settings import default_settings

    found = set()
    with default_settings():
        for line in text.splitlines():
            found |= {s.secret_value for s in scan_line(line) if s.secret_value and len(s.secret_value) >= MIN_SECRET_LEN}
    return found


def pii(text: str) -> list[tuple[str, str]]:
    return [(r.entity_type, text[r.start:r.end]) for r in _presidio().analyze(text=text, language="en")]


def scan_step(step: dict) -> dict:
    """{"secrets": [...], "pii": [[kind, text], ...]} for one ATIF step. Prose gets NER; every field gets secret scanning."""
    every = "\n".join(strings(step))
    prose = "\n".join(filter(None, [step.get("message") if isinstance(step.get("message"), str) else "", step.get("reasoning_content")]))
    return {"secrets": sorted(secrets(every)), "pii": sorted({tuple(p) for p in pii(prose)})}
