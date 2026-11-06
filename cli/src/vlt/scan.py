"""Deterministic flagging. Over-selects on purpose; Haiku decides what actually matters.

Secrets come from Yelp detect-secrets, everything else from Microsoft Presidio.
"""
from __future__ import annotations

import functools
import os
import re
import tempfile
from pathlib import Path

HOME = str(Path.home())
# detect-secrets may match only a prefix of a credential (e.g. its Stripe rule); replace the whole token.
TOKEN = re.compile(r"[A-Za-z0-9_\-+/=.:]*")


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
    """Credentials per detect-secrets' file scanner, which applies its entropy limits and filters.
    The ad-hoc line scanner skips those and flags ordinary words, so it is not used."""
    from detect_secrets import SecretsCollection
    from detect_secrets.settings import default_settings

    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as f:
        f.write(text)
    try:
        with default_settings():
            found = SecretsCollection()
            found.scan_file(f.name)
            values = {s.secret_value for _, s in found if s.secret_value}
    finally:
        os.unlink(f.name)
    out = set()
    for v in values:
        for m in re.finditer(re.escape(v), text):
            out.add(v + TOKEN.match(text, m.end()).group(0))
    return out


def pii(text: str) -> list[tuple[str, str]]:
    return [(r.entity_type, text[r.start:r.end]) for r in _presidio().analyze(text=text, language="en")]


def scan_step(step: dict) -> dict:
    """{"secrets": [...], "pii": [[kind, text], ...]} for one ATIF step. Prose gets NER; every field gets secret scanning."""
    every = "\n".join(strings(step))
    prose = "\n".join(filter(None, [step.get("message") if isinstance(step.get("message"), str) else "", step.get("reasoning_content")]))
    return {"secrets": sorted(secrets(every)), "pii": sorted({tuple(p) for p in pii(prose)})}
