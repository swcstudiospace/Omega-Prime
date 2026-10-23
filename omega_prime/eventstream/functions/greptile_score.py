"""Parse Greptile's explicit score comments into the typed review event."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from omega_prime.eventstream.functions import publish_keyed
from omega_prime.eventstream.taxonomy import EVENT_TYPES, derive_key, validate_payload

_FUNCTION_SOURCE = "urn:omega:function:greptile-score"
_APPROVED_GREPTILE_ACTORS = frozenset(
    {
        "greptile",
        "greptile-apps",
        "greptile-apps[bot]",
        "greptileai",
    }
)
# Accept Score: N, Score: N%, Score: N/100, Confidence score: N/5, and the
# HTML marker <!-- greptile_confidence_score: N --> used by Greptile reviews.
_SCORE_PATTERN = re.compile(
    r"(?im)(?:^\s*(?:greptile\s+)?(?:confidence\s+)?score\s*:\s*(\d{1,3})"
    r"(?:\s*/\s*(?:100|5)|\s*%)?\s*$|"
    r"<!--\s*greptile_confidence_score\s*:\s*([0-5])\s*-->)"
)


@dataclass(frozen=True, slots=True)
class NoScore:
    """A comment did not contain one unambiguous supported score."""

    reason: str


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """A validated score and its PR identity, preserving the routing key."""

    repo: str
    pr: int
    head_sha: str
    key: str
    score: int


def parse_greptile_score(
    comment: str, identity: Mapping[str, object]
) -> ScoreResult | NoScore:
    """Parse one standalone ``Score: N``, ``Score: N%`` or ``Score: N/100`` line."""
    if not isinstance(comment, str):
        return NoScore("score_absent_or_malformed")
    matches = list(_SCORE_PATTERN.finditer(comment))
    if not matches:
        return NoScore("score_absent_or_malformed")
    if len(matches) != 1:
        return NoScore("score_ambiguous")
    match = matches[0]
    raw = match.group(1) or match.group(2)
    score = int(raw)
    # Confidence score: N/5 and the HTML marker are on a 0-5 scale; normalize
    # to the 0-100 event payload the taxonomy stores.
    matched_text = match.group(0)
    if match.group(2) is not None or re.search(r"/\s*5\s*$", matched_text):
        if score > 5:
            return NoScore("score_out_of_range")
        score = score * 20
    elif score > 100:
        return NoScore("score_out_of_range")
    repo = identity.get("repo")
    pr = identity.get("pr")
    head_sha = identity.get("head_sha")
    key = identity.get("key")
    if (
        not isinstance(repo, str)
        or not repo
        or isinstance(pr, bool)
        or not isinstance(pr, int)
        or pr <= 0
        or not isinstance(head_sha, str)
        or not head_sha
        or not isinstance(key, str)
        or not key
    ):
        return NoScore("identity_missing_or_malformed")
    try:
        validated = validate_payload(
            "omega.sdlc.review.greptile_scored",
            {
                "repo": repo,
                "number": pr,
                "head_sha": head_sha,
                "actor": "greptile",
                "score": score,
            },
        )
        expected_key = derive_key("omega.sdlc.review.greptile_scored", validated)
    except (TypeError, ValueError):
        return NoScore("identity_missing_or_malformed")
    if key != expected_key:
        return NoScore("identity_missing_or_malformed")
    return ScoreResult(
        repo=cast(str, validated["repo"]),
        pr=cast(int, validated["number"]),
        head_sha=cast(str, validated["head_sha"]),
        key=expected_key,
        score=cast(int, validated["score"]),
    )


def process(input: bytes, context: Any) -> None:
    """Parse score text from the broker property on a catalog comment event.

    The immutable payload intentionally contains only comment identity. The
    ingress may attach the private ``comment_body`` broker property; only the
    parsed score leaves the Function.
    """
    from omega_prime.eventstream.envelope import CloudEvent

    try:
        incoming = CloudEvent.from_bytes(input)
    except (TypeError, ValueError):
        return None
    if (
        incoming.type != "omega.sdlc.review.commented"
        or incoming.source == _FUNCTION_SOURCE
    ):
        return None
    get_properties = getattr(context, "get_message_properties", None)
    if not callable(get_properties):
        return None
    properties = get_properties()
    if not isinstance(properties, Mapping):
        return None
    comment = properties.get("comment_body")
    if not isinstance(comment, str) and incoming.model_extra:
        comment = incoming.model_extra.get("comment")
    if not isinstance(comment, str):
        return None
    data = incoming.data
    actor = data.get("actor")
    reviewer = data.get("reviewer")
    identities = {
        value.casefold()
        for value in (actor, reviewer)
        if isinstance(value, str) and value
    }
    if not identities.intersection(
        {name.casefold() for name in _APPROVED_GREPTILE_ACTORS}
    ):
        return None
    repo, number, head_sha = data.get("repo"), data.get("number"), data.get("head_sha")
    if (
        not isinstance(repo, str)
        or isinstance(number, bool)
        or not isinstance(number, int)
    ):
        return None
    if not isinstance(head_sha, str):
        return None
    key = derive_key(incoming.type, data)
    result = parse_greptile_score(
        comment,
        {"repo": repo, "pr": number, "head_sha": head_sha, "key": key},
    )
    if isinstance(result, NoScore):
        return None
    event_type = "omega.sdlc.review.greptile_scored"
    output = CloudEvent(
        specversion="1.0",
        id="greptile-"
        + hashlib.sha256(f"{incoming.id}\\0{result.score}".encode()).hexdigest()[:32],
        source=_FUNCTION_SOURCE,
        type=event_type,
        subject=incoming.subject or f"pr/{number}",
        datacontenttype="application/json",
        dataschema=EVENT_TYPES[event_type].schema_ref,
        data={
            "repo": result.repo,
            "number": result.pr,
            "head_sha": result.head_sha,
            "actor": "greptile",
            "score": result.score,
        },
    )
    publish_keyed(
        context,
        EVENT_TYPES[event_type].topic,
        output.to_bytes(),
        result.key,
    )
    return None


class GreptileScoreFunction:
    """Apache Pulsar Python Function class entry point."""

    def process(self, input: bytes, context: Any) -> None:
        return process(input, context)
