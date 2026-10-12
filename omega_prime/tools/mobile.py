"""Mobile pack: desk mobile tools + interactive device review.

Ports `services/desk-gateway/src/desk_gateway/tools/mobile.py` (Play and App
Store release flows, artifact/lint/entitlement/risk evidence) and adds
device list/review/act across the adb, simctl, and Appium backends, with
screenshots flowing into `vision_analyze`. Store clients, VCS, devices, and
vision are injected seams; tests use fakes only.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from omega_prime.tools.devices import AdbDevice, DeviceError, SimctlDevice
from omega_prime.tools.registry import ToolRegistry
from omega_prime.tools.vision import vision_analyze

MOBILE_TOOL_NAMES = (
    "mob_play_track_status",
    "mob_play_staged_rollout",
    "mob_play_halt_rollout",
    "mob_artifact_size_delta",
    "mob_lint_baseline_diff",
    "mob_testflight_status",
    "mob_appstore_phased_release",
    "mob_appstore_pause_release",
    "mob_entitlements_diff",
    "mob_review_risk_check",
    "mob_device_list",
    "mob_device_review",
    "mob_device_act",
)

ARTIFACT_SUFFIXES = (".apk", ".aab", ".ipa")
USAGE_KEYS = re.compile(r"NS[A-Za-z]+UsageDescription")
PRIVATE_API_MARKERS = (
    "dlopen(",
    "dlsym(",
    "NSSelectorFromString",
    "performSelector",
    "_UIBackdropView",
    "objc_msgSend",
)
STOREKIT_MARKERS = ("StoreKit", "SKProduct", "Product.products", "Transaction.updates")
ACCOUNT_MARKERS = (
    "deleteAccount",
    "delete account",
    "Account deletion",
    "DeleteAccount",
)
PACKAGE_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
ISSUE_TAG_PATTERN = re.compile(r"<issue(?=[\s/>])")


def _error(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"error": f"{code}: {reason}", **extra}


def _safe_shot_name(name: str) -> str | None:
    """`<stem>.png` confined to the artifacts dir, else None."""
    stem = name[:-4] if name.endswith(".png") else name
    if not stem or stem in (".", "..") or "/" in stem or "\\" in stem:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.~-]*", stem):
        return None
    return stem + ".png"


def _check_ref(ref: Any) -> None:
    """Reject refs git could parse as options; git owns revision syntax."""
    if (
        not isinstance(ref, str)
        or not ref
        or ref.startswith("-")
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in ref)
    ):
        raise ValueError("ref must be a git revision, not an option")


def _git_vcs(root: str):
    def show(ref: str, path: str) -> str | None:
        _check_ref(ref)
        run = subprocess.run(
            ["git", "show", "--end-of-options", f"{ref}:{path}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return run.stdout if run.returncode == 0 else None

    def diff_names(base: str, head: str) -> list[str]:
        _check_ref(base)
        _check_ref(head)
        run = subprocess.run(
            ["git", "diff", "--name-only", "--end-of-options", f"{base}...{head}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return run.stdout.splitlines() if run.returncode == 0 else []

    def diff(base: str, head: str, paths: list[str]) -> str:
        _check_ref(base)
        _check_ref(head)
        run = subprocess.run(
            ["git", "diff", "--end-of-options", f"{base}...{head}", "--", *paths],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return run.stdout if run.returncode == 0 else ""

    return {"show": show, "diff_names": diff_names, "diff": diff}


@dataclass
class MobileContext:
    """Everything the mobile tools need. Clients default to unconfigured."""

    root: str | Path = "."
    play: Any = None
    asc: Any = None
    vcs: Any = None
    devices: Any = None
    vision: Any = None
    artifacts_dir: str | Path = "artifacts/reviews"


class MobileClient:
    """Desk mobile tools + device review bound to one context."""

    def __init__(self, ctx: MobileContext) -> None:
        self.ctx = ctx

    # -- Play Console --------------------------------------------------------

    def play_track_status(self, package_name: str, track: str) -> dict[str, Any]:
        """One track's releases via a read edit."""
        _check_package(package_name)
        play = self._play()
        if isinstance(play, dict):
            return {"track": None, **play}
        edit_id = self._edit(play, package_name)
        if isinstance(edit_id, dict):
            return {"track": None, **edit_id}
        result = play.track(package_name, edit_id, track)
        if not isinstance(result, dict) or result.get("error"):
            return {
                "track": None,
                **(result if isinstance(result, dict) else {"error": "upstream_error"}),
            }
        return {"track": result.get("body"), "edit_id": edit_id}

    def play_staged_rollout(
        self,
        package_name: str,
        track: str,
        version_code: int,
        user_fraction: float,
        halt_threshold: str,
        confirm_full: bool = False,
    ) -> dict[str, Any]:
        """Staged rollout; 100% production needs `confirm_full`."""
        _check_package(package_name)
        if (
            isinstance(user_fraction, bool)
            or not isinstance(user_fraction, (int, float))
            or not 0 < user_fraction <= 1
        ):
            raise ValueError("user_fraction must be within (0, 1]")
        if track == "production" and user_fraction >= 1.0 and not confirm_full:
            return _error("forbidden", "production at 100 percent needs confirm_full")
        play = self._play()
        if isinstance(play, dict):
            return play
        edit_id = self._edit(play, package_name)
        if isinstance(edit_id, dict):
            return edit_id
        current = play.track(package_name, edit_id, track)
        if not isinstance(current, dict) or current.get("error"):
            reason = (
                current.get("error", "upstream_error")
                if isinstance(current, dict)
                else "upstream_error"
            )
            return _error(
                "upstream_error", f"cannot read track before rollout: {reason}"
            )
        existing = (current.get("body") or {}).get("releases") or []
        status = "inProgress" if user_fraction < 1.0 else "completed"
        release: dict[str, Any] = {
            "versionCodes": [str(version_code)],
            "status": status,
        }
        if user_fraction < 1.0:
            release["userFraction"] = user_fraction
        merged = [
            row
            for row in existing
            if str(version_code)
            not in [str(code) for code in (row.get("versionCodes") or [])]
        ] + [release]
        updated = play.update_track(
            package_name, edit_id, track, {"track": track, "releases": merged}
        )
        if not isinstance(updated, dict) or updated.get("error"):
            return _passthrough(updated, "track update failed")
        committed = play.commit(package_name, edit_id)
        if not isinstance(committed, dict) or committed.get("error"):
            return _passthrough(committed, "commit failed")
        return {
            "ok": True,
            "halt_threshold": halt_threshold,
            "release": release,
            "edit_id": edit_id,
        }

    def play_halt_rollout(
        self, package_name: str, track: str, reason: str
    ) -> dict[str, Any]:
        """Halt every in-progress release on one track."""
        _check_package(package_name)
        play = self._play()
        if isinstance(play, dict):
            return play
        edit_id = self._edit(play, package_name)
        if isinstance(edit_id, dict):
            return edit_id
        current = play.track(package_name, edit_id, track)
        if not isinstance(current, dict) or current.get("error"):
            return _passthrough(current, "track read failed")
        releases = (current.get("body") or {}).get("releases") or []
        changed = False
        for release in releases:
            if release.get("status") == "inProgress":
                release["status"] = "halted"
                changed = True
        if not changed:
            return _error(
                "nothing_to_halt", "no in-progress staged release on that track"
            )
        updated = play.update_track(
            package_name, edit_id, track, {"track": track, "releases": releases}
        )
        if not isinstance(updated, dict) or updated.get("error"):
            return _passthrough(updated, "halt failed")
        committed = play.commit(package_name, edit_id)
        if not isinstance(committed, dict) or committed.get("error"):
            return _passthrough(committed, "commit failed")
        return {
            "ok": True,
            "reason": reason,
            "halted": [r.get("name") for r in releases if r.get("status") == "halted"],
        }

    # -- artifact + lint evidence --------------------------------------------

    def artifact_size_delta(self, before_path: str, after_path: str) -> dict[str, Any]:
        """Byte delta between two APK/AAB/IPA artifacts under root."""
        root = Path(self.ctx.root)
        for rel in (before_path, after_path):
            if not _repo_relative(rel):
                return _error("invalid_args", "paths must be repo-relative")
            target = root / rel
            if not target.is_file() or target.suffix.lower() not in ARTIFACT_SUFFIXES:
                return _error(
                    "not_found", f"{target.name} is not an APK/AAB/IPA under this root"
                )
        before = (root / before_path).stat().st_size
        after = (root / after_path).stat().st_size
        return {
            "before_bytes": before,
            "after_bytes": after,
            "delta_bytes": after - before,
            "delta_pct": round((after - before) / before * 100, 2) if before else None,
        }

    def lint_baseline_diff(
        self, baseline_path: str, base_ref: str, head_ref: str
    ) -> dict[str, Any]:
        """Compare lint baseline issue counts and ids across two refs."""
        if not _repo_relative(baseline_path):
            return _error("invalid_args", "baseline_path must be repo-relative")
        show = self._vcs()["show"]
        base = show(base_ref, baseline_path) or ""
        head = show(head_ref, baseline_path) or ""
        base_ids = set(re.findall(r'id="([^"]+)"', base))
        head_ids = set(re.findall(r'id="([^"]+)"', head))
        base_issues = len(ISSUE_TAG_PATTERN.findall(base))
        head_issues = len(ISSUE_TAG_PATTERN.findall(head))
        return {
            "baseline": baseline_path,
            "issues_before": base_issues,
            "issues_after": head_issues,
            "grew": head_issues > base_issues,
            "new_ids": sorted(head_ids - base_ids),
            "verdict": "a baseline that grows hides a finding"
            if head_issues > base_issues
            else "baseline did not grow",
        }

    # -- App Store Connect ----------------------------------------------------

    def testflight_status(
        self, bundle_id: str, build: str | None = None
    ) -> dict[str, Any]:
        """Recent TestFlight builds for one bundle id."""
        asc = self._asc()
        if isinstance(asc, dict):
            return {"builds": [], **asc}
        apps = asc.request(
            "GET", "/apps", params={"filter[bundleId]": bundle_id, "limit": 1}
        )
        if not isinstance(apps, dict) or apps.get("error"):
            return {
                "builds": [],
                **(apps if isinstance(apps, dict) else {"error": "upstream_error"}),
            }
        data = (apps.get("body") or {}).get("data") or []
        if not data:
            return {"builds": [], **_error("not_found", "no app with that bundle id")}
        app_id = data[0]["id"]
        params: dict[str, Any] = {
            "filter[app]": app_id,
            "sort": "-uploadedDate",
            "limit": 10,
        }
        if build:
            params["filter[version]"] = build
        builds = asc.request("GET", "/builds", params=params)
        if not isinstance(builds, dict) or builds.get("error"):
            return {
                "builds": [],
                **(builds if isinstance(builds, dict) else {"error": "upstream_error"}),
            }
        items = [
            {
                "id": b.get("id"),
                **{
                    k: (b.get("attributes") or {}).get(k)
                    for k in ("version", "processingState", "uploadedDate", "expired")
                },
            }
            for b in (builds.get("body") or {}).get("data") or []
        ]
        return {"app_id": app_id, "builds": items}

    def _appstore_version_id(
        self, asc: Any, version: str, app_id: str | None
    ) -> str | dict[str, Any]:
        """One version id for a string, or an error. Refuses ambiguity."""
        params: dict[str, Any] = {"filter[versionString]": version, "limit": 2}
        if app_id:
            params["filter[app]"] = app_id
        versions = asc.request("GET", "/appStoreVersions", params=params)
        if not isinstance(versions, dict) or versions.get("error"):
            return _passthrough(versions, "version lookup failed")
        data = (versions.get("body") or {}).get("data") or []
        if not data:
            return _error("not_found", "no App Store version with that string")
        if len(data) > 1:
            return _error(
                "ambiguous", "that version string matches several apps; pass app_id"
            )
        if not isinstance(data[0], dict) or not data[0].get("id"):
            return _error("upstream_error", "version lookup returned no id")
        return data[0]["id"]

    def appstore_phased_release(
        self, version: str, halt_signal: str, app_id: str | None = None
    ) -> dict[str, Any]:
        """Activate a phased release for one version string."""
        asc = self._asc()
        if isinstance(asc, dict):
            return asc
        version_id = self._appstore_version_id(asc, version, app_id)
        if isinstance(version_id, dict):
            return version_id
        body = {
            "data": {
                "type": "appStoreVersionPhasedReleases",
                "attributes": {"phasedReleaseState": "ACTIVE"},
                "relationships": {
                    "appStoreVersion": {
                        "data": {"type": "appStoreVersions", "id": version_id}
                    }
                },
            }
        }
        result = asc.request("POST", "/appStoreVersionPhasedReleases", json=body)
        if not isinstance(result, dict) or result.get("error"):
            return _passthrough(result, "phased release failed")
        return {
            "ok": True,
            "halt_signal": halt_signal,
            "phased_release": (result.get("body") or {}).get("data"),
        }

    def appstore_pause_release(
        self, version: str, reason: str, app_id: str | None = None
    ) -> dict[str, Any]:
        """Pause the phased release of one version string."""
        asc = self._asc()
        if isinstance(asc, dict):
            return asc
        params: dict[str, Any] = {
            "filter[versionString]": version,
            "include": "appStoreVersionPhasedRelease",
            "limit": 2,
        }
        if app_id:
            params["filter[app]"] = app_id
        versions = asc.request("GET", "/appStoreVersions", params=params)
        if not isinstance(versions, dict) or versions.get("error"):
            return _passthrough(versions, "version lookup failed")
        data = (versions.get("body") or {}).get("data") or []
        if len(data) > 1:
            return _error(
                "ambiguous", "that version string matches several apps; pass app_id"
            )
        included = (versions.get("body") or {}).get("included") or []
        phased = next(
            (i for i in included if i.get("type") == "appStoreVersionPhasedReleases"),
            None,
        )
        if not phased:
            return _error("nothing_to_pause", "that version has no phased release")
        body = {
            "data": {
                "type": "appStoreVersionPhasedReleases",
                "id": phased["id"],
                "attributes": {"phasedReleaseState": "PAUSED"},
            }
        }
        result = asc.request(
            "PATCH", f"/appStoreVersionPhasedReleases/{phased['id']}", json=body
        )
        if not isinstance(result, dict) or result.get("error"):
            return _passthrough(result, "pause failed")
        return {"ok": True, "reason": reason, "phased_release_id": phased["id"]}

    # -- iOS review evidence ---------------------------------------------------

    def entitlements_diff(
        self, base_ref: str, head_ref: str, target_dir: str = "ios"
    ) -> dict[str, Any]:
        """Entitlement and usage-description changes across two refs."""
        vcs = self._vcs()
        names = vcs["diff_names"](base_ref, head_ref)
        relevant = [
            n
            for n in names
            if n.startswith(target_dir + "/")
            and (
                n.endswith(".entitlements")
                or n.endswith("Info.plist")
                or n.endswith(".plist")
            )
        ]
        diff = vcs["diff"](base_ref, head_ref, relevant) if relevant else ""
        return {
            "files": relevant,
            "usage_descriptions_added": sorted(
                set(re.findall(r"^\+.*?(NS[A-Za-z]+UsageDescription)", diff, re.M))
            ),
            "usage_descriptions_removed": sorted(
                set(re.findall(r"^-.*?(NS[A-Za-z]+UsageDescription)", diff, re.M))
            ),
            "entitlements_added": sorted(
                set(
                    re.findall(
                        r"^\+\s*<key>(com\.apple\.[A-Za-z0-9.-]+)</key>", diff, re.M
                    )
                )
            ),
            "entitlements_removed": sorted(
                set(
                    re.findall(
                        r"^-\s*<key>(com\.apple\.[A-Za-z0-9.-]+)</key>", diff, re.M
                    )
                )
            ),
            "paragraph": "no entitlement change"
            if not relevant
            else "entitlement or usage-description files changed; list them in the report",
        }

    def review_risk_check(self, base_ref: str, head_ref: str) -> dict[str, Any]:
        """Flag App Review risks in added Swift/ObjC/plist lines."""
        vcs = self._vcs()
        names = vcs["diff_names"](base_ref, head_ref)
        swift = [
            n for n in names if n.endswith((".swift", ".m", ".plist", ".entitlements"))
        ]
        diff = vcs["diff"](base_ref, head_ref, swift) if swift else ""
        added = "\n".join(
            line
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        risks = []
        if USAGE_KEYS.search(added):
            risks.append(
                {
                    "risk": "permission",
                    "detail": "a usage description was added or changed; confirm the string matches the code path and the prompt appears in context",
                }
            )
        if any(m in added for m in PRIVATE_API_MARKERS):
            risks.append(
                {
                    "risk": "private_api",
                    "detail": "dynamic selector or dlopen usage added; a compile that succeeds does not prove the symbol is public",
                }
            )
        if any(m in added for m in STOREKIT_MARKERS):
            risks.append(
                {
                    "risk": "purchases",
                    "detail": "StoreKit code changed; digital goods must go through StoreKit and the flow needs LEAD's attention",
                }
            )
        if any(m in added for m in ACCOUNT_MARKERS):
            risks.append(
                {
                    "risk": "account_deletion",
                    "detail": "account UI changed; account deletion must stay reachable",
                }
            )
        if (
            "NSLocationAlwaysAndWhenInUseUsageDescription" in added
            or "NSLocationAlwaysUsageDescription" in added
        ):
            risks.append(
                {
                    "risk": "always_location",
                    "detail": "always-on location needs a justification a reviewer can read",
                }
            )
        return {
            "files_checked": swift,
            "risks": risks,
            "verdict": "none" if not risks else "flag before submission",
        }

    # -- devices -----------------------------------------------------------------

    def device_list(self) -> dict[str, Any]:
        """Aggregate devices across the configured backends."""
        devices = []
        errors = []
        for backend in self._devices():
            try:
                for row in backend.list_devices():
                    devices.append({"backend": backend.name, **row})
            except DeviceError as exc:
                errors.append(str(exc))
        result: dict[str, Any] = {"devices": devices}
        if errors:
            result["errors"] = errors
        return result

    def device_review(
        self,
        device_id: str,
        backend: str,
        question: str,
        name: str | None = None,
        capabilities: dict | None = None,
    ) -> dict[str, Any]:
        """Screenshot one device and run vision analysis over it."""
        if not isinstance(question, str) or question == "":
            raise ValueError("question must be a non-empty string")
        peer = self._backend(backend)
        if isinstance(peer, dict):
            return peer
        shot_name = _safe_shot_name(name or f"device-{abs(hash(device_id)) % 10**8}")
        if shot_name is None:
            return _error(
                "invalid_name", "screenshot name stays inside the artifacts dir"
            )
        target = str(Path(self.ctx.artifacts_dir) / shot_name)
        try:
            if backend == "appium":
                shot = peer.screenshot(device_id, target, capabilities or {})
            else:
                shot = peer.screenshot(device_id, target)
        except DeviceError as exc:
            return {"device_id": device_id, "backend": backend, "error": str(exc)}
        vision = vision_analyze(self.ctx.vision, shot["path"], question)
        return {
            "device_id": device_id,
            "backend": backend,
            "screenshot": shot,
            "vision": vision,
        }

    def device_act(
        self,
        device_id: str,
        backend: str,
        kind: str,
        x: int | None = None,
        y: int | None = None,
        text: str | None = None,
        capabilities: dict | None = None,
    ) -> dict[str, Any]:
        """One tap/type/back on an Appium device. Approval-gated at registration."""
        peer = self._backend(backend)
        if isinstance(peer, dict):
            return peer
        act = getattr(peer, "act", None)
        if not callable(act):
            return _error("unsupported", f"{backend} devices capture screenshots only")
        action: dict[str, Any] = {"kind": kind}
        if kind == "tap":
            if not isinstance(x, int) or not isinstance(y, int):
                raise ValueError("tap needs integer x and y")
            action.update({"x": x, "y": y})
        elif kind == "type":
            if not isinstance(text, str) or text == "":
                raise ValueError("type needs non-empty text")
            action["text"] = text
        elif kind != "back":
            raise ValueError("kind must be tap, type, or back")
        try:
            return {
                "device_id": device_id,
                "backend": backend,
                **act(device_id, action, capabilities or {}),
            }
        except DeviceError as exc:
            return {"device_id": device_id, "backend": backend, "error": str(exc)}

    # -- seams --------------------------------------------------------------------

    def _play(self) -> Any:
        if self.ctx.play is None:
            return _error(
                "not_configured", "Play Console access token is not configured"
            )
        return self.ctx.play

    def _edit(self, play: Any, package_name: str) -> Any:
        edit = play.edit(package_name)
        if not isinstance(edit, dict) or edit.get("error"):
            return _passthrough(edit, "edit failed")
        edit_id = (edit.get("body") or {}).get("id")
        if not edit_id:
            return _error("upstream_error", "edit returned no id")
        return edit_id

    def _asc(self) -> Any:
        if self.ctx.asc is None:
            return _error("not_configured", "App Store Connect key is not configured")
        return self.ctx.asc

    def _vcs(self) -> Any:
        return self.ctx.vcs or _git_vcs(str(self.ctx.root))

    def _devices(self) -> list:
        if self.ctx.devices is not None:
            return list(self.ctx.devices)
        return [AdbDevice(), SimctlDevice()]

    def _backend(self, backend: Any) -> Any:
        for peer in self._devices():
            if peer.name == backend:
                return peer
        return _error("not_found", f"no {backend} device backend is configured")


def _check_package(package_name: Any) -> None:
    if not isinstance(package_name, str) or not PACKAGE_PATTERN.match(package_name):
        raise ValueError("package_name must be a dotted Java-style package")


def _repo_relative(path: Any) -> bool:
    if not isinstance(path, str) or path == "":
        return False
    candidate = PurePosixPath(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


def _passthrough(result: Any, fallback: str) -> dict[str, Any]:
    if isinstance(result, dict) and result.get("error"):
        return result
    reason = result.get("reason", fallback) if isinstance(result, dict) else fallback
    code = (
        result.get("error", "upstream_error")
        if isinstance(result, dict)
        else "upstream_error"
    )
    return _error(str(code), str(reason))


def register_mobile_tools(registry: ToolRegistry, client: MobileClient) -> list[str]:
    """Register the 13 mobile tools. Release writes + device act need approval."""

    def _wrap(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}

    handlers: dict[str, Callable[..., Any]] = {
        "mob_play_track_status": lambda package_name, track: _wrap(
            client.play_track_status, package_name, track
        ),
        "mob_play_staged_rollout": lambda package_name, track, version_code, user_fraction, halt_threshold, confirm_full=False: (
            _wrap(
                client.play_staged_rollout,
                package_name,
                track,
                version_code,
                user_fraction,
                halt_threshold,
                confirm_full=confirm_full,
            )
        ),
        "mob_play_halt_rollout": lambda package_name, track, reason: _wrap(
            client.play_halt_rollout, package_name, track, reason
        ),
        "mob_artifact_size_delta": lambda before_path, after_path: _wrap(
            client.artifact_size_delta, before_path, after_path
        ),
        "mob_lint_baseline_diff": lambda baseline_path, base_ref, head_ref: _wrap(
            client.lint_baseline_diff, baseline_path, base_ref, head_ref
        ),
        "mob_testflight_status": lambda bundle_id, build=None: _wrap(
            client.testflight_status, bundle_id, build=build
        ),
        "mob_appstore_phased_release": lambda version, halt_signal, app_id=None: _wrap(
            client.appstore_phased_release, version, halt_signal, app_id=app_id
        ),
        "mob_appstore_pause_release": lambda version, reason, app_id=None: _wrap(
            client.appstore_pause_release, version, reason, app_id=app_id
        ),
        "mob_entitlements_diff": lambda base_ref, head_ref, target_dir="ios": _wrap(
            client.entitlements_diff, base_ref, head_ref, target_dir=target_dir
        ),
        "mob_review_risk_check": lambda base_ref, head_ref: _wrap(
            client.review_risk_check, base_ref, head_ref
        ),
        "mob_device_list": lambda: _wrap(client.device_list),
        "mob_device_review": lambda device_id, backend, question, name=None, capabilities=None: (
            _wrap(
                client.device_review,
                device_id,
                backend,
                question,
                name=name,
                capabilities=capabilities,
            )
        ),
        "mob_device_act": lambda device_id, backend, kind, x=None, y=None, text=None, capabilities=None: (
            _wrap(
                client.device_act,
                device_id,
                backend,
                kind,
                x=x,
                y=y,
                text=text,
                capabilities=capabilities,
            )
        ),
    }
    if set(handlers) != set(MOBILE_TOOL_NAMES):
        raise RuntimeError("Mobile tool handlers drifted from MOBILE_TOOL_NAMES")
    approvals = {
        "mob_play_staged_rollout",
        "mob_play_halt_rollout",
        "mob_appstore_phased_release",
        "mob_appstore_pause_release",
        "mob_device_act",
    }
    for name in MOBILE_TOOL_NAMES:
        description, parameters = _SCHEMAS[name]
        registry.register(
            name,
            description,
            parameters,
            handlers[name],
            requires_approval=(name in approvals),
        )
    return list(MOBILE_TOOL_NAMES)


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


_SCHEMAS: dict[str, tuple[str, dict]] = {
    "mob_play_track_status": (
        "One Play track's releases. Read-only.",
        _object(
            {"package_name": _string("App package."), "track": _string("Track name.")},
            ["package_name", "track"],
        ),
    ),
    "mob_play_staged_rollout": (
        "Staged Play rollout; 100% production needs confirm_full. Requires approval.",
        _object(
            {
                "package_name": _string("App package."),
                "track": _string("Track name."),
                "version_code": {"type": "integer"},
                "user_fraction": {"type": "number"},
                "halt_threshold": _string("Halt condition."),
                "confirm_full": {"type": "boolean"},
            },
            [
                "package_name",
                "track",
                "version_code",
                "user_fraction",
                "halt_threshold",
            ],
        ),
    ),
    "mob_play_halt_rollout": (
        "Halt in-progress releases on one track. Requires approval.",
        _object(
            {
                "package_name": _string("App package."),
                "track": _string("Track name."),
                "reason": _string("Halt reason."),
            },
            ["package_name", "track", "reason"],
        ),
    ),
    "mob_artifact_size_delta": (
        "Byte delta between two APK/AAB/IPA files. Read-only.",
        _object(
            {
                "before_path": _string("Repo-relative path."),
                "after_path": _string("Repo-relative path."),
            },
            ["before_path", "after_path"],
        ),
    ),
    "mob_lint_baseline_diff": (
        "Lint baseline growth across two refs. Read-only.",
        _object(
            {
                "baseline_path": _string("Repo-relative path."),
                "base_ref": _string("Base ref."),
                "head_ref": _string("Head ref."),
            },
            ["baseline_path", "base_ref", "head_ref"],
        ),
    ),
    "mob_testflight_status": (
        "Recent TestFlight builds. Read-only.",
        _object(
            {"bundle_id": _string("Bundle id."), "build": _string("Build filter.")},
            ["bundle_id"],
        ),
    ),
    "mob_appstore_phased_release": (
        "Activate a phased release. Requires approval.",
        _object(
            {
                "version": _string("Version string."),
                "halt_signal": _string("Halt condition."),
                "app_id": _string("App id when the string is ambiguous."),
            },
            ["version", "halt_signal"],
        ),
    ),
    "mob_appstore_pause_release": (
        "Pause a phased release. Requires approval.",
        _object(
            {
                "version": _string("Version string."),
                "reason": _string("Pause reason."),
                "app_id": _string("App id when the string is ambiguous."),
            },
            ["version", "reason"],
        ),
    ),
    "mob_entitlements_diff": (
        "Entitlement changes across two refs. Read-only.",
        _object(
            {
                "base_ref": _string("Base ref."),
                "head_ref": _string("Head ref."),
                "target_dir": _string("Target dir."),
            },
            ["base_ref", "head_ref"],
        ),
    ),
    "mob_review_risk_check": (
        "App Review risks in added lines. Read-only.",
        _object(
            {"base_ref": _string("Base ref."), "head_ref": _string("Head ref.")},
            ["base_ref", "head_ref"],
        ),
    ),
    "mob_device_list": (
        "Devices across the configured backends. Read-only.",
        _object({}, []),
    ),
    "mob_device_review": (
        "Screenshot one device and run vision analysis. Read-only.",
        _object(
            {
                "device_id": _string("Device id."),
                "backend": _string("adb, simctl, or appium."),
                "question": _string("Vision question."),
                "name": _string("Screenshot stem."),
                "capabilities": {"type": "object"},
            },
            ["device_id", "backend", "question"],
        ),
    ),
    "mob_device_act": (
        "One tap/type/back on an Appium device. Requires approval.",
        _object(
            {
                "device_id": _string("Device id."),
                "backend": _string("Backend name."),
                "kind": _string("tap, type, or back."),
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "text": _string("Text for type."),
                "capabilities": {"type": "object"},
            },
            ["device_id", "backend", "kind"],
        ),
    ),
}


__all__ = [
    "MOBILE_TOOL_NAMES",
    "MobileClient",
    "MobileContext",
    "register_mobile_tools",
]
