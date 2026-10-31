"""Phase 31: quality tool family behind fakes."""

from __future__ import annotations

import json
from typing import Any

from omega_prime.receipts import append_execution
from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.quality import (
    QUALITY_TOOL_NAMES,
    AckStore,
    QualityClient,
    QualityContext,
    register_quality_tools,
)
from omega_prime.tools.registry import ToolRegistry


class FakeRunner:
    def __init__(self, exits=None):
        self.exits = exits if exits is not None else {}
        self.calls: list[list] = []
        self.cwds: list = []

    def __call__(self, argv, timeout=120, cwd=None):
        self.calls.append(argv)
        self.cwds.append(cwd)
        code = self.exits.get(argv[3] if len(argv) > 3 else "", 0)
        return {"exit_code": code, "stdout": "out", "stderr": ""}


class FakeGreptile:
    def __init__(self):
        self.calls: list[tuple] = []

    def trigger(self, repo, pr):
        self.calls.append(("trigger", repo, pr))
        return {"body": {"review_id": "r-1"}}

    def get(self, review_id):
        self.calls.append(("get", review_id))
        return {"body": {"review_id": review_id, "conclusion": "pass"}}

    def comments(self, repo, pr):
        self.calls.append(("comments", repo, pr))
        return {"body": [{"id": "c-1", "addressed": True}]}


def _ctx(tmp_path, **overrides):
    (tmp_path / "omega_prime" / "tests").mkdir(parents=True, exist_ok=True)
    base: dict[str, Any] = dict(
        root=str(tmp_path),
        run=FakeRunner(),
        greptile=FakeGreptile(),
        acks=AckStore(tmp_path / "acks.json"),
    )
    base.update(overrides)
    return QualityContext(**base)


def _receipt(bot="bot-01-systems"):
    return {
        "bot": bot,
        "commands": [{"cmd": "pytest", "exit_code": 0}],
        "claims": [{"claim": "done", "evidence_command_index": 0}],
        "unverified": [],
    }


def test_gates_run_aggregates(tmp_path):
    run = FakeRunner()
    client = QualityClient(_ctx(tmp_path, run=run))
    result = client.gates_run()
    assert result["ok"] is True
    assert sorted(result["gates"]) == ["assemble", "evals", "suite"]
    assert run.cwds == [str(tmp_path)] * 3


def test_gates_run_failure(tmp_path):
    run = FakeRunner({"omega_prime/evals/cases": 1})
    client = QualityClient(_ctx(tmp_path, run=run))
    result = client.gates_run()
    assert result["ok"] is False
    assert result["gates"]["evals"]["exit_code"] == 1
    assert result["gates"]["suite"]["exit_code"] == 0


def test_greptile_actions(tmp_path):
    greptile = FakeGreptile()
    client = QualityClient(_ctx(tmp_path, greptile=greptile))
    triggered = client.greptile_review("trigger", pr_number=3)
    assert triggered["ok"] is True
    assert "queued" in triggered["note"]
    assert greptile.calls[0] == ("trigger", "swcstudiospace/Omega-Prime", 3)
    got = client.greptile_review("get", review_id="r-1")
    assert got["result"]["conclusion"] == "pass"
    comments = client.greptile_review("comments", pr_number=3)
    assert comments["note"].startswith("comments")
    bad = QualityClient(_ctx(tmp_path, greptile=None)).greptile_review(
        "get", review_id="r-1"
    )
    assert "not_configured" in bad["error"]


def test_receipt_approve_stamps(tmp_path, monkeypatch):
    monkeypatch.setenv("OMEGA_PRIME_COMMAND_LOG", str(tmp_path / "command-log.jsonl"))
    append_execution("pytest", 0, "", cwd=str(tmp_path))
    target = tmp_path / "plan.json"
    target.write_text(json.dumps(_receipt()), encoding="utf-8")
    client = QualityClient(_ctx(tmp_path))
    result = client.receipt_approve("plan.json", note="looks good")
    assert result == {
        "ok": True,
        "receipt_path": "plan.json",
        "approved_by": "bot-00-omega-prime",
        "pushed": False,
        "reason": result["reason"],
    }
    stamped = json.loads(target.read_text(encoding="utf-8"))
    assert stamped["approved_by"] == "bot-00-omega-prime"
    assert stamped["approved_at"]
    assert stamped["approval_note"] == "looks good"


def test_receipt_approve_refusals(tmp_path):
    client = QualityClient(_ctx(tmp_path))
    own = tmp_path / "own.json"
    own.write_text(json.dumps(_receipt(bot="bot-00-omega-prime")), encoding="utf-8")
    assert "self_approval" in client.receipt_approve("own.json")["error"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"bot": "x"}), encoding="utf-8")
    refused = client.receipt_approve("bad.json")
    assert "gate_failed" in refused["error"]
    assert refused["problems"]
    assert "not_found" in client.receipt_approve("missing.json")["error"]
    assert "invalid_args" in client.receipt_approve("../escape.json")["error"]
    anon = tmp_path / "anon.json"
    anon.write_text(
        json.dumps({k: v for k, v in _receipt().items() if k != "bot"}),
        encoding="utf-8",
    )
    assert "invalid_receipt" in client.receipt_approve("anon.json")["error"]


def test_waiver_record(tmp_path):
    client = QualityClient(_ctx(tmp_path))
    result = client.waiver_record(9, ["c-1"], "false positive", branch="v5")
    assert result["ok"] is True
    assert result["pushed"] is False
    waiver = json.loads((tmp_path / result["receipt_path"]).read_text(encoding="utf-8"))
    assert waiver["pr_number"] == 9
    assert waiver["waived_comment_ids"] == ["c-1"]
    assert waiver["branch"] == "v5"
    assert waiver["unverified"]


def test_contract_ack_lifecycle(tmp_path):
    client = QualityClient(_ctx(tmp_path))
    acked = client.contract_ack("c-1", True, "fine")
    assert acked["ok"] is True
    short = client.contract_ack("c-1", False, "no")
    assert "invalid_args" in short["error"]
    rejected = client.contract_ack("c-1", False, "breaks the deploy contract badly")
    assert rejected["ok"] is True
    assert len(rejected["acknowledgements"]) == 2
    status = client.contract_ack_status("c-1")
    assert status["acknowledged"] == ["bot-00-omega-prime"]
    assert status["rejected"]
    assert status["complete"] is False


def test_contract_ack_status_coverage(tmp_path):
    changes = tmp_path / "contracts" / "changes"
    changes.mkdir(parents=True)
    (changes / "c-2.json").write_text(
        json.dumps({"consumers_required": ["bot-00-omega-prime"]}), encoding="utf-8"
    )
    client = QualityClient(_ctx(tmp_path))
    before = client.contract_ack_status("c-2")
    assert before["missing"] == ["bot-00-omega-prime"]
    assert before["complete"] is False
    client.contract_ack("c-2", True, "fine")
    after = client.contract_ack_status("c-2")
    assert after["missing"] == []
    assert after["complete"] is True


def test_contract_ack_status_reads_yaml_proposal(tmp_path):
    changes = tmp_path / "contracts" / "changes"
    changes.mkdir(parents=True)
    (changes / "c-3.yaml").write_text(
        "change_id: c-3\nbreaking: false\nconsumers_required:\n"
        '  - bot-00-omega-prime\n  - "bot:with-colon"\nacknowledgements: []\n',
        encoding="utf-8",
    )
    client = QualityClient(_ctx(tmp_path))
    before = client.contract_ack_status("c-3")
    assert before["missing"] == ["bot-00-omega-prime", "bot:with-colon"]
    assert before["complete"] is False
    client.contract_ack("c-3", True, "fine")
    after = client.contract_ack_status("c-3")
    assert after["missing"] == ["bot:with-colon"]
    assert after["complete"] is False


def test_supply_chain_check(tmp_path):
    vcs = {
        "diff_names": lambda base, head: ["pyproject.toml", "uv.lock", "main.py"],
        "diff": lambda base, head, paths: (
            '+dep = "latest"\n+other = "1.0"\n+git+https://x\n'
        ),
    }
    client = QualityClient(_ctx(tmp_path, vcs=vcs))
    result = client.supply_chain_check("a", "b")
    assert result["lockfiles_changed"] == ["uv.lock"]
    assert result["manifests_changed"] == ["pyproject.toml"]
    assert result["unpinned"] == ['dep = "latest"']
    assert result["git_dependencies"] == ["git+https://x"]
    assert result["verdict"] == "review"
    assert result["unverified"]


def test_secret_scan(tmp_path):
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "dirty.py").write_text(
        'token = "ghp_' + "x" * 36 + '"\n', encoding="utf-8"
    )
    client = QualityClient(_ctx(tmp_path))
    flagged = client.secret_scan(["dirty.py", "clean.py"])
    assert flagged["ok"] is False
    assert flagged["gate"] == "G-3"
    assert flagged["findings"] == [{"path": "dirty.py", "line": 1}]
    clean = client.secret_scan(["clean.py"])
    assert clean["ok"] is True
    assert "invalid_args" in client.secret_scan(["../x"])["error"]


def test_secret_scan_default_skips_generated(tmp_path):
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "dep.py").write_text('token = "ghp_' + "x" * 36 + '"\n', encoding="utf-8")
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    client = QualityClient(_ctx(tmp_path))
    result = client.secret_scan()
    assert result["ok"] is True
    assert result["truncated"] is False
    assert result["findings"] == []
    (tmp_path / "src.py").write_text(
        'token = "ghp_' + "x" * 36 + '"\n', encoding="utf-8"
    )
    flagged = client.secret_scan()
    assert flagged["ok"] is False
    assert flagged["findings"] == [{"path": "src.py", "line": 1}]


def test_register_quality_tools_approval(tmp_path):
    vcs = {"diff_names": lambda base, head: [], "diff": lambda base, head, paths: ""}
    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    client = QualityClient(_ctx(tmp_path, vcs=vcs))
    names = register_quality_tools(registry, client)
    assert names == list(QUALITY_TOOL_NAMES)
    assert json.loads(registry.dispatch("qua_gates_run", {}))["ok"] is True
    assert (
        json.loads(registry.dispatch("qua_contract_ack_status", {"change_id": "c-9"}))[
            "missing"
        ]
        == []
    )
    assert (
        json.loads(
            registry.dispatch(
                "qua_supply_chain_check", {"base_ref": "a", "head_ref": "b"}
            )
        )["verdict"]
        == "no dependency change"
    )
    assert json.loads(registry.dispatch("qua_secret_scan", {"paths": []}))["ok"] is True
    for name, args in (
        ("qua_greptile_review", {"action": "get", "review_id": "r-1"}),
        ("qua_receipt_approve", {"receipt_path": "plan.json"}),
        ("qua_waiver_record", {"pr_number": 1, "comment_ids": ["c"], "reason": "x"}),
        ("qua_contract_ack", {"change_id": "c-9", "ack": True, "note": "ok"}),
    ):
        assert json.loads(registry.dispatch(name, args)) == {
            "error": "approval required",
            "tool": name,
        }
    assert log.approve("qua_contract_ack", "ada").get("approved") is True
    assert (
        json.loads(
            registry.dispatch(
                "qua_contract_ack", {"change_id": "c-9", "ack": True, "note": "ok"}
            )
        )["ok"]
        is True
    )
