#!/usr/bin/env python3
"""Shared CI receipt helper for .github/workflows/ci.yml.

Dedups the interpreter-identity, exit-capture, and receipt-summary blocks
previously repeated across the verify/lint/types matrix jobs (48-REVIEW.md
IN-01), and applies the WR-01 exit-code mapping fix in one place.

Standard library only -- no added packages.

Modes (argv[1]):
  identity              Emit interpreter/checkout identity JSON, record this
                        stage's exit code, and propagate it.
  run STAGE -- CMD ...  Execute CMD with inherited stdio, record STAGE's
                        exit code to GITHUB_OUTPUT, and propagate it exactly
                        (bash's 128+signo signal mapping included).
  retention ...         Gate the readiness candidate report and retain exactly
                        one sanitized document (validated report or truthful
                        failure metadata) under RUNNER_TEMP; exact observed
                        exits propagate, guard rejection never becomes green.
  summary               Always-run per-job receipt: map raw Actions step
                        outcomes/conclusions to passed/failed/skipped/not_run
                        evidence. When GUARD_TESTCASE is set, also check the
                        named JUnit testcase (verify job only).

Every mode fails closed: an unobserved stage, a missing prerequisite, or a
failed setup yields a nonzero exit -- never a fabricated pass.
"""

import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any


def record_exit(stage, rc):
    """Print the stage marker, publish exit_code, and return rc.

    Raises (nonzero step exit) when GITHUB_OUTPUT is absent rather than
    recording a pass that was never observed.
    """
    print(f"stage={stage} exit_code={rc}", flush=True)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"exit_code={rc}\n")
    return rc


def cmd_identity():
    """Interpreter-identity receipt (one copy; used by every matrix job)."""
    requested = os.environ["REQUESTED_PYTHON"]
    checkout = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    keys = "GITHUB_REPOSITORY GITHUB_WORKFLOW GITHUB_RUN_ID GITHUB_RUN_NUMBER GITHUB_RUN_ATTEMPT GITHUB_JOB GITHUB_EVENT_NAME GITHUB_REF GITHUB_SHA RUNNER_OS RUNNER_ARCH"
    print(
        json.dumps(
            {
                "requested_python": requested,
                "setup_python_version": os.environ.get("SETUP_PYTHON_VERSION") or None,
                "setup_python_path": os.environ.get("SETUP_PYTHON_PATH") or None,
                "checkout_sha": checkout.stdout.strip() or None,
                "sys_version": sys.version,
                "sys_version_info": list(sys.version_info),
                "sys_executable": sys.executable,
                "executable_realpath": os.path.realpath(sys.executable),
                "sys_prefix": sys.prefix,
                "sys_base_prefix": sys.base_prefix,
                "platform": platform.platform(),
                "implementation": platform.python_implementation(),
                "machine": platform.machine(),
                "actions": {key: os.environ.get(key) for key in keys.split()},
            },
            sort_keys=True,
        )
    )
    if checkout.returncode:
        print(f"git rev-parse HEAD exited {checkout.returncode}", file=sys.stderr)
        return record_exit("interpreter_identity", 1)
    if list(sys.version_info[:2]) != [int(part) for part in requested.split(".")[:2]]:
        print(
            f"requested Python {requested} but interpreter reports {sys.version}",
            file=sys.stderr,
        )
        return record_exit("interpreter_identity", 1)
    return record_exit("interpreter_identity", 0)


def cmd_run(stage, argv):
    """Run CMD, record its exit code, and propagate it exactly."""
    try:
        completed = subprocess.run(argv)
    except FileNotFoundError:
        print(f"{argv[0]}: command not found", file=sys.stderr)
        return record_exit(stage, 127)
    rc = completed.returncode
    if rc < 0:
        rc = 128 - rc  # match bash's 128+signo mapping for signal death
    return record_exit(stage, rc)


RETENTION_UNOBSERVED_TOKEN = "unobserved"
"""Literal --observed-exit value when the readiness stage never ran."""

_RETENTION_REASON_MISSING = "candidate report missing"
_RETENTION_REASON_UNREADABLE = "candidate report unreadable"
_RETENTION_REASON_INADMISSIBLE = "candidate report outside owned runner temp"
_RETENTION_REASON_UNDECODABLE = "candidate report undecodable"
_RETENTION_REASON_INCOMPLETE = "candidate report incomplete"
_RETENTION_REASON_WITHHELD = "candidate report withheld: secret gate tripped"
_RETENTION_REASON_UNAVAILABLE = (
    "candidate report withheld: publication gate unavailable"
)
_RETENTION_REASON_INCOHERENT = "candidate report incoherent with observed exit"

_RETENTION_FLAGS = (
    "--stage",
    "--observed-exit",
    "--interpreter",
    "--source-commit",
    "--candidate",
    "--output",
)


def _retention_metadata(stage, exit_code, reason, interpreter, source_commit):
    """Failure-metadata document: exactly these five fields, never a report."""
    return {
        "stage": stage,
        "exit_code": exit_code,
        "reason": reason,
        "interpreter": interpreter,
        "source_commit": source_commit,
    }


# Fixed safe stand-ins published when an identity label or the serialized
# failure metadata trips the secret gate. Only these constants (plus the
# truthful observed exit int/null) ever reach the retained JSON or stdout on
# a trip -- never the rejected fragment.
_RETENTION_SAFE_STAGE = "withheld"
_RETENTION_SAFE_FIELD = "withheld: secret gate tripped"


def _retention_labels_clean(stage, interpreter, source_commit):
    """Whole-input gate over the caller-supplied identity triple.

    Returns (clean, unavailable): clean False means a recognized trip (or an
    unavailable gate -- fail closed); unavailable True only when the gate
    itself could not be imported.
    """
    try:
        from omega_prime.grokbot import secret_guard
    except ImportError:
        return False, True
    try:
        secret_guard.assert_no_secrets(
            f"{stage}\n{interpreter}\n{source_commit}",
            "readiness-retention-labels",
        )
    except secret_guard.SecretLeakError:
        return False, False
    return True, False


def _guard_retention_field(value):
    """Identity-field gate: unchanged when clean, fixed safe value otherwise."""
    try:
        from omega_prime.grokbot import secret_guard
    except ImportError:
        return _RETENTION_SAFE_FIELD
    try:
        secret_guard.assert_no_secrets(str(value), "readiness-retention-label")
    except secret_guard.SecretLeakError:
        return _RETENTION_SAFE_FIELD
    return str(value)


def _guard_retention_stage(stage):
    """Stdout/record_exit stage label: unchanged when clean, fixed otherwise."""
    if _guard_retention_field(stage) == _RETENTION_SAFE_FIELD:
        return _RETENTION_SAFE_STAGE
    return str(stage)


def _guard_retention_metadata(document):
    """Whole-input gate over the final serialized failure metadata.

    The complete document reaches the unchanged ``assert_no_secrets`` gate
    BEFORE any write. A recognized trip (or an unavailable gate) returns a
    fixed safe document carrying the truthful observed exit -- never the
    rejected fragment, never a fabricated healthy report.
    """
    text = json.dumps(document, sort_keys=True) + "\n"
    try:
        from omega_prime.grokbot import secret_guard
    except ImportError:
        return _retention_metadata(
            _RETENTION_SAFE_STAGE,
            document.get("exit_code"),
            _RETENTION_REASON_UNAVAILABLE,
            _RETENTION_SAFE_FIELD,
            _RETENTION_SAFE_FIELD,
        )
    try:
        secret_guard.assert_no_secrets(text, "readiness-retention-metadata")
    except secret_guard.SecretLeakError:
        return _retention_metadata(
            _RETENTION_SAFE_STAGE,
            document.get("exit_code"),
            _RETENTION_REASON_WITHHELD,
            _RETENTION_SAFE_FIELD,
            _RETENTION_SAFE_FIELD,
        )
    return document


def _retention_admit(report):
    """Admit via the actual canonical readiness validator and roster.

    Lazy imports keep identity/run/summary modes stdlib-only. The canonical
    roster is loaded from the checkout itself, never from caller input, so an
    unknown caller fixture cannot self-vouch. Returns (True, None) on
    admission, (False, <candidate_error kind>) otherwise; an unavailable
    validator dependency refuses with 'unavailable' (fixed safe non-success).
    """
    try:
        from omega_prime.grokbot.local_validation import ROSTER_REL
        from omega_prime.grokbot.readiness import find_default_root, validate_report
        from omega_prime.tooling.roster import load_roster_names
    except ImportError:
        return False, "unavailable"
    try:
        canonical = load_roster_names(find_default_root() / ROSTER_REL)
    except OSError:
        return False, "unavailable"
    if not canonical:
        return False, "unavailable"
    try:
        validate_report(report, canonical)
    except (ValueError, TypeError, AttributeError):
        return False, "incomplete"
    return True, None


def _retention_effective(observed_exit):
    """Observed nonzero passes through; observed 0 on a rejected set becomes 1."""
    return observed_exit if observed_exit != 0 else 1


def _retention_json_object(pairs):
    """Reject ambiguous member names before canonical report validation."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def decide_retention(
    stage,
    observed_exit,
    interpreter,
    source_commit,
    candidate_text,
    candidate_error,
    gate_clean,
):
    """Pure retention decision over the full candidate input.

    observed_exit is the exact observed initial exit as int, or None when the
    stage was skipped/not-run (caller passes the literal unobserved token).
    candidate_text holds the full decoded candidate, or None when the file is
    missing, unreadable, inadmissible, undecodable, or the publication gate is
    unavailable (see candidate_error). gate_clean reports the full-input
    secret gate over that text.

    Returns (publish, document, effective_exit): publish True means document
    is the canonically validated report; otherwise document is failure
    metadata carrying exactly stage, exit_code, reason, interpreter,
    source_commit, whole-input-gated before return. Rejection never becomes
    green: metadata records the observed exit truthfully (including 0, or
    null when unobserved) while the effective exit is nonzero. A recognized
    trip in the caller-supplied identity labels refuses publication even for
    an otherwise valid candidate: fixed safe values carry the truthful
    observed exit with a nonzero effective exit.
    """
    labels_clean, labels_unavailable = _retention_labels_clean(
        stage, interpreter, source_commit
    )
    if not labels_clean:
        reason = (
            _RETENTION_REASON_UNAVAILABLE
            if labels_unavailable
            else _RETENTION_REASON_WITHHELD
        )
        return (
            False,
            _guard_retention_metadata(
                _retention_metadata(
                    _guard_retention_stage(stage),
                    observed_exit,
                    reason,
                    _guard_retention_field(interpreter),
                    _guard_retention_field(source_commit),
                )
            ),
            1 if observed_exit is None else _retention_effective(observed_exit),
        )
    if observed_exit is None:
        return (
            False,
            _guard_retention_metadata(
                _retention_metadata(
                    stage,
                    None,
                    f"{stage} stage unobserved: no execution observed",
                    interpreter,
                    source_commit,
                )
            ),
            1,
        )
    if candidate_error is not None or candidate_text is None:
        if candidate_error is None:
            reason = _RETENTION_REASON_MISSING
        else:
            reason = {
                "missing": _RETENTION_REASON_MISSING,
                "unreadable": _RETENTION_REASON_UNREADABLE,
                "inadmissible": _RETENTION_REASON_INADMISSIBLE,
                "unavailable": _RETENTION_REASON_UNAVAILABLE,
                "undecodable": _RETENTION_REASON_UNDECODABLE,
            }.get(candidate_error, _RETENTION_REASON_MISSING)
        return (
            False,
            _guard_retention_metadata(
                _retention_metadata(
                    stage, observed_exit, reason, interpreter, source_commit
                )
            ),
            _retention_effective(observed_exit),
        )
    try:
        report = json.loads(candidate_text, object_pairs_hook=_retention_json_object)
    except (ValueError, RecursionError):
        return (
            False,
            _guard_retention_metadata(
                _retention_metadata(
                    stage,
                    observed_exit,
                    _RETENTION_REASON_UNDECODABLE,
                    interpreter,
                    source_commit,
                )
            ),
            _retention_effective(observed_exit),
        )
    admitted, admit_error = _retention_admit(report)
    if not admitted:
        reason = (
            _RETENTION_REASON_UNAVAILABLE
            if admit_error == "unavailable"
            else _RETENTION_REASON_INCOMPLETE
        )
    elif not gate_clean:
        reason = _RETENTION_REASON_WITHHELD
    elif (observed_exit == 0) != (report["ok"] is True):
        reason = _RETENTION_REASON_INCOHERENT
    else:
        return True, report, observed_exit
    return (
        False,
        _guard_retention_metadata(
            _retention_metadata(
                stage, observed_exit, reason, interpreter, source_commit
            )
        ),
        _retention_effective(observed_exit),
    )


def _write_retained_text(path, data):
    """Atomically replace path with data via the canonical fs primitive.

    Lazy import keeps identity/run/summary modes stdlib-only; the canonical
    helper uses the same backend (sibling temp file, fsync, rename) at mode
    0600. An unavailable helper fails closed in the caller.
    """
    from omega_prime.tooling.fs import atomic_write_text

    atomic_write_text(path, data)


def _parse_retention_args(args):
    """Parse retention flags into a dict; None when usage is violated."""
    opts = {}
    index = 0
    while index < len(args):
        flag = args[index]
        if flag not in _RETENTION_FLAGS or index + 1 >= len(args):
            return None
        if flag in opts:
            return None
        opts[flag] = args[index + 1]
        index += 2
    if set(opts) != set(_RETENTION_FLAGS):
        return None
    if not opts["--stage"]:
        return None
    if not opts["--interpreter"]:
        return None
    if not opts["--source-commit"]:
        return None
    return opts


def _under(base, path):
    """True when path resolves inside base (both absolute)."""
    try:
        return os.path.commonpath([base, path]) == base
    except (ValueError, OSError):
        return False


def _gate_candidate_text(text):
    """Full-input secret gate over candidate text; returns (clean, unavailable)."""
    try:
        from omega_prime.grokbot import secret_guard
    except ImportError:
        return False, True
    try:
        secret_guard.assert_no_secrets(text, "readiness-report")
    except secret_guard.SecretLeakError:
        return False, False
    return True, False


def _parse_observed_exit(raw):
    """Exact observed exit as int, or None for the unobserved token."""
    if raw in ("", RETENTION_UNOBSERVED_TOKEN):
        return None
    return int(raw, 10)


def _read_candidate_text(path):
    """Full decoded candidate, or (None, error) when unavailable."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "undecodable"


def cmd_retention(args):
    """Gate the readiness candidate and retain exactly one sanitized document.

    Writes either the validated approved report (verbatim) or a truthful
    failure-metadata object to the owned RUNNER_TEMP output path, records the
    actual retention outcome via record_exit with the observed status noted
    alongside, and returns it. Exact observed nonzero exits pass through;
    guard rejection or admission failure never becomes green while the
    observed exit stays recorded truthfully.
    """
    opts = _parse_retention_args(args)
    if opts is None:
        print(
            "usage: ci_receipt.py retention --stage STAGE --observed-exit N|unobserved "
            "--interpreter TEXT --source-commit TEXT --candidate PATH --output PATH",
            file=sys.stderr,
        )
        return 2
    runner_temp = os.environ.get("RUNNER_TEMP")
    if not runner_temp:
        print("ci_receipt.py retention requires RUNNER_TEMP", file=sys.stderr)
        return 2
    root = os.path.realpath(runner_temp)
    output = os.path.realpath(opts["--output"])
    candidate_path = os.path.realpath(opts["--candidate"])
    if not _under(root, output) or candidate_path == output:
        print(
            "ci_receipt.py retention refuses paths outside RUNNER_TEMP",
            file=sys.stderr,
        )
        return 2
    try:
        observed_exit = _parse_observed_exit(opts["--observed-exit"])
    except ValueError:
        print(
            "ci_receipt.py retention needs an integer or unobserved exit",
            file=sys.stderr,
        )
        return 2
    if not _under(root, candidate_path):
        candidate_text, candidate_error = None, "inadmissible"
    else:
        candidate_text, candidate_error = _read_candidate_text(candidate_path)
    gate_clean, unavailable = True, False
    if candidate_error is None:
        gate_clean, unavailable = _gate_candidate_text(candidate_text)
        if unavailable:
            candidate_text, candidate_error = None, "unavailable"
    publish, document, effective = decide_retention(
        opts["--stage"],
        observed_exit,
        opts["--interpreter"],
        opts["--source-commit"],
        candidate_text,
        candidate_error,
        gate_clean,
    )
    safe_stage = _guard_retention_stage(opts["--stage"])
    try:
        if publish:
            _write_retained_text(output, candidate_text)
        else:
            _write_retained_text(output, json.dumps(document, sort_keys=True) + "\n")
    except (OSError, ImportError):
        print(
            "ci_receipt.py retention could not write retained evidence",
            file=sys.stderr,
        )
        return record_exit(safe_stage + "_retention", 1)
    observed_note = "null" if observed_exit is None else str(observed_exit)
    print(
        f"observed_stage={safe_stage} observed_exit_code={observed_note}",
        flush=True,
    )
    return record_exit(safe_stage + "_retention", effective)


def cmd_summary():
    """Always-run per-job receipt over STEPS_JSON with optional JUnit guard."""
    steps = json.loads(os.environ["STEPS_JSON"])
    job_status = os.environ["JOB_STATUS"]
    states = {"success": "passed", "failure": "failed", "cancelled": "cancelled"}
    stages, blocker = [], None
    for name in os.environ["STAGES"].split():
        step = steps.get(name) or {}
        outcome = step.get("outcome")
        exit_code = (step.get("outputs") or {}).get("exit_code")
        if outcome in states:
            state, reason = states[outcome], f"Actions outcome {outcome}"
        elif outcome == "skipped" and blocker:
            state, reason = (
                "not_run",
                f"not started: prerequisite {blocker} did not pass",
            )
        elif outcome == "skipped" and job_status == "cancelled":
            state, reason = "not_run", "not started: job cancelled"
        elif outcome == "skipped":
            state, reason = "skipped", "Actions skipped this stage"
        else:
            state, reason = "not_run", "no Actions outcome recorded"
        if state != "passed" and blocker is None:
            blocker = name
        stages.append(
            {
                "stage": name,
                "state": state,
                "outcome": outcome,
                "conclusion": step.get("conclusion"),
                # WR-01 fix: test for absence, so an observed numeric 0 stays 0
                # instead of collapsing to null like any other falsy value would.
                "exit_code": (
                    int(str(exit_code)) if exit_code not in (None, "") else None
                ),
                "reason": reason,
            }
        )
    summary: dict[str, Any] = {
        "job": os.environ.get("GITHUB_JOB"),
        "requested_python": os.environ["REQUESTED_PYTHON"],
        "job_status": job_status,
        "stages": stages,
    }
    guard_testcase = os.environ.get("GUARD_TESTCASE")
    if not guard_testcase:
        print(json.dumps(summary, sort_keys=True))
        if blocker is not None:
            sys.exit("receipt summary: a required stage did not pass")
        return 0
    suite_state = next(s["state"] for s in stages if s["stage"] == "suite")
    junit = os.path.join(os.environ["RUNNER_TEMP"], "phase48-suite.xml")
    guard = {
        "testcase": guard_testcase,
        "state": "not_run",
        "reason": f"suite stage {suite_state}",
    }
    if suite_state in ("passed", "failed") and not os.path.isfile(junit):
        guard["reason"] = "JUnit report not found"
    elif suite_state in ("passed", "failed"):
        root = ET.parse(junit).getroot()
        summary["suite_counts"] = {
            key: sum(int(node.get(key, 0)) for node in root.iter("testsuite"))
            for key in ("tests", "failures", "errors", "skipped")
        }
        results = [
            {child.tag for child in node} & {"failure", "error", "skipped"}
            for node in root.iter("testcase")
            if node.get("classname") == os.environ["GUARD_CLASSNAME"]
            and node.get("name") == os.environ["GUARD_NAME"]
        ]
        if not results:
            guard["reason"] = "named testcase absent from JUnit report"
        elif any(result & {"failure", "error"} for result in results):
            guard.update(state="failed", reason="named testcase failed")
        elif any("skipped" in result for result in results):
            guard.update(state="skipped", reason="named testcase skipped")
        else:
            guard.update(state="passed", reason="named testcase passed")
    summary["discord_guard"] = guard
    print(json.dumps(summary, sort_keys=True))
    if blocker is not None or guard["state"] != "passed":
        sys.exit("receipt summary: a required stage or the named guard did not pass")
    return 0


def main(argv):
    if len(argv) < 2:
        print(
            "usage: ci_receipt.py {identity|run STAGE -- CMD...|summary|retention ...}",
            file=sys.stderr,
        )
        return 2
    mode = argv[1]
    if mode == "identity":
        return cmd_identity()
    if mode == "run":
        if "--" not in argv[3:]:
            print("usage: ci_receipt.py run STAGE -- CMD [ARGS...]", file=sys.stderr)
            return 2
        sep = argv.index("--", 3)
        stage = argv[2]
        command = argv[sep + 1 :]
        if not stage or not command:
            print("usage: ci_receipt.py run STAGE -- CMD [ARGS...]", file=sys.stderr)
            return 2
        return cmd_run(stage, command)
    if mode == "summary":
        return cmd_summary()
    if mode == "retention":
        return cmd_retention(argv[2:])
    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
