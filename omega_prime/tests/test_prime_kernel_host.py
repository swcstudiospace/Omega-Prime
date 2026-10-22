# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Round-trip Omega's in-process host against the pinned MIT Prime ``rlm`` package.

The runtime is imported from the Prime checkout (see VENDOR.md), not copied.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from omega_prime.prime_kernel import (
    InProcessHost,
    checkout,
    ensure_runtime_imported,
    native,
    workspace_members,
)

_MEMBERS = [
    "pa-telemetry",
    "pa-types",
    "pa-ai",
    "pa-models",
    "pa-agent",
    "pa-core",
    "pa-daemon",
    "pa-tui",
    "pa-cli",
]


def _run_child(
    prompt: str, model: str | None = None, thinking: str | None = None
) -> str:
    return "pong"


# Caller-supplied fixture selector: relayed verbatim by the kernel, never an
# advertised catalog entry (the host brokers no model inventory).
_MODEL = "test-provider/test-model"


def _parent(tmp_path):
    return SimpleNamespace(
        session_dir=tmp_path,
        session_name=None,
        delegate_depth=0,
        max_depth=2,
        max_children=4,
    )


def _fake_workspace(tmp_path):
    root = tmp_path / "prime-agent"
    manifests = {
        "pa-core": "",
        # Valid TOML a line scan would misread: trailing comment and a tab.
        "pa-perf": "publish\t= false # dev tooling\n",
        # publish = false outside [package] must not drop a shipped crate.
        "pa-tui": "[package.metadata.docs]\npublish = false\n",
    }
    for name, extra in manifests.items():
        crate = root / "crates" / name
        crate.mkdir(parents=True)
        (crate / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\n{extra}', encoding="utf-8"
        )
    members = ", ".join(f'"crates/{name}"' for name in manifests)
    (root / "Cargo.toml").write_text(
        f"[workspace]\nmembers = [{members}]\n", encoding="utf-8"
    )
    return root


def test_workspace_members_skip_unpublished_dev_tooling(tmp_path, monkeypatch) -> None:
    root = _fake_workspace(tmp_path)
    monkeypatch.setattr(checkout, "checkout_root", lambda: root)
    assert checkout.workspace_members() == ["pa-core", "pa-tui"]


def test_native_fallback_matches_workspace_members(tmp_path, monkeypatch) -> None:
    assert native._members_from_cargo() == workspace_members() == _MEMBERS
    root = _fake_workspace(tmp_path)
    monkeypatch.setattr(checkout, "checkout_root", lambda: root)
    assert native._members_from_cargo(root) == checkout.workspace_members()


def test_prime_kernel_host_round_trip(tmp_path) -> None:
    assert workspace_members() == _MEMBERS
    rlm = ensure_runtime_imported()
    previous = rlm.repl.host_request
    previous_active = rlm.repl.is_active
    parent = _parent(tmp_path)
    host = InProcessHost(parent, run_child=_run_child)
    host.install()
    try:
        asyncio.run(_round_trip(rlm))
    finally:
        host.uninstall()
    assert rlm.repl.host_request is previous
    assert rlm.repl.is_active is previous_active


async def _round_trip(rlm) -> None:
    handle = await rlm.spawn("ping", name="child", model=_MODEL)
    assert handle.model == _MODEL
    results = await rlm.collect(handle, timeout_ms=5000)
    assert results[0].status == "done"
    assert results[0].answer_preview is not None and "pong" in results[0].answer_preview
    listed = await rlm.list_subagents()
    assert any(row.rlm_child_id == handle.rlm_child_id for row in listed)
    # Roster rows are metadata-only: no answer text outside collect.
    assert all(row.answer_preview is None for row in listed)
    deleted = await rlm.delete_subagent(handle)
    assert deleted.rlm_child_id == handle.rlm_child_id


def test_kernel_progress_note_unbound_is_rejected(tmp_path) -> None:
    parent = _parent(tmp_path)
    host = InProcessHost(parent, run_child=_run_child)
    reply = asyncio.run(
        host.host_request({"type": "rlm.progress.note", "message": "root note"})
    )
    assert reply["status"] == "ok"
    assert reply["result"] == {"accepted": False, "retry_after_ms": None}


def test_kernel_child_bound_progress_accepts_then_throttles(tmp_path) -> None:
    seen: list = []

    def noting_child(prompt, model=None, thinking=None, progress=None):
        if progress is not None:
            seen.append(progress("halfway"))
            seen.append(progress("again"))
        return "pong"

    parent = _parent(tmp_path)
    host = InProcessHost(parent, run_child=noting_child)
    run = asyncio.run(
        host.host_request(
            {
                "type": "rlm.run",
                "prompt": "ping",
                "kwargs": {"name": "child", "model": _MODEL},
            }
        )
    )
    assert run["status"] == "ok"
    assert run["result"]["model"] == _MODEL
    collected = asyncio.run(
        host.host_request(
            {
                "type": "rlm.collect",
                "targets": [run["result"]["rlm_child_id"]],
                "timeout_ms": 5000,
            }
        )
    )
    assert collected["status"] == "ok"
    assert collected["result"]["results"][0]["status"] == "done"
    assert seen[0].accepted is True
    assert seen[1].accepted is False
    assert seen[1].retry_after_ms is not None and seen[1].retry_after_ms > 0
    listed = asyncio.run(host.host_request({"type": "rlm.list_subagents"}))
    rows = listed["result"]["subagents"]
    assert rows[0]["progress_note"] == "halfway"
    assert "answer_preview" not in rows[0]
    assert "pong" not in str(rows)
    assert "pong" in collected["result"]["results"][0]["answer_preview"]
    deleted = asyncio.run(
        host.host_request(
            {
                "type": "rlm.delete_subagent",
                "target": run["result"]["rlm_child_id"],
            }
        )
    )
    assert "answer_preview" not in deleted["result"]["subagent"]
    assert "pong" not in str(deleted["result"])


def test_kernel_create_session_returns_real_session_file(tmp_path) -> None:
    from omega_prime.session.persist import load_session

    parent = _parent(tmp_path)
    host = InProcessHost(parent, run_child=_run_child)
    created = asyncio.run(
        host.host_request(
            {
                "type": "rlm.create_session",
                "prompt": "durable work",
                "kwargs": {
                    "name": "durable",
                    "model": _MODEL,
                    "cwd": None,
                },
            }
        )
    )
    assert created["status"] == "ok"
    assert created["result"]["model"] == _MODEL
    session_file = created["result"]["session_file"]
    assert session_file.endswith(".json")
    document = load_session(tmp_path, created["result"]["session_id"])
    assert document["messages"][0] == {"role": "user", "content": "durable work"}
    renamed = asyncio.run(
        host.host_request(
            {
                "type": "rlm.rename",
                "name": "renamed",
                "session_id": created["result"]["session_id"],
            }
        )
    )
    # create_session names the child, not the session id: renaming by the
    # durable session id addresses no child and stays an explicit error.
    assert renamed["status"] == "error"


def test_kernel_create_session_rejects_non_null_cwd_before_any_child(
    tmp_path,
) -> None:
    host = InProcessHost(_parent(tmp_path), run_child=_run_child)
    rejected = asyncio.run(
        host.host_request(
            {
                "type": "rlm.create_session",
                "prompt": "durable work",
                "kwargs": {
                    "name": "durable",
                    "model": _MODEL,
                    "cwd": str(tmp_path / "elsewhere"),
                },
            }
        )
    )
    assert rejected["status"] == "error"
    assert rejected["error"].startswith("bad_value")
    listed = asyncio.run(host.host_request({"type": "rlm.list_subagents"}))
    assert listed["result"]["subagents"] == []
    assert list(tmp_path.iterdir()) == []


def test_kernel_rejects_boolean_model_before_child(tmp_path) -> None:
    host = InProcessHost(_parent(tmp_path), run_child=_run_child)
    rejected = asyncio.run(
        host.host_request(
            {
                "type": "rlm.run",
                "prompt": "ping",
                "kwargs": {"name": "child", "model": True},
            }
        )
    )
    assert rejected["status"] == "error"
    listed = asyncio.run(host.host_request({"type": "rlm.list_subagents"}))
    assert listed["result"]["subagents"] == []


def test_kernel_missing_model_fails_before_child(tmp_path) -> None:
    host = InProcessHost(_parent(tmp_path), run_child=_run_child)
    rejected = asyncio.run(
        host.host_request(
            {"type": "rlm.run", "prompt": "ping", "kwargs": {"name": "child"}}
        )
    )
    assert rejected["status"] == "error"
    assert "model selector" in rejected["error"]
    listed = asyncio.run(host.host_request({"type": "rlm.list_subagents"}))
    assert listed["result"]["subagents"] == []


def test_kernel_configured_model_applies_without_request_model(tmp_path) -> None:
    host = InProcessHost(_parent(tmp_path), run_child=_run_child, model=_MODEL)
    run = asyncio.run(
        host.host_request(
            {"type": "rlm.run", "prompt": "ping", "kwargs": {"name": "child"}}
        )
    )
    assert run["status"] == "ok"
    assert run["result"]["model"] == _MODEL


def test_kernel_spoofed_progress_ids_are_rejected(tmp_path) -> None:
    host = InProcessHost(_parent(tmp_path), run_child=_run_child)
    run = asyncio.run(
        host.host_request(
            {
                "type": "rlm.run",
                "prompt": "ping",
                "kwargs": {"name": "child", "model": _MODEL},
            }
        )
    )
    assert run["status"] == "ok"
    real_id = run["result"]["rlm_child_id"]
    # Explicit ids from an unbound root context cannot impersonate a child —
    # not even the child's own real id.
    for probe in (
        {"type": "rlm.progress.note", "message": "spoofed", "child_id": real_id},
        {"type": "rlm.progress.note", "message": "spoofed", "rlm_child_id": real_id},
        {"type": "rlm.progress.note", "message": "spoofed", "session_id": real_id},
        {"type": "rlm.progress.note", "message": "spoofed", "child_id": "ghost"},
    ):
        reply = asyncio.run(host.host_request(probe))
        assert reply["status"] == "ok"
        assert reply["result"] == {"accepted": False, "retry_after_ms": None}
    listed = asyncio.run(host.host_request({"type": "rlm.list_subagents"}))
    assert listed["result"]["subagents"][0]["progress_note"] is None


def test_kernel_find_models_advertises_nothing(tmp_path) -> None:
    host = InProcessHost(_parent(tmp_path), run_child=_run_child)
    reply = asyncio.run(host.host_request({"type": "rlm.find_models", "limit": 8}))
    assert reply == {"status": "ok", "result": {"models": []}}
    bad = asyncio.run(host.host_request({"type": "rlm.find_models", "limit": True}))
    assert bad["status"] == "error"
