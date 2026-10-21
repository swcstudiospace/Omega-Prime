"""Baseline local readiness scenarios (Phase 75-05).

Proves the baseline local slice through real production dispatch on the
provided probe: file write/read/patch/edit/search roundtrips with reopened-byte
assertions, workspace-escape refusal, in-memory TODO, memory add to lead recall
plus durable fresh-process persistence, skill lifecycle with reopened artifacts,
harmless allowlisted subprocess execution with command-log-backed receipt proof,
canonical MCP interop against an owned official SDK server, a real available
LSP (or an exact missing-binary prerequisite), and reachable local comparison
reads. Four gated tools (sys_cache, sys_contract_propose, qua_receipt_approve,
qua_waiver_record) run positive in-memory effects under the Core-granted
targeted local approval; denial/revoke lifecycle stays Core-owned on the
loopback host. Unavailable transports/backends/services are recorded blocked
with their genuine source-wired prerequisites, never generic untested labels.

Only ``omega_prime/grokbot/readiness_local.py`` is owned here. Shared types
come from ``local_validation`` only; the ``mcp_call`` roster name is consumed
unchanged so the 75-10 canonical session cutover needs no change. Every exact
tool name, argument key, and result field below traces to the registered
handler signatures and ``_SCHEMAS`` in ``omega_prime/tools/coding.py``,
``growth.py``, ``platform.py``, ``quality.py``, ``packs.py``, ``ide.py``,
``systems.py``, ``mobile.py``, and ``lead.py``.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from omega_prime.grokbot.local_validation import ReadinessProbe

__all__ = ["run"]

_MARKER = "omega-prime-75-05-baseline"
_PROFILE = "readiness-75-05"
_SKILL_NAME = "readiness-75-05-probe"


def _skill_document(body: str) -> str:
    """Canonical skill file. ``skill_manage`` stores a valid envelope unchanged.

    The lifecycle below asserts that the reopened file equals the content
    that was submitted, so the fixture is that envelope rather than plain
    Markdown the manager would wrap.
    """
    return (
        "---\n"
        f"name: {_SKILL_NAME}\n"
        "description: 75-05 baseline skill probe\n"
        "---\n"
        f'<skill name="{_SKILL_NAME}" version="1">\n'
        f"{body}</skill>\n"
    )


_SKILL_V1 = _skill_document("# Probe skill v1\n75-05 skill body v1.\n")
_SKILL_V2 = _skill_document("# Probe skill v2\n75-05 skill body v2 with an update.\n")
_LSP_FILE = f"{_PROFILE}/lsp_probe.py"
_LSP_SOURCE = "def probe_answer() -> int:\n    return 42\n"
_MCP_TOOL = "local_roundtrip"
_MCP_SCRIPT = f"{_PROFILE}/mcp_proof_server.py"
_MCP_HOME_FILE = "mcp-proof-75-05.txt"
_MCP_CONTENT = "75-05 official-SDK interop payload"


def run(probe: ReadinessProbe) -> None:
    """Execute the full baseline local slice on one fresh product child."""
    _files(probe)
    _todo(probe)
    _memory(probe)
    _skills(probe)
    _subprocess_receipts(probe)
    _comparison_reads(probe)
    _lead_effects(probe)
    _service_blocks(probe)
    _mcp_interop(probe)
    _ide_browser_devices(probe)
    _negatives(probe)
    _capacity(probe)
    _dispatch_lane(probe)


def _result(probe: ReadinessProbe, name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch through production and return the unchanged envelope result."""
    return probe.call(name, arguments)["result"]


def _capacity(probe: ReadinessProbe) -> None:
    """Exercise ``omega_capacity`` without calling a model HTTP API."""
    body = _result(probe, "omega_capacity", {})
    probe.check(isinstance(body, dict), "omega_capacity must return an object")
    assert isinstance(body, dict)
    allow = body.get("allowlist")
    probe.check(
        isinstance(allow, dict)
        and allow.get("refused_subscription_oauth") == ["anthropic", "muse"],
        "omega_capacity must refuse Anthropic and Muse subscription OAuth",
    )
    plans = body.get("plans")
    names = (
        {row.get("plan") for row in plans if isinstance(row, dict)}
        if isinstance(plans, list)
        else set()
    )
    probe.check(
        names == {"zai", "xai", "anthropic", "muse", "cursor", "chatgpt"},
        "omega_capacity must list the six coding plans",
    )
    encoded = json.dumps(body, ensure_ascii=False)
    probe.check(
        "sk-" not in encoded and "Bearer " not in encoded,
        "omega_capacity must not return a credential",
    )
    probe.record(
        "capacity:allowlist",
        ("omega_capacity",),
        "omega_capacity listed six plans and refused Anthropic and Muse "
        "subscription OAuth; no model HTTP call",
    )


def _refused(probe: ReadinessProbe, name: str, arguments: dict[str, Any]) -> Any:
    """Call a tool whose honest result is an error object, not a raise."""
    if probe.emulator is None:
        raise AssertionError("probe has no emulator")
    outcome = probe.emulator.execute_tool(name, arguments)
    probe.check(
        outcome.get("status") == "error" and isinstance(outcome.get("result"), dict),
        f"{name} must return an error object, got {outcome.get('status')}",
    )
    result = outcome.get("result")
    return result if isinstance(result, dict) else {}


def _dispatch_lane(probe: ReadinessProbe) -> None:
    """Exercise the lane tools with the coordinator and confirm token unset.

    The isolated child environment never copies the parent process
    environment, so these calls stop before any coordinator HTTP request.
    """
    dispatched = _refused(
        probe,
        "omega_dispatch",
        {
            "repo": "acme/widget",
            "prompt": "fix the python test",
            "confirm_token": "readiness-local-confirm",
            "languages": ["python"],
        },
    )
    probe.check(
        dispatched.get("error") == "confirm_rejected",
        "omega_dispatch must reject a confirm token when none is configured",
    )
    status = _refused(probe, "omega_jobs_status", {})
    probe.check(
        status.get("error") == "not_configured",
        "omega_jobs_status must report the coordinator unset",
    )
    cancelled = _refused(probe, "omega_jobs_cancel", {"job_id": "local-probe"})
    probe.check(
        cancelled.get("error") == "not_configured",
        "omega_jobs_cancel must report the coordinator unset",
    )
    probe.record(
        "dispatch:lane-refusal",
        ("omega_dispatch", "omega_jobs_status", "omega_jobs_cancel"),
        "omega_dispatch rejected the confirm token; job status and cancel "
        "reported the fleet coordinator unset; no coordinator HTTP call",
    )


def _files(probe: ReadinessProbe) -> None:
    rel = f"{_PROFILE}/baseline.txt"
    v1 = f"alpha one\n{_MARKER} marker\nomega three\n"
    v2 = f"alpha one\n{_MARKER} patched marker\nomega three\n"
    v3 = f"alpha one\n{_MARKER} edited marker\nomega three\n"

    written = _result(probe, "write_file", {"path": rel, "content": v1})
    probe.check(
        written.get("bytes_written") == len(v1.encode("utf-8")),
        f"write_file must report exact bytes, got {written}",
    )
    work_file = Path(probe.layout.work) / rel
    probe.check(
        work_file.read_bytes().decode("utf-8") == v1,
        "scratch file reopened bytes must equal the written content",
    )

    read_back = _result(probe, "read_file", {"path": rel})
    probe.check(
        read_back.get("content") == v1,
        f"read_file must return the written bytes, got {read_back}",
    )

    patch_diff = (
        f"--- a/{rel}\n"
        f"+++ b/{rel}\n"
        "@@ -1,3 +1,3 @@\n"
        " alpha one\n"
        f"-{_MARKER} marker\n"
        f"+{_MARKER} patched marker\n"
        " omega three\n"
    )
    patched = _result(probe, "patch_file", {"path": rel, "diff": patch_diff})
    probe.check(
        patched.get("applied") is True,
        f"patch_file must apply, got {patched}",
    )
    probe.check(
        (Path(probe.layout.work) / rel).read_bytes().decode("utf-8") == v2,
        "patched scratch file reopened bytes must equal v2",
    )

    edit_diff = (
        f"--- a/{rel}\n"
        f"+++ b/{rel}\n"
        "@@ -1,3 +1,3 @@\n"
        " alpha one\n"
        f"-{_MARKER} patched marker\n"
        f"+{_MARKER} edited marker\n"
        " omega three\n"
    )
    edited = _result(probe, "edit_file", {"path": rel, "diff": edit_diff})
    probe.check(
        edited.get("applied") is True,
        f"edit_file must apply, got {edited}",
    )
    probe.check(
        (Path(probe.layout.work) / rel).read_bytes().decode("utf-8") == v3,
        "edited scratch file reopened bytes must equal v3",
    )

    found = _result(probe, "search_text", {"query": _MARKER, "path": "."})
    probe.check(
        isinstance(found.get("paths"), list) and rel in found["paths"],
        f"search_text must find the scratch marker, got {found}",
    )
    probe.record(
        "baseline file write-read-patch-edit-search roundtrip",
        ("write_file", "read_file", "patch_file", "edit_file", "search_text"),
        "local-file-proof: reopened bytes equal after write, patch, and edit; "
        f"search_text paths contain {rel}; all inside scratch WORK",
    )


def _todo(probe: ReadinessProbe) -> None:
    items = [
        {
            "id": "75-05-1",
            "content": "prove baseline file effects",
            "status": "completed",
        },
        {
            "id": "75-05-2",
            "content": "prove memory persistence",
            "status": "in_progress",
        },
    ]
    stored = _result(probe, "todo_write", {"todos": items})
    probe.check(
        stored.get("todos") == items,
        f"todo_write must echo the stored list, got {stored}",
    )
    current = _result(probe, "todo_read", {})
    probe.check(
        current.get("todos") == items,
        f"todo_read must return the written list, got {current}",
    )
    probe.record(
        "TODO write-read boundary",
        ("todo_write", "todo_read"),
        "in-memory boundary: TodoStore replace/read in this process only, "
        "not durable state",
    )


def _memory(probe: ReadinessProbe) -> None:
    sentinel = f"[hindsight:omega-prime-lead] {_MARKER} sentinel"
    kept = _result(
        probe, "memory", {"action": "add", "target": "memory", "content": sentinel}
    )
    probe.check(
        kept.get("ok") is True,
        f"memory add must succeed, got {kept}",
    )
    recall = _result(probe, "lead_memory_recall", {"query": f"{_MARKER} sentinel"})
    probe.check(
        any(
            _MARKER in entry.get("content", "")
            for entry in recall.get("results", [])
            if isinstance(entry, dict)
        ),
        f"lead_memory_recall must surface the retained entry, got {recall}",
    )
    candidates = list(Path(probe.layout.state).rglob("MEMORY.md"))
    probe.check(
        bool(candidates),
        "durable memory file MEMORY.md must exist under disposable state",
    )
    probe.check(
        any(_MARKER in path.read_bytes().decode("utf-8") for path in candidates),
        "reopened MEMORY.md bytes must persist the sentinel for a fresh process",
    )
    probe.record(
        "memory add to lead recall with durable persistence",
        ("memory", "lead_memory_recall"),
        "same-registry recall plus reopened MEMORY.md bytes proving a fresh "
        "MemoryStore reloads what this store wrote",
    )
    seen = _result(probe, "session_search", {"query": f"{_MARKER}-no-such-session"})
    probe.check(
        seen.get("success") is True and isinstance(seen.get("messages"), list),
        f"session_search must answer the read shape, got {seen}",
    )
    probe.record(
        "session search read shape",
        ("session_search",),
        "effect-free local read returning success/messages on the empty fixture DB",
    )


def _skills(probe: ReadinessProbe) -> None:
    created = _result(
        probe,
        "skill_manage",
        {"action": "create", "name": _SKILL_NAME, "content": _SKILL_V1},
    )
    probe.check(
        created.get("success") is True,
        f"skill_manage create must succeed, got {created}",
    )
    updated = _result(
        probe,
        "skill_manage",
        {"action": "edit", "name": _SKILL_NAME, "content": _SKILL_V2},
    )
    probe.check(
        updated.get("success") is True,
        f"skill_manage edit must succeed, got {updated}",
    )
    viewed = _result(probe, "skill_view", {"name": _SKILL_NAME})
    probe.check(
        viewed.get("success") is True and viewed.get("content") == _SKILL_V2,
        f"skill_view must return the updated SKILL.md, got {viewed}",
    )
    skills_root = Path(probe.layout.install) / "omega_prime" / "skills"
    artifacts = list((skills_root / _SKILL_NAME).rglob("SKILL.md"))
    probe.check(
        bool(artifacts),
        "created SKILL.md artifact must exist under disposable INSTALL skills",
    )
    probe.check(
        any(path.read_bytes().decode("utf-8") == _SKILL_V2 for path in artifacts),
        "reopened SKILL.md bytes must equal the updated content",
    )
    probe.record(
        "skill create-edit-view lifecycle",
        ("skill_manage", "skill_view"),
        "real SKILL.md lifecycle with reopened-artifact bytes under disposable INSTALL",
    )


def _comparison_reads(probe: ReadinessProbe) -> None:
    rel = f"{_PROFILE}/baseline.txt"
    scan = _result(probe, "qua_secret_scan", {"paths": [rel]})
    probe.check(
        scan.get("ok") is True
        and scan.get("files") == 1
        and scan.get("findings") == [],
        f"qua_secret_scan must report the clean scratch file, got {scan}",
    )
    probe.record(
        "quality secret scan of scratch baseline",
        ("qua_secret_scan",),
        "read-only local scan: ok/files/findings asserted on the scratch file",
    )
    ack_status = _result(
        probe, "qua_contract_ack_status", {"change_id": "75-05-no-such-change"}
    )
    probe.check(
        ack_status.get("change_id") == "75-05-no-such-change"
        and ack_status.get("complete") is False,
        f"qua_contract_ack_status must report uncovered change, got {ack_status}",
    )
    probe.record(
        "quality contract ack status read",
        ("qua_contract_ack_status",),
        "read-only local ack coverage read with exact change_id/complete fields",
    )
    _gates_run(probe)
    if _git_work_ready(probe):
        _supply_chain(probe)
    else:
        probe.blocked(
            "quality supply-chain check needs git",
            ("qua_supply_chain_check",),
            ("git executable on PATH (not found in fixture)",),
            "scratch WORK git history cannot be built without git",
        )
    probe.blocked(
        "Greptile review needs its token",
        ("qua_greptile_review",),
        ("GREPTILE_API_KEY credential", "qua_greptile_review approval"),
        "no Greptile client is wired in the isolated fixture",
    )
    _receipt_approve(probe)
    _waiver_record(probe)
    probe.blocked(
        "contract ack needs its store",
        ("qua_contract_ack",),
        (
            "configured QualityContext AckStore (None in fixture)",
            "qua_contract_ack approval",
        ),
        "ack store is unconfigured regardless of approval in this fixture",
    )

    _sys_cache(probe)
    _sys_lsp(probe)
    probe.blocked(
        "systems index query needs its store",
        ("sys_index_query",),
        ("timescale index store (SystemsContext.timescale is None)",),
        "no index service is wired in the isolated fixture",
    )
    probe.blocked(
        "systems events query needs its store",
        ("sys_events_query",),
        ("greptime event store (SystemsContext.greptime is None)",),
        "no event service is wired in the isolated fixture",
    )
    _contract_propose(probe)
    _install_git = _install_git_ready(probe)
    if _install_git:
        _design_artifact(probe)

    _artifact_delta(probe)
    if _install_git:
        _lint_diff(probe)
        _entitlements_diff(probe)
        _review_risk(probe)
    probe.blocked(
        "Play Console reads need its token",
        ("mob_play_track_status",),
        ("PLAY_CONSOLE_TOKEN credential",),
        "no Play client is wired in the isolated fixture",
    )
    probe.blocked(
        "Play release writes need token and gate",
        ("mob_play_staged_rollout", "mob_play_halt_rollout"),
        ("PLAY_CONSOLE_TOKEN credential", "release-write approvals"),
        "no Play client is wired; release mutation stays out of this slice",
    )
    probe.blocked(
        "TestFlight read needs its key",
        ("mob_testflight_status",),
        ("ASC_KEY_ID + ASC_ISSUER_ID + ASC_PRIVATE_KEY credentials",),
        "no App Store Connect client is wired in the isolated fixture",
    )
    probe.blocked(
        "App Store release writes need key and gate",
        ("mob_appstore_phased_release", "mob_appstore_pause_release"),
        (
            "ASC_KEY_ID + ASC_ISSUER_ID + ASC_PRIVATE_KEY credentials",
            "release-write approvals",
        ),
        "no App Store Connect client is wired; release mutation stays out of this slice",
    )
    _devices_blocked(probe)

    probe.blocked(
        "pack load needs a declared pack",
        ("app_tools_load",),
        ("pack declaration in OMEGA_PRIME_PACKS_JSON (empty in fixture)",),
        "no app pack is declared for an absorbed seat",
    )
    probe.blocked(
        "pack backend reads need an API base",
        (
            "app_api_smoke",
            "app_supabase_query",
            "app_feature_flags",
            "app_crash_reports",
            "app_scoreboard_get",
            "app_store_listing_get",
            "app_render_job_status",
        ),
        ("app API base in OMEGA_PRIME_PACK_API_BASES_JSON (empty in fixture)",),
        "no owned app service is configured; nothing contacted",
    )
    probe.blocked(
        "pack push test needs backend and gate",
        ("app_push_test",),
        ("app API base in OMEGA_PRIME_PACK_API_BASES_JSON", "app_push_test approval"),
        "no owned app service is configured; nothing contacted",
    )


_CACHE_KEY = f"{_MARKER}-cache"
_CHANGE_ID = "ct-75-05-local"
_RECEIPT_FILE = f"{_PROFILE}/receipt-approve.json"
_RECEIPT_BOT = "readiness-75-05-probe"
_REVIEWER = "readiness-75-05-reviewer"
_APK_DIR = "readiness-75-05"
_APK_BEFORE = f"{_APK_DIR}/before.apk"
_APK_AFTER = f"{_APK_DIR}/after.apk"
_APK_BEFORE_BYTES = 1024
_APK_AFTER_BYTES = 1536


def _sys_cache(probe: ReadinessProbe) -> None:
    """Real in-memory cache effects under the Core-granted targeted approval."""
    put = _result(
        probe, "sys_cache", {"action": "set", "key": _CACHE_KEY, "value": "v1"}
    )
    probe.check(
        put.get("ok") is True and put.get("key") == _CACHE_KEY,
        f"sys_cache set must store, got {put}",
    )
    got = _result(probe, "sys_cache", {"action": "get", "key": _CACHE_KEY})
    probe.check(
        got.get("key") == _CACHE_KEY and got.get("value") == "v1",
        f"sys_cache get must return the exact value, got {got}",
    )
    replaced = _result(
        probe, "sys_cache", {"action": "set", "key": _CACHE_KEY, "value": "v2"}
    )
    probe.check(
        replaced.get("ok") is True,
        f"sys_cache replacement set must store, got {replaced}",
    )
    got2 = _result(probe, "sys_cache", {"action": "get", "key": _CACHE_KEY})
    probe.check(
        got2.get("value") == "v2",
        f"sys_cache get must return the replaced value, got {got2}",
    )
    removed = _result(probe, "sys_cache", {"action": "delete", "key": _CACHE_KEY})
    probe.check(
        removed.get("deleted") is True,
        f"sys_cache delete must remove, got {removed}",
    )
    gone = _result(probe, "sys_cache", {"action": "get", "key": _CACHE_KEY})
    probe.check(
        gone.get("value") is None,
        f"sys_cache get after delete must be empty, got {gone}",
    )
    probe.record(
        "systems cache set-get-replace-delete",
        ("sys_cache",),
        "in-memory cache effects with exact values; denial/revoke lifecycle "
        "proven by the Core loopback over both transports",
    )


def _contract_propose(probe: ReadinessProbe) -> None:
    """Real local contract bundle under the Core-granted targeted approval."""
    bundle = _result(
        probe,
        "sys_contract_propose",
        {
            "surface": "omega_prime/tools/quality.py",
            "body": "75-05 local contract probe",
            "version": "1",
            "summary": "75-05 local contract probe bundle",
            "breaking": False,
            "consumers_required": ["bot-00-omega-prime"],
            "change_id": _CHANGE_ID,
            "migration_note": "none",
        },
    )
    probe.check(
        bundle.get("ok") is True,
        f"sys_contract_propose must build, got {bundle}",
    )
    proposal = bundle.get("proposal", {})
    probe.check(
        isinstance(proposal, dict) and proposal.get("change_id") == _CHANGE_ID,
        f"proposal must carry the fixed change id, got {bundle}",
    )
    files = bundle.get("bundle", {}).get("files", {})
    probe.check(
        isinstance(files, dict)
        and "omega_prime/tools/quality.py" in files
        and f"contracts/changes/{_CHANGE_ID}.yaml" in files,
        f"bundle must carry surface and change files, got {bundle}",
    )
    probe.record(
        "systems contract proposal bundle",
        ("sys_contract_propose",),
        f"local bundle for fixed {_CHANGE_ID}; operator commits, nothing pushed",
    )


def _receipt_approve(probe: ReadinessProbe) -> None:
    """Real receipt stamp over reopened command-log rows, distinct reviewer."""
    rows = _read_command_log(probe)
    term_cmd = " ".join((sys.executable, "-c", "print('omega-prime-75-05-terminal')"))
    exec_cmd = " ".join((sys.executable, "-c", "print('omega-prime-75-05-exec')"))
    wanted = []
    for cmd in (term_cmd, exec_cmd):
        row = next(
            (
                item
                for item in rows
                if item.get("cmd") == cmd and item.get("exit_code") == 0
            ),
            None,
        )
        probe.check(row is not None, f"command log must hold {cmd}")
        assert row is not None
        wanted.append(
            {
                "cmd": row["cmd"],
                "exit_code": row["exit_code"],
                "output_tail": str(row.get("output_tail", "")),
            }
        )
    receipt = {
        "bot": _RECEIPT_BOT,
        "commands": wanted,
        "claims": [
            {
                "claim": "allowlisted terminal argv executed",
                "evidence_command_index": 0,
            },
            {
                "claim": "fixed interpreter snippet executed",
                "evidence_command_index": 1,
            },
        ],
        "unverified": [],
    }
    text = json.dumps(receipt, indent=2) + "\n"
    wrote = _result(probe, "write_file", {"path": _RECEIPT_FILE, "content": text})
    probe.check(
        wrote.get("bytes_written") == len(text.encode("utf-8")),
        f"receipt fixture must be written, got {wrote}",
    )
    stamped = _result(
        probe,
        "qua_receipt_approve",
        {
            "receipt_path": _RECEIPT_FILE,
            "note": "75-05 local stamp",
            "approver": _REVIEWER,
        },
    )
    probe.check(
        stamped.get("ok") is True
        and stamped.get("approved_by") == _REVIEWER
        and stamped.get("pushed") is False,
        f"qua_receipt_approve must stamp without pushing, got {stamped}",
    )
    back = _result(probe, "read_file", {"path": _RECEIPT_FILE})
    try:
        saved = json.loads(back.get("content", ""))
    except ValueError:
        saved = {}
    probe.check(
        isinstance(saved, dict)
        and saved.get("approved_by") == _REVIEWER
        and isinstance(saved.get("approved_at"), str),
        f"stamped receipt reopened bytes must carry the stamp, got {back}",
    )
    probe.record(
        "quality receipt approve stamp",
        ("qua_receipt_approve",),
        "validated against reopened command-log rows, stamped by a distinct "
        "reviewer identity, pushed False, stamp verified by reopened bytes",
    )


def _waiver_record(probe: ReadinessProbe) -> None:
    """Real local waiver file under the Core-granted targeted approval."""
    waiver = _result(
        probe,
        "qua_waiver_record",
        {
            "pr_number": 7505,
            "comment_ids": ["75-05-local-1"],
            "reason": "75-05 local waiver probe",
            "branch": "main",
        },
    )
    probe.check(
        waiver.get("ok") is True
        and waiver.get("pushed") is False
        and isinstance(waiver.get("receipt_path"), str),
        f"qua_waiver_record must write locally without pushing, got {waiver}",
    )
    assert isinstance(waiver.get("receipt_path"), str)
    back = _result(probe, "read_file", {"path": waiver["receipt_path"]})
    try:
        saved = json.loads(back.get("content", ""))
    except ValueError:
        saved = {}
    probe.check(
        isinstance(saved, dict)
        and saved.get("pr_number") == 7505
        and saved.get("waived_comment_ids") == ["75-05-local-1"],
        f"waiver reopened bytes must carry exact fields, got {back}",
    )
    probe.record(
        "quality waiver record",
        ("qua_waiver_record",),
        "local waiver file with exact pr/comment fields; Greptile not contacted",
    )


def _artifact_delta(probe: ReadinessProbe) -> None:
    """Real byte-delta read over owned INSTALL fixtures created in-child."""
    install = Path(probe.layout.install)
    before = install / _APK_BEFORE
    after = install / _APK_AFTER
    before.parent.mkdir(parents=True, exist_ok=True)
    before.write_bytes(b"\x50\x4b\x03\x04" + b"a" * (_APK_BEFORE_BYTES - 4))
    after.write_bytes(b"\x50\x4b\x03\x04" + b"b" * (_APK_AFTER_BYTES - 4))
    delta = _result(
        probe,
        "mob_artifact_size_delta",
        {"before_path": _APK_BEFORE, "after_path": _APK_AFTER},
    )
    probe.check(
        delta.get("before_bytes") == _APK_BEFORE_BYTES
        and delta.get("after_bytes") == _APK_AFTER_BYTES
        and delta.get("delta_bytes") == _APK_AFTER_BYTES - _APK_BEFORE_BYTES
        and delta.get("delta_pct") == 50.0,
        f"artifact delta must report exact byte fields, got {delta}",
    )
    probe.record(
        "mobile artifact size delta",
        ("mob_artifact_size_delta",),
        "owned disposable-INSTALL fixtures measured by production stat; "
        "1024 to 1536 bytes, delta 512, no store contact",
    )


_GATE_SUITE = "readiness-75-05-tiny"
_GATE_PROOF = f"{_PROFILE}/gate-proof.txt"
_REQ_FIXTURE = f"{_PROFILE}/deps/requirements.txt"
_REQ_V1 = "requests==2.31.0\n"
_REQ_V2 = "requests==2.31.0\nurllib3==2.2.1\n"
_LINT_FIXTURE = f"{_APK_DIR}/lint-baseline.xml"
_LINT_V1 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<issues>\n"
    '<issue id="UnusedResources" severity="warning" />\n'
    "</issues>\n"
)
_LINT_V2 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<issues>\n"
    '<issue id="UnusedResources" severity="warning" />\n'
    '<issue id="HardcodedText" severity="warning" />\n'
    "</issues>\n"
)
_ENT_FIXTURE = "ios/App.entitlements"
_ENT_V1 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<plist version="1.0">\n'
    "<dict>\n"
    "</dict>\n"
    "</plist>\n"
)
_ENT_V2 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<plist version="1.0">\n'
    "<dict>\n"
    "\t<key>com.apple.developer.associated-domains</key>\n"
    "\t<array>\n"
    "\t\t<string>applinks:example.com</string>\n"
    "\t</array>\n"
    "</dict>\n"
    "</plist>\n"
)
_PLIST_FIXTURE = "ios/Info.plist"
_PLIST_V1 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<plist version="1.0">\n'
    "<dict>\n"
    "\t<key>CFBundleIdentifier</key>\n"
    "\t<string>com.example.probe</string>\n"
    "</dict>\n"
    "</plist>\n"
)
_PLIST_V2 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<plist version="1.0">\n'
    "<dict>\n"
    "\t<key>CFBundleIdentifier</key>\n"
    "\t<string>com.example.probe</string>\n"
    "\t<key>NSCameraUsageDescription</key>\n"
    "\t<string>Scan codes</string>\n"
    "</dict>\n"
    "</plist>\n"
)
_BASE_REF = "HEAD~1"
_HEAD_REF = "HEAD"
_GIT_NAME = "readiness-75-05"
_GIT_EMAIL = "readiness-75-05@localhost"
_DAP_FILE = f"{_PROFILE}/dap_probe.py"
_DAP_SOURCE = "import time\ntime.sleep(30)\n"


def _gates_run(probe: ReadinessProbe) -> None:
    """Owned tiny suite: fixed interpreter argv under WORK with file proof."""
    code = (
        "import pathlib\n"
        f"marker = pathlib.Path('{_PROFILE}/baseline.txt').read_text(encoding='utf-8')\n"
        f"assert '{_MARKER}' in marker, 'baseline marker missing'\n"
        f"pathlib.Path('{_GATE_PROOF}').write_text('gate-ok\\n', encoding='utf-8')\n"
        "print('tiny-suite-ok')\n"
    )
    result = _result(
        probe,
        "qua_gates_run",
        {
            "suites": [
                {
                    "name": _GATE_SUITE,
                    "argv": [sys.executable, "-c", code],
                    "timeout": 60,
                }
            ]
        },
    )
    probe.check(
        result.get("ok") is True,
        f"qua_gates_run must pass the owned suite, got {result}",
    )
    gates = result.get("gates", {})
    probe.check(
        isinstance(gates, dict)
        and gates.get(_GATE_SUITE, {}).get("exit_code") == 0
        and "tiny-suite-ok" in gates.get(_GATE_SUITE, {}).get("output", ""),
        f"owned suite must exit 0 with its marker output, got {result}",
    )
    probe.check(
        (Path(probe.layout.work) / _GATE_PROOF).read_bytes().decode("utf-8")
        == "gate-ok\n",
        "reopened gate proof bytes must equal the suite output",
    )
    probe.record(
        "quality gates run tiny owned suite",
        ("qua_gates_run",),
        "explicit suite only: fixed interpreter argv under WORK reads the "
        "baseline marker and writes gate proof; exit/output/files asserted",
    )


def _git_run(probe: ReadinessProbe, root: str, *args: str) -> str:
    """Run fixed scenario-owned git argv via production dispatch; require exit 0."""
    out = _result(
        probe, "run_terminal", {"argv": ["git", "-C", root, *args], "cwd": "."}
    )
    probe.check(
        out.get("exit_code") == 0,
        f"git {' '.join(args)} must succeed, got {out}",
    )
    return str(out.get("stdout", ""))


def _git_commit(
    probe: ReadinessProbe, root: str, paths: list[str], message: str
) -> None:
    """Stage explicit fixture paths and commit with local identity only."""
    flags = [
        "-c",
        f"user.name={_GIT_NAME}",
        "-c",
        f"user.email={_GIT_EMAIL}",
        "-c",
        "commit.gpgsign=false",
    ]
    _git_run(probe, root, *flags, "add", "--", *paths)
    _git_run(probe, root, *flags, "commit", "-q", "-m", message)


def _git_work_ready(probe: ReadinessProbe) -> bool:
    """Build owned scratch git history in WORK; False only when git is missing."""
    if shutil.which("git") is None:
        return False
    wrote = _result(probe, "write_file", {"path": _REQ_FIXTURE, "content": _REQ_V1})
    probe.check(
        wrote.get("bytes_written") == len(_REQ_V1.encode("utf-8")),
        f"requirements fixture v1 must be written, got {wrote}",
    )
    root = str(Path(probe.layout.work))
    _git_run(probe, root, "init", "-q")
    _git_commit(probe, root, [_REQ_FIXTURE], "75-05 base fixtures")
    wrote2 = _result(probe, "write_file", {"path": _REQ_FIXTURE, "content": _REQ_V2})
    probe.check(
        wrote2.get("bytes_written") == len(_REQ_V2.encode("utf-8")),
        f"requirements fixture v2 must be written, got {wrote2}",
    )
    _git_commit(probe, root, [_REQ_FIXTURE], "75-05 head fixtures")
    probe.record(
        "scratch git history in WORK",
        ("write_file", "run_terminal"),
        "owned WORK git repo with base/head fixture commits; explicit local "
        "identity, no remotes, no user config, explicit pathspec only",
    )
    return True


def _supply_chain(probe: ReadinessProbe) -> None:
    """Real lockfile diff over the owned WORK history."""
    result = _result(
        probe,
        "qua_supply_chain_check",
        {"base_ref": _BASE_REF, "head_ref": _HEAD_REF},
    )
    probe.check(
        result.get("lockfiles_changed") == [_REQ_FIXTURE]
        and result.get("manifests_changed") == [],
        f"supply chain must flag the requirements change, got {result}",
    )
    probe.check(
        result.get("verdict") == "review"
        and result.get("unpinned") == []
        and result.get("git_dependencies") == [],
        f"supply chain verdict must be review with no flags, got {result}",
    )
    probe.record(
        "quality supply-chain lockfile diff",
        ("qua_supply_chain_check",),
        "real HEAD~1...HEAD diff over owned WORK history; pinned requirements "
        "change flagged review",
    )


def _install_git_ready(probe: ReadinessProbe) -> bool:
    """Build owned scratch git history in INSTALL; blocks the four git tools out."""
    tools = (
        "sys_design_artifact_get",
        "mob_lint_baseline_diff",
        "mob_entitlements_diff",
        "mob_review_risk_check",
    )
    if shutil.which("git") is None:
        for name in tools:
            probe.blocked(
                f"{name} needs git",
                (name,),
                ("git executable on PATH (not found in fixture)",),
                "scratch INSTALL git history cannot be built without git",
            )
        return False
    install = Path(probe.layout.install)
    for rel, text in (
        (_LINT_FIXTURE, _LINT_V1),
        (_ENT_FIXTURE, _ENT_V1),
        (_PLIST_FIXTURE, _PLIST_V1),
    ):
        target = install / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    root = str(install)
    _git_run(probe, root, "init", "-q")
    _git_commit(
        probe,
        root,
        [_LINT_FIXTURE, _ENT_FIXTURE, _PLIST_FIXTURE],
        "75-05 base fixtures",
    )
    for rel, text in (
        (_LINT_FIXTURE, _LINT_V2),
        (_ENT_FIXTURE, _ENT_V2),
        (_PLIST_FIXTURE, _PLIST_V2),
    ):
        (install / rel).write_bytes(text.encode("utf-8"))
    _git_commit(
        probe,
        root,
        [_LINT_FIXTURE, _ENT_FIXTURE, _PLIST_FIXTURE],
        "75-05 head fixtures",
    )
    probe.record(
        "scratch git history in INSTALL",
        ("run_terminal",),
        "owned INSTALL git repo with base/head fixture commits; explicit local "
        "identity, no remotes, no user config, explicit pathspec only",
    )
    return True


def _design_artifact(probe: ReadinessProbe) -> None:
    """Real file-at-ref read over the owned INSTALL history."""
    result = _result(
        probe,
        "sys_design_artifact_get",
        {"path": _LINT_FIXTURE, "ref": _HEAD_REF},
    )
    probe.check(
        result.get("path") == _LINT_FIXTURE
        and result.get("content") == _LINT_V2
        and result.get("truncated") is False,
        f"design artifact must return the head bytes, got {result}",
    )
    probe.record(
        "systems design artifact at ref",
        ("sys_design_artifact_get",),
        "real git show of the owned INSTALL history at HEAD with exact bytes",
    )


def _lint_diff(probe: ReadinessProbe) -> None:
    """Real lint-baseline growth comparison over the owned INSTALL history."""
    result = _result(
        probe,
        "mob_lint_baseline_diff",
        {
            "baseline_path": _LINT_FIXTURE,
            "base_ref": _BASE_REF,
            "head_ref": _HEAD_REF,
        },
    )
    probe.check(
        result.get("issues_before") == 1
        and result.get("issues_after") == 2
        and result.get("grew") is True
        and result.get("new_ids") == ["HardcodedText"]
        and result.get("verdict") == "a baseline that grows hides a finding",
        f"lint baseline must report exact growth fields, got {result}",
    )
    probe.record(
        "mobile lint baseline diff",
        ("mob_lint_baseline_diff",),
        "real HEAD~1...HEAD comparison: 1 to 2 issues, HardcodedText is new",
    )


def _entitlements_diff(probe: ReadinessProbe) -> None:
    """Real entitlement review over the owned INSTALL history."""
    result = _result(
        probe,
        "mob_entitlements_diff",
        {"base_ref": _BASE_REF, "head_ref": _HEAD_REF, "target_dir": "ios"},
    )
    probe.check(
        set(result.get("files", [])) == {_ENT_FIXTURE, _PLIST_FIXTURE},
        f"entitlement files must be the ios fixtures, got {result}",
    )
    probe.check(
        result.get("entitlements_added") == ["com.apple.developer.associated-domains"]
        and result.get("entitlements_removed") == []
        and result.get("usage_descriptions_added") == ["NSCameraUsageDescription"]
        and result.get("usage_descriptions_removed") == [],
        f"entitlement changes must match the fixtures, got {result}",
    )
    probe.record(
        "mobile entitlements diff",
        ("mob_entitlements_diff",),
        "real HEAD~1...HEAD comparison: one entitlement and one usage "
        "description added, none removed",
    )


def _review_risk(probe: ReadinessProbe) -> None:
    """Real App Review risk scan over the owned INSTALL history."""
    result = _result(
        probe,
        "mob_review_risk_check",
        {"base_ref": _BASE_REF, "head_ref": _HEAD_REF},
    )
    risks = result.get("risks", [])
    probe.check(
        isinstance(risks, list)
        and any(r.get("risk") == "permission" for r in risks if isinstance(r, dict)),
        f"review risk must flag the usage-description permission, got {result}",
    )
    probe.check(
        result.get("verdict") == "flag before submission",
        f"review risk verdict must flag, got {result}",
    )
    probe.record(
        "mobile review risk check",
        ("mob_review_risk_check",),
        "real HEAD~1...HEAD scan flags the added camera usage description",
    )


def _dap_stop(probe: ReadinessProbe) -> None:
    """Use a real installed DAP adapter when present; else name the gap."""
    if importlib.util.find_spec("debugpy") is None:
        probe.blocked(
            "DAP needs its adapter",
            ("dap_stop",),
            (
                "DAP adapter executable speaking DAP over stdio "
                "(debugpy adapter not installed in the fixture interpreter; "
                "checked via importlib)",
            ),
            "dap_stop spawns an external debug adapter per call; none is installed",
        )
        return
    wrote = _result(probe, "write_file", {"path": _DAP_FILE, "content": _DAP_SOURCE})
    probe.check(
        wrote.get("bytes_written") == len(_DAP_SOURCE.encode("utf-8")),
        f"DAP jail program must be written, got {wrote}",
    )
    try:
        stopped = _result(
            probe,
            "dap_stop",
            {
                "command": [sys.executable, "-m", "debugpy.adapter", "--stdio"],
                "path": _DAP_FILE,
                "line": 2,
                "timeout": 30,
            },
        )
    except Exception as exc:
        probe.check(
            "no such file" in str(exc).lower() or "not found" in str(exc).lower(),
            f"dap failure must name its cause, got {exc}",
        )
        probe.blocked(
            "DAP adapter spawn failed",
            ("dap_stop",),
            ("working debugpy DAP adapter argv",),
            f"adapter present but unusable: {exc}",
        )
        return
    probe.check(
        stopped.get("stopped") is True and isinstance(stopped.get("frames"), list),
        f"dap_stop must report the stop with frames, got {stopped}",
    )
    probe.record(
        "DAP launch-break-stop",
        ("dap_stop", "write_file"),
        "real debugpy adapter launched the jail program and stopped at the "
        "breakpoint with stack frames",
    )


def _browser_blocked(probe: ReadinessProbe) -> None:
    """Name the checked browser prerequisites; never open a socket."""
    missing = ["injected BrowserSession (platform fixture registers browser=None)"]
    if not Path("/opt/google/chrome/chrome").exists():
        missing.append("guarded-browser chrome binary (/opt/google/chrome/chrome)")
    if shutil.which("bwrap") is None:
        missing.append("guarded-browser sandbox (/usr/bin/bwrap)")
    if not Path("/sys/fs/cgroup/omega-prime-browser").exists():
        missing.append("guarded-browser cgroup (/sys/fs/cgroup/omega-prime-browser)")
    probe.blocked(
        "browser tools need their backend",
        ("browser_navigate", "browser_snapshot"),
        tuple(missing),
        "host prerequisites checked by existence lookup only; no socket "
        "opened and no page claimed without a backend",
    )


def _devices_blocked(probe: ReadinessProbe) -> None:
    """Name the checked device prerequisites; never drive host tooling."""
    listing = []
    if shutil.which("adb") is None:
        listing.append("adb CLI executable on PATH (PATH lookup found none)")
    else:
        listing.append(
            "explicit device-lab grant (host adb exists but is not driven "
            "from the readiness child)"
        )
    if shutil.which("xcrun") is None:
        listing.append("xcrun simctl executable on PATH (PATH lookup found none)")
    else:
        listing.append(
            "explicit device-lab grant (host simctl exists but is not driven "
            "from the readiness child)"
        )
    listing.append("configured Appium server (no Appium backend injected)")
    listing.append("device vision transport for review (None in fixture)")
    probe.blocked(
        "mobile device listing needs host tooling",
        ("mob_device_list", "mob_device_review"),
        tuple(listing),
        "device executables checked by PATH lookup only; nothing executed, "
        "no service contacted",
    )
    probe.blocked(
        "mobile device act needs backend and gate",
        ("mob_device_act",),
        ("configured Appium backend", "mob_device_act approval"),
        "action path needs a real device backend; screenshot-only backends refuse acts",
    )


_LEAD_RETAIN = f"{_MARKER} retained note"
_EVENT_KIND = "75-05-note"
_SCAN_FIXTURE = f"{_APK_DIR}/scan.txt"
_SCAN_TEXT = "75-05 bundle scan fixture\nsecond clean line\n"
_POLICY_REL = "omega_prime/contracts/policies/omega-prime.json"
_TOKEN_VARS = (
    "X_API_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "RAILWAY_TOKEN",
    "VERCEL_TOKEN",
    "GREPTILE_API_KEY",
    "PLAY_CONSOLE_TOKEN",
    "ASC_KEY_ID",
    "ASC_ISSUER_ID",
    "ASC_PRIVATE_KEY",
    "OMEGA_PRIME_DESK_DOCS_INDEX",
    "OMEGA_PRIME_DESK_BUS_URL",
    "OMEGA_PRIME_PACKS_JSON",
    "OMEGA_PRIME_PACK_API_BASES_JSON",
    "SUBSTRATE_TOKEN",
    "SUBSTRATE_TOKEN_GROK_BOT",
)


def _lead_effects(probe: ReadinessProbe) -> None:
    """Real lead coordination: brief, ownership, receipt-backed retain, stored event."""
    brief = _result(probe, "lead_brief", {})
    probe.check(
        brief.get("seat") == "Omega Prime"
        and isinstance(brief.get("recall"), dict)
        and isinstance(brief.get("intake_queue"), dict)
        and isinstance(brief.get("reminders"), list),
        f"lead_brief must return the seat shape, got {brief}",
    )
    probe.record(
        "lead seat brief",
        ("lead_brief",),
        "live recall+packs+intake+reminders from the disposable stores",
    )
    owned = _result(
        probe, "lead_ownership_resolve", {"paths": ["omega_prime/tools/quality.py"]}
    )
    results = owned.get("results", [])
    probe.check(
        len(results) == 1
        and results[0].get("owner") == "bot-00-omega-prime"
        and results[0].get("mine") is True
        and results[0].get("unowned") is False,
        f"ownership must resolve to the seat bot, got {owned}",
    )
    probe.record(
        "lead ownership resolution",
        ("lead_ownership_resolve",),
        "shipped ownership.yaml resolves the probe path to bot-00-omega-prime",
    )
    retained = _result(
        probe,
        "lead_memory_retain",
        {
            "content": _LEAD_RETAIN,
            "receipt_path": _RECEIPT_FILE,
            "source": "75-05-baseline",
        },
    )
    probe.check(
        retained.get("ok") is True,
        f"lead_memory_retain must persist with receipt evidence, got {retained}",
    )
    recall = _result(probe, "lead_memory_recall", {"query": _LEAD_RETAIN})
    probe.check(
        any(
            _LEAD_RETAIN in entry.get("content", "")
            for entry in recall.get("results", [])
            if isinstance(entry, dict)
        ),
        f"retained entry must be recallable, got {recall}",
    )
    probe.record(
        "lead memory retain with receipt evidence",
        ("lead_memory_retain", "lead_memory_recall"),
        "receipt-backed retain persisted to the shared store and recalled",
    )
    emitted = probe.emulator.execute_tool(
        "lead_event_emit",
        {"kind": _EVENT_KIND, "payload": {"marker": _MARKER}},
    )
    outcome = emitted.get("result") if isinstance(emitted.get("result"), dict) else {}
    probe.check(
        emitted.get("status") == "error" and outcome.get("local_mirror") is True,
        f"event must degrade to the local mirror, got {emitted}",
    )
    mirrors = list(Path(probe.layout.state).rglob("events.ndjson"))
    probe.check(bool(mirrors), "events mirror file must exist under disposable state")
    blob = "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in mirrors
    )
    probe.check(
        _EVENT_KIND in blob and _MARKER in blob,
        "stored event must carry the kind and marker",
    )
    probe.record(
        "lead event local-mirror control",
        (),
        "control only: external delivery refused upstream while the NDJSON "
        "mirror effect was verified above; not delivered-tool evidence",
    )
    probe.blocked(
        "lead event delivery needs substrate",
        ("lead_event_emit",),
        (
            "reachable substrate service (SUBSTRATE_URL plane + SUBSTRATE_TOKEN; "
            "default http://127.0.0.1:7410 off-limits, never contacted)",
        ),
        "invocation failed upstream with only the local mirror stored",
    )


def _service_blocks(probe: ReadinessProbe) -> None:
    """Verified credential gaps plus reachable credential-free reads."""
    env = probe.layout.env
    for var in _TOKEN_VARS:
        probe.check(
            not env.get(var),
            f"fixture must not inherit {var}",
        )
    for name, args in (
        ("telegram_updates", {}),
        ("discord_read", {"channel_id": 1}),
    ):
        refused = probe.emulator.execute_tool(name, args)
        probe.check(
            refused.get("status") == "error"
            and "credential" in str(refused.get("error", "")).lower(),
            f"{name} must refuse without credential, got {refused}",
        )
    probe.record(
        "service credential refusal shapes",
        (),
        "negative coverage only: telegram/discord resolve the blank credential "
        "before any client or transport use (source: _resolve_token first in "
        "_run); x_* refusal wording is not asserted because x_mentions has no "
        "clean preflight (None transport raises inside the handler), so all "
        "five x tools stay blocked on the absent X_API_TOKEN",
    )
    probe.blocked(
        "x reads need token",
        ("x_mentions", "x_read_post"),
        ("X_API_TOKEN credential (absent in fixture)",),
        "no X client identity wired; reads never attempted, nothing fetched",
    )
    probe.blocked(
        "x posts need token and gate",
        ("x_post", "x_post_thread"),
        (
            "X_API_TOKEN credential (absent in fixture)",
            "x_post/x_post_thread approvals",
        ),
        "publishing stays out of this slice; nothing posted",
    )
    probe.blocked(
        "x media needs token",
        ("x_upload_media",),
        ("X_API_TOKEN credential (absent in fixture)",),
        "no upload staged",
    )
    probe.blocked(
        "telegram reads need token",
        ("telegram_updates",),
        ("TELEGRAM_BOT_TOKEN credential (absent in fixture)",),
        "no bot identity wired; refusal shape kept as control-only negative, "
        "reads never performed",
    )
    probe.blocked(
        "telegram send needs token and gate",
        ("telegram_send",),
        (
            "TELEGRAM_BOT_TOKEN credential (absent in fixture)",
            "telegram_send approval",
        ),
        "nothing sent",
    )
    probe.blocked(
        "discord reads need token",
        ("discord_read",),
        ("DISCORD_BOT_TOKEN credential (absent in fixture)",),
        "no bot identity wired; refusal shape kept as control-only negative, "
        "reads never performed",
    )
    probe.blocked(
        "discord send needs token and gate",
        ("discord_send",),
        (
            "DISCORD_BOT_TOKEN credential (absent in fixture)",
            "discord_send approval",
        ),
        "nothing sent",
    )
    probe.blocked(
        "lead docs search needs its index",
        ("lead_docs_search",),
        ("docs index endpoint (OMEGA_PRIME_DESK_DOCS_INDEX unset in fixture)",),
        "no docs plane wired",
    )
    probe.blocked(
        "lead graph needs substrate",
        ("lead_graph_register", "lead_graph_state"),
        (
            "reachable substrate service (SUBSTRATE_URL plane + SUBSTRATE_TOKEN; "
            "default http://127.0.0.1:7410 off-limits, never contacted)",
            "lead_graph_register/lead_graph_state approvals",
        ),
        "pure substrate passthrough with no local effect; nothing claimed",
    )
    probe.blocked(
        "lead bus needs its endpoint",
        ("lead_bus_start", "lead_bus_wait"),
        (
            "agent-bus endpoint (OMEGA_PRIME_DESK_BUS_URL unset in fixture)",
            "lead_bus_start approval",
        ),
        "no bus client wired",
    )
    probe.blocked(
        "vercel reads need token",
        ("web_vercel_deployments",),
        ("VERCEL_TOKEN credential (absent in fixture)",),
        "no Vercel client wired",
    )
    probe.blocked(
        "vercel writes need token and gate",
        ("web_vercel_promote", "web_vercel_rollback"),
        (
            "VERCEL_TOKEN credential (absent in fixture)",
            "web_vercel_promote/web_vercel_rollback approvals",
        ),
        "no mutation attempted",
    )
    policy = Path(probe.layout.install) / _POLICY_REL
    if policy.is_file():
        try:
            network = json.loads(policy.read_text(encoding="utf-8")).get("network", {})
            hosts = network.get("hosts", None) if isinstance(network, dict) else None
        except ValueError:
            hosts = None
        probe.check(
            hosts == [],
            "seat policy must allow no outbound hosts",
        )
    probe.blocked(
        "preview check needs egress",
        ("web_preview_check",),
        (
            "bound preview operation for the target URL",
            "permitted outbound egress (seat policy network hosts empty)",
        ),
        "fetch would leave the seat; never attempted",
    )
    probe.blocked(
        "review page needs session",
        ("web_review_page",),
        (
            "target-URL connectivity (no permitted egress)",
            "guarded browser session (no backend)",
            "vision transport (None in fixture)",
        ),
        "render path needs all three; never launched",
    )
    install = Path(probe.layout.install)
    scan_target = install / _SCAN_FIXTURE
    scan_target.parent.mkdir(parents=True, exist_ok=True)
    scan_target.write_bytes(_SCAN_TEXT.encode("utf-8"))
    scan = _result(probe, "web_bundle_secret_scan", {"paths": [_SCAN_FIXTURE]})
    probe.check(
        scan.get("ok") is True
        and scan.get("gate") == "G-3"
        and scan.get("findings") == [],
        f"bundle scan must report the clean fixture, got {scan}",
    )
    probe.record(
        "web bundle secret scan",
        ("web_bundle_secret_scan",),
        "local G-3 scan of the owned INSTALL fixture; clean",
    )
    probe.blocked(
        "railway reads need token",
        (
            "infra_railway_status",
            "infra_railway_logs",
            "infra_railway_variable_names",
        ),
        ("RAILWAY_TOKEN credential (absent in fixture)",),
        "no Railway client wired",
    )
    probe.blocked(
        "railway redeploy needs token and gate",
        ("infra_railway_redeploy",),
        (
            "RAILWAY_TOKEN credential (absent in fixture)",
            "infra_railway_redeploy approval",
        ),
        "no mutation attempted",
    )
    if shutil.which("tailscale") is None:
        tailscale = ("tailscale CLI executable on PATH (PATH lookup found none)",)
    else:
        tailscale = (
            "explicit host-inventory grant (tailscale exists but host "
            "tailnet state is out of scope)",
        )
    probe.blocked(
        "tailscale needs its CLI",
        ("infra_tailscale_status",),
        tailscale,
        "tailnet state checked by PATH lookup only; status never executed",
    )
    if shutil.which("systemctl") is None:
        systemd = ("systemctl executable on PATH (PATH lookup found none)",)
    else:
        systemd = (
            "explicit host-inventory grant (systemctl exists but host unit "
            "state is out of scope)",
        )
    probe.blocked(
        "vps units need systemd host",
        ("infra_vps_units",),
        systemd,
        "unit states checked by PATH lookup only; host never queried",
    )
    health = _result(probe, "infra_db_health", {})
    probe.check(
        health.get("summary") == {}
        and health.get("checks") == {}
        and "no health checks" in str(health.get("note", "")),
        f"db health must report no configured checks, got {health}",
    )
    probe.record(
        "infra db health empty",
        ("infra_db_health",),
        "credential-free read truthfully reports no configured checks",
    )
    ultra = env.get("ULTRATHINK_ROOT", "")
    probe.check(
        ultra == str(Path(probe.layout.base) / "ultrathink"),
        f"ULTRATHINK_ROOT must be the owned fixture root, got {ultra!r}",
    )
    ultra_bin = Path(ultra) / "bin" if ultra else None
    probe.check(
        ultra_bin is not None and not (ultra_bin / "ultrathink").exists(),
        "owned ultrathink root must carry no checkout CLI",
    )
    probe.check(
        ultra_bin is not None and not (ultra_bin / "ultrathink-mcp").exists(),
        "owned ultrathink root must carry no mcp CLI",
    )
    probe.check(
        ultra_bin is not None and not (ultra_bin / "ultrathink-ship").exists(),
        "owned ultrathink root must carry no ship CLI",
    )
    refused_ult = probe.emulator.execute_tool("ult_status", {})
    probe.check(
        refused_ult.get("status") == "error"
        and "ultrathink exited 127" in str(refused_ult.get("error", "")),
        f"ult_status must report the missing-binary spawn failure, got {refused_ult}",
    )
    probe.record(
        "ultrathink unconfigured shape",
        (),
        "negative coverage only: status fails at spawn before any shell-out",
    )
    probe.blocked(
        "ultrathink status needs its CLI",
        ("ult_status",),
        (
            "ultrathink CLI (bin/ultrathink under the owned root; absent, "
            "verified by existence lookup)",
        ),
        "checkout root is owned but empty; nothing shelled out",
    )
    probe.blocked(
        "ultrathink tracker verbs need CLI and state",
        ("ult_track_complete", "ult_session_mark"),
        (
            "ultrathink-mcp CLI (bin/ultrathink-mcp under the owned root; "
            "absent, verified by existence lookup)",
            "session state file for the verb",
            "ult_track_complete/ult_session_mark approvals",
        ),
        "no CLI, no session state; nothing shelled out",
    )
    probe.blocked(
        "ultrathink assess needs CLI and state",
        ("ult_ship_assess",),
        (
            "ultrathink-ship CLI (bin/ultrathink-ship under the owned root; "
            "absent, verified by existence lookup)",
            "session state file for the verb",
        ),
        "no CLI, no session state; nothing shelled out",
    )
    probe.blocked(
        "ultrathink ship writes need CLI and remote auth",
        ("ult_ship_pr", "ult_ship_review", "ult_ship_merge"),
        (
            "ultrathink-ship CLI (bin/ultrathink-ship under the owned root; "
            "absent, verified by existence lookup)",
            "session state file for the verb",
            "remote PR auth for push (ship_pr)",
            "Greptile review auth (ship_review)",
            "merge policy auth (ship_merge)",
            "ult_ship_pr/ult_ship_review/ult_ship_merge approvals",
        ),
        "no CLI, no session state, no remote auth; no mutation attempted",
    )
    probe.blocked(
        "substrate needs its service",
        (
            "substrate_docs_search",
            "substrate_graph_claim",
            "substrate_graph_release",
            "substrate_graph_complete",
            "substrate_graph_heartbeat",
        ),
        (
            "reachable substrate service (SUBSTRATE_URL plane + "
            "SUBSTRATE_TOKEN/SUBSTRATE_TOKEN_GROK_BOT; default "
            "http://127.0.0.1:7410 off-limits, never contacted)",
            "approvals for graph_claim/release/complete",
        ),
        "no live plane; docs/graph verbs never sent",
    )


def _sys_lsp(probe: ReadinessProbe) -> None:
    binary = shutil.which("pyright-langserver")
    if binary is None:
        probe.blocked(
            "systems LSP needs its executable",
            ("sys_lsp_diagnostics",),
            ("pyright-langserver --stdio executable on PATH",),
            "no language server binary is available in the fixture",
        )
        return
    candidates = [
        "omega_prime/tools/todo.py",
        "omega_prime/tools/clarify.py",
        "omega_prime/__init__.py",
    ]
    target = next(
        (rel for rel in candidates if (Path(probe.layout.install) / rel).is_file()),
        None,
    )
    if target is None:
        probe.blocked(
            "systems LSP needs a matching install file",
            ("sys_lsp_diagnostics",),
            ("matching python file under the systems root (INSTALL)",),
            "no candidate install file exists in the exported tree",
        )
        return
    try:
        found = _result(
            probe,
            "sys_lsp_diagnostics",
            {
                "command": [binary, "--stdio"],
                "path": target,
                "language": "python",
                "timeout": 30,
            },
        )
    except Exception as exc:
        probe.check(
            "no such file" in str(exc).lower() or "not found" in str(exc).lower(),
            f"sys_lsp failure must name its cause, got {exc}",
        )
        probe.blocked(
            "systems LSP executable missing at spawn",
            ("sys_lsp_diagnostics",),
            ("pyright-langserver --stdio executable on PATH",),
            f"spawn failed: {exc}",
        )
        return
    probe.check(
        found.get("path") == target and isinstance(found.get("diagnostics"), list),
        f"sys_lsp_diagnostics must return path/diagnostics, got {found}",
    )
    probe.record(
        "systems LSP diagnostics on install file",
        ("sys_lsp_diagnostics",),
        f"real {binary} --stdio diagnostics for {target} inside the systems root",
    )


def _subprocess_receipts(probe: ReadinessProbe) -> None:
    term_argv = [sys.executable, "-c", "print('omega-prime-75-05-terminal')"]
    term = _result(probe, "run_terminal", {"argv": term_argv})
    probe.check(
        term.get("exit_code") == 0
        and "omega-prime-75-05-terminal" in term.get("stdout", ""),
        f"run_terminal must run the allowlisted argv, got {term}",
    )
    exec_code = "print('omega-prime-75-05-exec')"
    executed = _result(probe, "execute_code", {"code": exec_code})
    probe.check(
        executed.get("exit_code") == 0
        and "omega-prime-75-05-exec" in executed.get("stdout", ""),
        f"execute_code must run the fixed snippet, got {executed}",
    )
    rows = _read_command_log(probe)
    term_cmd = " ".join(term_argv)
    exec_cmd = " ".join((sys.executable, "-c", exec_code))
    term_row = next(
        (
            row
            for row in rows
            if row.get("cmd") == term_cmd and row.get("exit_code") == 0
        ),
        None,
    )
    exec_row = next(
        (
            row
            for row in rows
            if row.get("cmd") == exec_cmd and row.get("exit_code") == 0
        ),
        None,
    )
    probe.check(term_row is not None, "command log must hold the captured terminal row")
    probe.check(exec_row is not None, "command log must hold the captured execute row")
    assert term_row is not None and exec_row is not None
    receipt = {
        "commands": [
            {
                "cmd": term_row["cmd"],
                "exit_code": term_row["exit_code"],
                "output_tail": str(term_row.get("output_tail", "")),
            },
            {
                "cmd": exec_row["cmd"],
                "exit_code": exec_row["exit_code"],
                "output_tail": str(exec_row.get("output_tail", "")),
            },
        ],
        "claims": [
            {
                "claim": "allowlisted terminal argv executed",
                "evidence_command_index": 0,
            },
            {
                "claim": "fixed interpreter snippet executed",
                "evidence_command_index": 1,
            },
        ],
        "unverified": [],
    }
    verdict = _result(probe, "lead_receipt_check", {"receipt": receipt})
    probe.check(
        verdict.get("ok") is True,
        f"lead_receipt_check must accept the captured receipt, got {verdict}",
    )
    probe.record(
        "subprocess execution with captured receipt proof",
        ("run_terminal", "execute_code", "lead_receipt_check"),
        "local-subprocess-proof: fixed allowlist (current interpreter only, no shell, "
        "no OS-sandbox claim); receipt built from reopened command-log rows only",
    )


def _read_command_log(probe: ReadinessProbe) -> list[dict[str, Any]]:
    log_path = Path(probe.layout.command_log)
    probe.check(log_path.is_file(), "command log file must exist in the layout")
    rows: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _mcp_interop(probe: ReadinessProbe) -> None:
    home = Path(probe.layout.home)
    script_rel = _MCP_SCRIPT
    script_text = (
        "from __future__ import annotations\n"
        "\n"
        "import asyncio\n"
        "from pathlib import Path\n"
        "\n"
        "from mcp.server import MCPServer\n"
        "\n"
        f"HOME_ROOT = Path({str(home)!r})\n"
        "\n"
        'server = MCPServer("readiness-75-05-proof")\n'
        "\n"
        "@server.tool()\n"
        'def local_roundtrip(path: str, content: str = "") -> str:\n'
        "    target = Path(path)\n"
        "    try:\n"
        "        resolved = target.resolve()\n"
        "    except OSError as exc:\n"
        '        raise ValueError(f"unresolvable path: {exc}") from exc\n'
        "    if resolved != HOME_ROOT and HOME_ROOT not in resolved.parents:\n"
        '        raise ValueError("path escapes the disposable HOME fixture root")\n'
        '    if content != "":\n'
        "        resolved.parent.mkdir(parents=True, exist_ok=True)\n"
        '        resolved.write_bytes(content.encode("utf-8"))\n'
        '    return resolved.read_bytes().decode("utf-8")\n'
        "\n"
        "asyncio.run(server.run_stdio_async())\n"
    )
    uploaded = _result(
        probe, "write_file", {"path": script_rel, "content": script_text}
    )
    probe.check(
        uploaded.get("bytes_written") == len(script_text.encode("utf-8")),
        f"MCP fixture script must be written, got {uploaded}",
    )
    script_back = _result(probe, "read_file", {"path": script_rel})
    probe.check(
        script_back.get("content") == script_text,
        "MCP fixture script reopened bytes must match",
    )
    probe.check(
        "from mcp.server import MCPServer" in script_text,
        "fixture must use the official SDK import, never mcp_v2 or FastMCP",
    )
    script_abs = str(Path(probe.layout.work) / script_rel)
    home_file = str(home / _MCP_HOME_FILE)
    if (home / _MCP_HOME_FILE).exists():
        (home / _MCP_HOME_FILE).unlink()
    wrote = _mcp_text(
        probe,
        [sys.executable, script_abs],
        {"path": home_file, "content": _MCP_CONTENT},
    )
    probe.check(
        wrote == _MCP_CONTENT,
        "mcp_call write must return the roundtripped text",
    )
    probe.check(
        (home / _MCP_HOME_FILE).read_bytes().decode("utf-8") == _MCP_CONTENT,
        "HOME-tree fixture reopened bytes must equal the written payload",
    )
    reread = _mcp_text(probe, [sys.executable, script_abs], {"path": home_file})
    probe.check(
        reread == _MCP_CONTENT,
        "mcp_call read must return the persisted payload",
    )
    probe.record(
        "canonical MCP interop through public dispatch",
        ("mcp_call",),
        "official-SDK interop: owned MCPServer (from mcp.server import MCPServer) "
        "over the canonical initializing session client; real HOME-tree file "
        "write/read verified by reopened bytes and result fields; no echo "
        "framing, no handshake bypass",
    )


def _mcp_text(
    probe: ReadinessProbe, command: list[str], arguments: dict[str, Any]
) -> str:
    envelope = probe.call(
        "mcp_call", {"command": command, "tool": _MCP_TOOL, "arguments": arguments}
    )
    payload = envelope["result"]
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    probe.check(
        isinstance(payload, dict) and isinstance(payload.get("content"), list),
        f"mcp_call must return session content blocks, got {payload}",
    )
    assert isinstance(payload, dict)
    blocks = [b for b in payload["content"] if isinstance(b, dict) and "text" in b]
    probe.check(bool(blocks), f"mcp_call content must hold text, got {payload}")
    text = blocks[0]["text"]
    probe.check(isinstance(text, str), f"mcp_call text must be a string, got {payload}")
    assert isinstance(text, str)
    return text


def _ide_browser_devices(probe: ReadinessProbe) -> None:
    wrote = _result(probe, "write_file", {"path": _LSP_FILE, "content": _LSP_SOURCE})
    probe.check(
        wrote.get("bytes_written") == len(_LSP_SOURCE.encode("utf-8")),
        f"LSP jail file must be written, got {wrote}",
    )
    binary = shutil.which("pyright-langserver")
    if binary is None:
        probe.blocked(
            "IDE LSP needs its executable",
            ("lsp_diagnostics",),
            ("pyright-langserver --stdio executable on PATH",),
            "no language server binary is available in the fixture",
        )
    else:
        try:
            found = _result(
                probe,
                "lsp_diagnostics",
                {
                    "command": [binary, "--stdio"],
                    "path": _LSP_FILE,
                    "language_id": "python",
                    "timeout": 30,
                },
            )
        except Exception as exc:
            probe.check(
                "no such file" in str(exc).lower() or "not found" in str(exc).lower(),
                f"lsp failure must name its cause, got {exc}",
            )
            probe.blocked(
                "IDE LSP executable missing at spawn",
                ("lsp_diagnostics",),
                ("pyright-langserver --stdio executable on PATH",),
                f"spawn failed: {exc}",
            )
        else:
            probe.check(
                found.get("path") == _LSP_FILE
                and isinstance(found.get("diagnostics"), list),
                f"lsp_diagnostics must return path/diagnostics, got {found}",
            )
            probe.record(
                "IDE LSP diagnostics on jail file",
                ("lsp_diagnostics",),
                f"real {binary} --stdio diagnostics for the matching WORK jail file",
            )
    probe.record(
        "IDE LSP jail fixture",
        ("write_file",),
        "LSP jail python file written under scratch WORK for the diagnostics run",
    )
    _dap_stop(probe)
    _browser_blocked(probe)


def _negatives(probe: ReadinessProbe) -> None:
    owned_outside = str(Path(probe.layout.state) / "escape-75-05.txt")
    refused = probe.emulator.execute_tool(
        "write_file", {"path": owned_outside, "content": "must not land"}
    )
    probe.check(
        refused.get("status") == "error" and "error" in refused,
        f"workspace escape must be refused, got {refused}",
    )
    probe.check(
        not Path(owned_outside).exists(),
        "escape refusal must leave no mutation at the owned outside-WORK path",
    )
    probe.record(
        "workspace escape refusal",
        (),
        "negative coverage only: absolute owned path outside WORK refused, "
        "no mutation, no shared temp touched",
    )

    unconfigured = probe.emulator.execute_tool("web_search", {"query": "omega prime"})
    probe.check(
        unconfigured.get("status") == "error"
        and "not configured" in str(unconfigured.get("error", "")).lower(),
        f"unconfigured web_search must report its shape, got {unconfigured}",
    )
    probe.record(
        "unconfigured transport refusal shape",
        (),
        "negative coverage only: configuration-error shape asserted, never exercise",
    )
    probe.blocked(
        "web tools need their transport",
        ("web_search", "web_extract"),
        ("configured web transport (None in fixture)",),
        "no default network client; refusal above is negative coverage only",
    )
    probe.blocked(
        "vision needs its transport",
        ("vision_analyze",),
        ("configured vision transport (None in fixture)",),
        "no default vision client and no image download in this fixture",
    )

    clarified = _result(probe, "clarify", {"question": "75-05 baseline ready?"})
    probe.check(
        clarified.get("question") == "75-05 baseline ready?",
        f"clarify must echo the recorded question, got {clarified}",
    )
    probe.record(
        "clarify effect-free echo",
        ("clarify",),
        "question recorded and returned unchanged; no user interrupt in this slice",
    )

    bad_receipt = {
        "commands": [
            {
                "cmd": "omega-prime-75-05-never-ran",
                "exit_code": 0,
                "output_tail": "",
            }
        ],
        "claims": [{"claim": "uncaptured work", "evidence_command_index": 0}],
        "unverified": [],
    }
    rejected = probe.emulator.execute_tool(
        "lead_receipt_check", {"receipt": bad_receipt}
    )
    outcome = rejected.get("result") if isinstance(rejected.get("result"), dict) else {}
    probe.check(
        isinstance(outcome, dict)
        and outcome.get("ok") is False
        and "not captured" in json.dumps(outcome.get("problems", [])),
        f"uncaptured receipt must be rejected, got {rejected}",
    )
    probe.record(
        "uncaptured-command receipt rejection",
        (),
        "negative coverage only: receipt citing a never-captured command is refused",
    )
