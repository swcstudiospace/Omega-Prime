"""Shared Greptile confidence-score parsing used by dashboard and eventstream."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Exact GitHub login the dashboard collector already trusts.
GREPTILE_BOT_LOGIN = "greptile-apps[bot]"

# Keep these patterns byte-identical to the historical dashboard collectors so
# both surfaces agree on what counts as a Greptile review score.
CONFIDENCE_SCORE = re.compile(r"Confidence score:\s*([0-5])\s*/\s*5", re.IGNORECASE)
BODY_SCORE = re.compile(
    r"<!--\s*greptile_confidence_score:\s*([0-5])\s*-->", re.IGNORECASE
)
# Eventstream also accepts explicit Score / Greptile score lines and a
# ``Confidence score: N/100`` form that is not used by the dashboard gate.
EXPLICIT_PERCENT = re.compile(
    r"(?im)^\s*(?:greptile\s+)?score\s*:\s*(\d{1,3})"
    r"(?:\s*/\s*100|\s*%)?\s*$"
)
EXPLICIT_FRACTION = re.compile(
    r"(?im)^\s*(?:greptile\s+)?score\s*:\s*([0-5])\s*/\s*5\s*$"
)
CONFIDENCE_PERCENT = re.compile(r"(?im)Confidence score:\s*(\d{1,3})\s*/\s*100")


@dataclass(frozen=True, slots=True)
class ParsedGreptileScore:
    """One unambiguous score on the 0-100 eventstream payload scale."""

    percent: int
    source: str


def extract_greptile_score(text: str) -> ParsedGreptileScore | None:
    """Return one unambiguous 0-100 score, or ``None`` if absent/ambiguous.

    Every recognized score shape contributes a candidate. Distinct candidate
    values (including a ``Confidence score: N/5`` header disagreeing with the
    HTML body marker) are treated as ambiguous so a forged header cannot
    override Greptile's marker, and conflicting lines cannot invent a score.
    """
    if not isinstance(text, str) or not text:
        return None
    candidates: list[tuple[int, str]] = []
    for match in CONFIDENCE_SCORE.finditer(text):
        candidates.append((int(match.group(1)) * 20, "confidence"))
    for match in BODY_SCORE.finditer(text):
        candidates.append((int(match.group(1)) * 20, "body"))
    for match in CONFIDENCE_PERCENT.finditer(text):
        raw = int(match.group(1))
        if raw <= 100:
            candidates.append((raw, "confidence_percent"))
    for match in EXPLICIT_FRACTION.finditer(text):
        candidates.append((int(match.group(1)) * 20, "explicit"))
    for match in EXPLICIT_PERCENT.finditer(text):
        raw = int(match.group(1))
        if raw <= 100:
            candidates.append((raw, "explicit"))
    if not candidates:
        return None
    values = {value for value, _source in candidates}
    if len(values) != 1:
        return None
    percent = next(iter(values))
    sources = {source for value, source in candidates if value == percent}
    if "confidence" in sources and "body" in sources:
        source = "agreed"
    elif len(sources) == 1:
        source = next(iter(sources))
    else:
        source = "agreed"
    return ParsedGreptileScore(percent=percent, source=source)
