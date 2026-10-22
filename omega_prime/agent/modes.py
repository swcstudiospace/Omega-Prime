"""Plan mode: the loop may read, never write.

Ports the ``packages/coding-agent/src/plan-mode`` guarantee (planning never
changes files) as a tool-map wrapper. Blocked tools are replaced by refusals
that name the tool; the originals are never called, so the file the call
named is unchanged. Unblocked tools are the identical function objects.
"""

from __future__ import annotations

from typing import Any

PLAN_MODE_BLOCKED = frozenset(
    {
        "write_file",
        "patch_file",
        "edit_file",
        "execute_code",
        "run_terminal",
        "mcp_call",
        "x_post",
        "x_post_thread",
        "x_upload_media",
        "exec_job_start",
        "exec_job_kill",
    }
)


def apply_plan_mode(tools: dict) -> dict:
    """Copy ``tools`` with every blocked name replaced by a refusal."""
    wrapped = dict(tools)
    for name in PLAN_MODE_BLOCKED:
        if name in wrapped:
            wrapped[name] = _refuse(name)
    return wrapped


def _refuse(name: str) -> Any:
    def _refused(*args: Any, **kwargs: Any) -> str:
        return f"error: plan mode forbids {name}"

    return _refused


__all__ = ["PLAN_MODE_BLOCKED", "apply_plan_mode"]
