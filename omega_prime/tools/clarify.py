"""Define the injected interaction boundary for live clarification."""

from __future__ import annotations

from typing import Any, Protocol


class ClarifyInteraction(Protocol):
    """Live operator transport used to ask and receive a clarification."""

    def ask(self, question: str) -> str:
        """Send a question to the operator and return their answer."""


def clarify(interaction: ClarifyInteraction | None, question: str) -> dict[str, Any]:
    """Ask the live operator, failing closed when no transport is configured."""
    if not isinstance(question, str):
        return {"error": "question must be a string"}
    if interaction is None:
        return {"error": "undeliverable: no clarify interaction is configured"}
    try:
        answer = interaction.ask(question)
    except Exception:
        return {"error": "undeliverable: live operator interaction failed"}
    if not isinstance(answer, str):
        return {
            "error": "undeliverable: live operator interaction returned invalid answer"
        }
    return {"answer": answer}


__all__ = ["ClarifyInteraction", "clarify"]
