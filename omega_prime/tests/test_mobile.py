"""Phase 29: mobile tool family behind fakes; devices stubbed."""

from __future__ import annotations

import json

import pytest

from omega_prime.tools.approvals import ApprovalLog
from omega_prime.tools.devices import AdbDevice, DeviceError, SimctlDevice
from omega_prime.tools.mobile import MobileClient, MobileContext, register_mobile_tools
from omega_prime.tools.registry import ToolRegistry


class FakePlay:
    def __init__(self, releases=None):
        self.releases = (
            releases
            if releases is not None
            else [{"name": "r1", "status": "inProgress", "versionCodes": ["7"]}]
        )
        self.calls: list[tuple] = []

    def edit(self, package):
        self.calls.append(("edit", package))
        return {"body": {"id": "e-1"}}

    def track(self, package, edit_id, track):
        self.calls.append(("track", package, edit_id, track))
        return {"body": {"track": track, "releases": [dict(r) for r in self.releases]}}

    def update_track(self, package, edit_id, track, body):
        self.calls.append(("update", package, edit_id, track, body))
        self.releases = body["releases"]
        return {"body": {"ok": True}}

    def commit(self, package, edit_id):
        self.calls.append(("commit", package, edit_id))
        return {"body": {"id": "e-1"}}


class FakeAsc:
    def __init__(self):
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, json=None):
        self.calls.append((method, path, params, json))
        if path == "/apps":
            return {"body": {"data": [{"id": "app-1"}]}}
        if path == "/builds":
            return {
                "body": {
                    "data": [
                        {
                            "id": "b-1",
                            "attributes": {
                                "version": "42",
                                "processingState": "VALID",
                                "uploadedDate": "today",
                                "expired": False,
                            },
                        }
                    ]
                }
            }
        if path == "/appStoreVersions":
            body = {"data": [{"id": "v-1"}]}
            if params and params.get("include"):
                body["included"] = [
                    {"type": "appStoreVersionPhasedReleases", "id": "pr-1"}
                ]
            return {"body": body}
        return {
            "body": {"data": {"id": "pr-1", "type": "appStoreVersionPhasedReleases"}}
        }


class FakeVcs:
    def __init__(self, files=None, names=None, diff=""):
        self.files = files or {}
        self.names = names if names is not None else []
        self.diff_text = diff

    def show(self, ref, path):
        return self.files.get((ref, path))

    def diff_names(self, base, head):
        return list(self.names)

    def diff(self, base, head, paths):
        return self.diff_text


class FakeDevice:
    name = "fake"

    def __init__(self, rows=None, shot=b"\x89PNG-fake"):
        self.rows = (
            rows
            if rows is not None
            else [
                {"id": "d-1", "platform": "android", "name": "emu", "state": "device"}
            ]
        )
        self.shot = shot
        self.calls: list[tuple] = []

    def list_devices(self):
        self.calls.append(("list",))
        return list(self.rows)

    def screenshot(self, device_id, path):
        from pathlib import Path as P

        self.calls.append(("shot", device_id, path))
        P(path).parent.mkdir(parents=True, exist_ok=True)
        P(path).write_bytes(self.shot)
        return {"path": path, "bytes": len(self.shot)}

    def act(self, device_id, action, capabilities):
        self.calls.append(("act", device_id, action))
        return {"ok": True, "kind": action["kind"]}


class FakeVision:
    def analyze(self, image_url, question, region=None):
        return {"answer": f"saw {question}"}


def _ctx(tmp_path, **overrides):
    vcs = FakeVcs()
    base = dict(
        root=tmp_path,
        play=FakePlay(),
        asc=FakeAsc(),
        vcs={"show": vcs.show, "diff_names": vcs.diff_names, "diff": vcs.diff},
        devices=[FakeDevice()],
        vision=FakeVision(),
        artifacts_dir=tmp_path / "shots",
    )
    base.update(overrides)
    return MobileContext(**base)


def test_play_track_status_and_rollout(tmp_path):
    client = MobileClient(_ctx(tmp_path))
    status = client.play_track_status("com.acme.app", "production")
    assert status["track"]["releases"][0]["status"] == "inProgress"
    assert status["edit_id"] == "e-1"
    staged = client.play_staged_rollout(
        "com.acme.app", "production", 8, 0.5, "crash>1%"
    )
    assert staged["ok"] is True and staged["release"]["userFraction"] == 0.5
    assert (
        "forbidden"
        in client.play_staged_rollout("com.acme.app", "production", 8, 1.0, "x")[
            "error"
        ]
    )
    full = client.play_staged_rollout(
        "com.acme.app", "production", 8, 1.0, "x", confirm_full=True
    )
    assert full["release"]["status"] == "completed"
    play = FakePlay()
    merging = MobileClient(_ctx(tmp_path, play=play))
    merging.play_staged_rollout("com.acme.app", "production", 9, 0.5, "x")
    body = next(call[4] for call in play.calls if call[0] == "update")
    codes = [code for row in body["releases"] for code in row["versionCodes"]]
    assert codes == ["7", "9"]
    bare = MobileClient(MobileContext(root=tmp_path))
    assert "not_configured" in bare.play_track_status("com.acme.app", "t")["error"]
    try:
        client.play_track_status("not-a-package", "t")
        raise SystemExit("should have raised")
    except ValueError:
        pass


def test_play_halt_edges(tmp_path):
    client = MobileClient(_ctx(tmp_path))
    halted = client.play_halt_rollout("com.acme.app", "production", "regression")
    assert halted == {"ok": True, "reason": "regression", "halted": ["r1"]}
    again = client.play_halt_rollout("com.acme.app", "production", "again")
    assert "nothing_to_halt" in again["error"]


def test_artifact_size_and_lint(tmp_path):
    (tmp_path / "a.apk").write_bytes(b"0" * 1000)
    (tmp_path / "b.ipa").write_bytes(b"0" * 1200)
    vcs = FakeVcs(
        files={
            ("base", "lint.xml"): '<issue id="A"/><issue id="B"/>',
            ("head", "lint.xml"): '<issue id="A"/><issue id="B"/><issue id="C"/>',
        }
    )
    client = MobileClient(
        _ctx(
            tmp_path,
            vcs={"show": vcs.show, "diff_names": vcs.diff_names, "diff": vcs.diff},
        )
    )
    delta = client.artifact_size_delta("a.apk", "b.ipa")
    assert delta == {
        "before_bytes": 1000,
        "after_bytes": 1200,
        "delta_bytes": 200,
        "delta_pct": 20.0,
    }
    assert "APK/AAB/IPA" in client.artifact_size_delta("a.apk", "nope.apk")["error"]
    lint = client.lint_baseline_diff("lint.xml", "base", "head")
    assert lint["issues_before"] == 2 and lint["issues_after"] == 3
    assert lint["grew"] is True and lint["new_ids"] == ["C"]


def _lint_doc(body: str, by: str = "lint 8.0.0") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<issues format="6" by="{by}">\n{body}</issues>\n'
    )


def _lint_client(tmp_path, files):
    vcs = FakeVcs(files=files)
    return MobileClient(
        _ctx(
            tmp_path,
            vcs={"show": vcs.show, "diff_names": vcs.diff_names, "diff": vcs.diff},
        )
    )


def test_lint_baseline_diff_counts_issue_elements_not_container(tmp_path):
    base = _lint_doc('  <issue id="HardcodedText" severity="Warning" />\n')
    head = _lint_doc(
        '  <issue id="HardcodedText" severity="Warning" />\n'
        '  <issue id="UnusedResources" severity="Warning" />\n'
    )
    client = _lint_client(
        tmp_path, {("base", "lint.xml"): base, ("head", "lint.xml"): head}
    )
    lint = client.lint_baseline_diff("lint.xml", "base", "head")
    assert lint["issues_before"] == 1 and lint["issues_after"] == 2
    assert lint["grew"] is True and lint["new_ids"] == ["UnusedResources"]


def test_lint_baseline_diff_empty_baseline_to_one(tmp_path):
    base = _lint_doc("")
    head = _lint_doc('  <issue id="HardcodedText" severity="Warning" />\n')
    client = _lint_client(
        tmp_path, {("base", "lint.xml"): base, ("head", "lint.xml"): head}
    )
    lint = client.lint_baseline_diff("lint.xml", "base", "head")
    assert lint["issues_before"] == 0 and lint["issues_after"] == 1
    assert lint["grew"] is True and lint["new_ids"] == ["HardcodedText"]


def test_lint_baseline_diff_container_metadata_change_is_no_growth(tmp_path):
    body = '  <issue id="HardcodedText" severity="Warning" />\n'
    client = _lint_client(
        tmp_path,
        {
            ("base", "lint.xml"): _lint_doc(body, by="lint 8.0.0"),
            ("head", "lint.xml"): _lint_doc(body, by="lint 8.1.0"),
        },
    )
    lint = client.lint_baseline_diff("lint.xml", "base", "head")
    assert lint["issues_before"] == 1 and lint["issues_after"] == 1
    assert lint["grew"] is False and lint["new_ids"] == []


def test_testflight_and_phased_flows(tmp_path):
    client = MobileClient(_ctx(tmp_path))
    status = client.testflight_status("com.acme.app")
    assert status["app_id"] == "app-1" and status["builds"][0]["version"] == "42"
    active = client.appstore_phased_release("1.2", "halt on crash")
    assert active["ok"] is True and active["phased_release"]["id"] == "pr-1"
    paused = client.appstore_pause_release("1.2", "regression")
    assert paused == {"ok": True, "reason": "regression", "phased_release_id": "pr-1"}

    class AmbiguousAsc(FakeAsc):
        def request(self, method, path, params=None, json=None):
            if path == "/appStoreVersions" and not (params or {}).get("filter[app]"):
                return {"body": {"data": [{"id": "v-1"}, {"id": "v-2"}]}}
            return super().request(method, path, params=params, json=json)

    cloudy = MobileClient(_ctx(tmp_path, asc=AmbiguousAsc()))
    assert "ambiguous" in cloudy.appstore_phased_release("1.2", "x")["error"]
    assert "ambiguous" in cloudy.appstore_pause_release("1.2", "x")["error"]
    scoped = cloudy.appstore_phased_release("1.2", "x", app_id="app-1")
    assert scoped["ok"] is True
    bare = MobileClient(MobileContext(root=tmp_path))
    assert "not_configured" in bare.testflight_status("com.acme.app")["error"]


def test_entitlements_and_risk_goldens(tmp_path):
    diff = (
        "+<key>com.apple.developer.in-app-payments</key>\n"
        "+<string>NSCameraUsageDescription</string>\n"
        "+run = StoreKit.Transaction.updates\n"
        "+dlopen(handle)\n"
        "+deleteAccount tapped\n"
    )
    vcs = FakeVcs(names=["ios/App.entitlements", "ios/Feat.swift"], diff=diff)
    client = MobileClient(
        _ctx(
            tmp_path,
            vcs={"show": vcs.show, "diff_names": vcs.diff_names, "diff": vcs.diff},
        )
    )
    ent = client.entitlements_diff("base", "head")
    assert ent["files"] == ["ios/App.entitlements"]
    assert ent["usage_descriptions_added"] == ["NSCameraUsageDescription"]
    assert ent["entitlements_added"] == ["com.apple.developer.in-app-payments"]
    assert "list them" in ent["paragraph"]
    risk = client.review_risk_check("base", "head")
    assert risk["files_checked"] == ["ios/App.entitlements", "ios/Feat.swift"]
    assert [r["risk"] for r in risk["risks"]] == [
        "permission",
        "private_api",
        "purchases",
        "account_deletion",
    ]
    assert risk["verdict"] == "flag before submission"
    clean = MobileClient(_ctx(tmp_path)).review_risk_check("b", "h")
    assert clean == {"files_checked": [], "risks": [], "verdict": "none"}


def test_devices_list_review_act_and_backend_errors(tmp_path):
    class _DownDevice(FakeDevice):
        def list_devices(self):
            raise DeviceError("adb down")

    failing = _DownDevice(rows=[])
    client = MobileClient(_ctx(tmp_path, devices=[FakeDevice(), failing]))
    listed = client.device_list()
    assert listed["devices"][0]["backend"] == "fake"
    assert listed["errors"] == ["adb down"]
    report = client.device_review("d-1", "fake", "is login visible?", name="login")
    assert report["screenshot"]["path"].endswith("login.png")
    assert (
        "invalid_name"
        in client.device_review("d-1", "fake", "q", name="../evil")["error"]
    )
    assert (
        "invalid_name" in client.device_review("d-1", "fake", "q", name="/abs")["error"]
    )
    assert "login visible" in report["vision"]["answer"]
    assert (
        "no appium device backend" in client.device_review("d", "appium", "q")["error"]
    )
    acted = client.device_act("d-1", "fake", "tap", x=10, y=20)
    assert acted["kind"] == "tap"
    adb_only = MobileClient(
        _ctx(tmp_path, devices=[AdbDevice(run=lambda *a, **k: None)])
    )
    assert "screenshots only" in adb_only.device_act("em-1", "adb", "back")["error"]


def test_adb_simctl_parsers_and_approvals(tmp_path):
    class Run:
        def __init__(self, code=0, out=b""):
            self.returncode = code
            self.stdout = out

    adb = AdbDevice(
        run=lambda argv, timeout=15: Run(
            0, b"List of devices\nem-1\tdevice model:Pixel\nxx\toffline\n"
        )
    )
    assert adb.list_devices() == [
        {"id": "em-1", "platform": "android", "name": "Pixel", "state": "device"}
    ]
    adb2 = AdbDevice(run=lambda argv, timeout=30: Run(0, b"\x89PNG-data"))
    shot = adb2.screenshot("em-1", tmp_path / "a.png")
    assert shot["bytes"] == 9
    sim = SimctlDevice(
        run=lambda argv, timeout=30: Run(
            0,
            b"-- iOS 18 --\n    iPhone 15 (AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE) (Booted)\n",
        )
    )
    assert sim.list_devices()[0]["name"] == "iPhone 15"

    log = ApprovalLog()
    registry = ToolRegistry(approval_log=log)
    register_mobile_tools(registry, MobileClient(_ctx(tmp_path)))
    assert json.loads(registry.dispatch("mob_device_list", {}))["devices"] != []
    for tool, args in (
        (
            "mob_play_staged_rollout",
            {
                "package_name": "com.acme.app",
                "track": "t",
                "version_code": 1,
                "user_fraction": 0.5,
                "halt_threshold": "x",
            },
        ),
        ("mob_device_act", {"device_id": "d", "backend": "fake", "kind": "back"}),
    ):
        assert json.loads(registry.dispatch(tool, args)) == {
            "error": "approval required",
            "tool": tool,
        }
    assert log.approve("mob_device_act", "ada").get("approved") is True
    assert (
        json.loads(
            registry.dispatch(
                "mob_device_act", {"device_id": "d", "backend": "fake", "kind": "back"}
            )
        )["kind"]
        == "back"
    )


# -- Phase 75 re-review WR-03: git ref validation in the real VCS seam --------
# Caller-supplied refs reach git argv, so a leading-dash ref must be refused
# before any subprocess spawns, and valid revisions must travel behind an
# explicit end-of-options marker so git never parses them as flags.


def test_git_vcs_rejects_option_like_refs_without_spawning(tmp_path, monkeypatch):
    from omega_prime.tools.mobile import _git_vcs

    def _no_spawn(*args, **kwargs):
        raise AssertionError("git must not spawn for an option-like ref")

    monkeypatch.setattr("subprocess.run", _no_spawn)
    vcs = _git_vcs(str(tmp_path))
    for call in (
        lambda: vcs["show"]("--help", "a.txt"),
        lambda: vcs["show"]("-h", "a.txt"),
        lambda: vcs["diff_names"]("--output=/tmp/x", "HEAD"),
        lambda: vcs["diff_names"]("HEAD", "--upload-pack=t"),
        lambda: vcs["diff"]("--output=/tmp/x", "HEAD", []),
        lambda: vcs["diff"]("HEAD", "-u", []),
    ):
        with pytest.raises(ValueError):
            call()


def test_git_vcs_rejects_malformed_refs_and_separates_options(
    tmp_path, monkeypatch
):
    from omega_prime.tools.mobile import _git_vcs

    seen: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = ""

    def _capture(argv, **kwargs):
        seen.append(list(argv))
        return _Done()

    monkeypatch.setattr("subprocess.run", _capture)
    vcs = _git_vcs(str(tmp_path))
    for bad in ("", "has space", "semi;colon", "tick`", "dollar$e", "paren(x)"):
        with pytest.raises(ValueError):
            vcs["show"](bad, "a.txt")
    assert seen == []
    assert vcs["show"]("HEAD", "a.txt") == ""
    vcs["diff_names"]("v1", "v2")
    vcs["diff"]("v1", "v2", ["ios/App.entitlements"])
    assert seen == [
        ["git", "show", "--end-of-options", "HEAD:a.txt"],
        ["git", "diff", "--name-only", "--end-of-options", "v1...v2"],
        ["git", "diff", "--end-of-options", "v1...v2", "--", "ios/App.entitlements"],
    ]
