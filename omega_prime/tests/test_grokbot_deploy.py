# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""The deployment kit: render four targets, check them, and pin the committed files."""

from __future__ import annotations

import configparser
import hashlib
import json
import re
import socket
from pathlib import Path
from typing import Any

import pytest
import yaml

from omega_prime.grokbot import deploy
from omega_prime.grokbot.deploy import (
    FINDING_CATALOG,
    TARGETS,
    DeployError,
    check,
    render,
)
from omega_prime.grokbot.manifest import find_repo_root

REPO = find_repo_root()


def _rendered(tmp_path: Path, target: str, **kwargs: Any) -> Path:
    out = tmp_path / target
    render(target, out, **kwargs)
    return out


def _main_file(directory: Path) -> Path:
    names = {
        "docker": "Dockerfile",
        "compose": "compose.yaml",
        "systemd": "omega-prime-mcp.service",
        "k8s": "omega-prime-mcp.yaml",
    }
    for name in names.values():
        if (directory / name).is_file():
            return directory / name
    raise AssertionError(f"nothing rendered in {directory}")


def _ids(findings: list[deploy.Finding], severity: str | None = None) -> set[str]:
    return {f.id for f in findings if severity is None or f.severity == severity}


def _mutate_file(path: Path, old: str, new: str, *, regex: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    if regex:
        mutated, count = re.subn(old, new, text, count=1, flags=re.MULTILINE)
        assert count == 1, f"pattern {old!r} not found"
    else:
        assert old in text, f"{old!r} not found"
        mutated = text.replace(old, new)
    path.write_text(mutated, encoding="utf-8")


# ----------------------------------------------------------------------- render


@pytest.mark.parametrize("target", TARGETS)
def test_render_is_deterministic_and_ends_with_a_newline(
    tmp_path: Path, target: str
) -> None:
    first = render(target, tmp_path / "a")
    second = render(target, tmp_path / "b")
    assert [p.name for p in first] == [p.name for p in second]
    for one, two in zip(first, second, strict=True):
        assert one.read_bytes() == two.read_bytes()
        assert one.read_bytes().endswith(b"\n")
        assert b"\r" not in one.read_bytes()
    # Re-rendering over generated files is allowed and changes nothing.
    again = render(target, tmp_path / "a")
    for one, two in zip(first, again, strict=True):
        assert one.read_bytes() == two.read_bytes()


@pytest.mark.parametrize("target", TARGETS)
def test_rendered_output_has_zero_findings(tmp_path: Path, target: str) -> None:
    out = _rendered(tmp_path, target)
    assert check(out) == []
    for path in out.iterdir():
        if path.name != ".dockerignore":
            assert check(path) == []


@pytest.mark.parametrize("target", TARGETS)
def test_rendered_output_with_custom_inputs_has_zero_findings(
    tmp_path: Path, target: str
) -> None:
    out = _rendered(
        tmp_path,
        target,
        name="grok-bot-2",
        image="registry.example.com:5000/team/omega-prime-mcp:1.2.3",
        port=9100,
        public_url="https://mcp.example.com/base?x=1%202&y=$z",
        uid=20000,
    )
    assert check(out) == []


@pytest.mark.parametrize("target", TARGETS)
def test_public_url_reaches_every_target(tmp_path: Path, target: str) -> None:
    url = "https://mcp.example.com"
    out = _rendered(tmp_path, target, public_url=url)
    text = _main_file(out).read_text(encoding="utf-8")
    assert "--public-url" in text
    assert url in text
    plain = _main_file(_rendered(tmp_path / "plain", target)).read_text(
        encoding="utf-8"
    )
    assert "--public-url" not in plain


def test_no_secret_is_ever_rendered(tmp_path: Path) -> None:
    for target in TARGETS:
        out = _rendered(tmp_path, target)
        for path in out.iterdir():
            text = path.read_text(encoding="utf-8")
            assert "MCP_AUTH_TOKEN" not in text
            assert not re.search(r"--token(?!-file)", text)
            assert not re.search(r"\b[0-9a-f]{32,}\b", text)
        main = _main_file(out).read_text(encoding="utf-8")
        assert "omega_prime_token" in main


def test_compose_hardening(tmp_path: Path) -> None:
    text = _main_file(_rendered(tmp_path, "compose")).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    svc = doc["services"]["omega-prime-mcp"]
    assert svc["read_only"] is True
    assert svc["tmpfs"] == ["/tmp"]
    assert svc["cap_drop"] == ["ALL"]
    assert svc["security_opt"] == ["no-new-privileges:true"]
    assert svc["user"] == "10001:10001"
    assert svc["ports"] == ["127.0.0.1:8000:8000"]
    assert svc["stop_grace_period"] == "30s"
    assert svc["restart"] == "unless-stopped"
    assert svc["mem_limit"] and svc["pids_limit"]
    assert svc["healthcheck"]["test"][0] == "CMD"
    assert svc["secrets"] == ["omega_prime_token"]
    assert doc["secrets"]["omega_prime_token"] == {
        "file": "./secrets/omega_prime_token"
    }
    assert svc["volumes"] == ["omega-prime-mcp-state:/var/lib/omega-prime"]
    assert "omega-prime-mcp-state" in doc["volumes"]


def test_systemd_unit_parses_and_is_hardened(tmp_path: Path) -> None:
    text = _main_file(_rendered(tmp_path, "systemd")).read_text(encoding="utf-8")
    parser = configparser.ConfigParser(strict=True, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    service = parser["Service"]
    assert service["Type"] == "exec"
    assert service["DynamicUser"] == "yes"
    assert service["StateDirectory"] == "omega-prime"
    assert service["LoadCredential"] == "omega_prime_token:/etc/omega-prime/token"
    assert "--token-file %d/omega_prime_token" in service["ExecStart"]
    for key, value in {
        "NoNewPrivileges": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateTmp": "yes",
        "PrivateDevices": "yes",
        "ProtectKernelTunables": "yes",
        "ProtectControlGroups": "yes",
        "RestrictAddressFamilies": "AF_INET AF_INET6 AF_UNIX",
        "RestrictNamespaces": "yes",
        "LockPersonality": "yes",
        "CapabilityBoundingSet": "",
        "SystemCallFilter": "@system-service",
        "TimeoutStopSec": "30",
        "Restart": "on-failure",
    }.items():
        assert service[key] == value
    assert service["MemoryMax"] and service["TasksMax"]
    assert parser["Install"]["WantedBy"] == "multi-user.target"


def test_systemd_escapes_specifiers_in_user_data(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "systemd", public_url="https://h.example/a%20b?c=$d")
    text = _main_file(out).read_text(encoding="utf-8")
    assert "https://h.example/a%%20b?c=$$d" in text


def test_k8s_hardening(tmp_path: Path) -> None:
    text = _main_file(_rendered(tmp_path, "k8s")).read_text(encoding="utf-8")
    docs = [d for d in yaml.safe_load_all(text) if d]
    assert [d["kind"] for d in docs] == [
        "Deployment",
        "Service",
        "NetworkPolicy",
        "PodDisruptionBudget",
    ]
    deployment, service, policy, pdb = docs
    pod = deployment["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["terminationGracePeriodSeconds"] == 45
    assert pod["securityContext"]["runAsUser"] == 10001
    assert pod["securityContext"]["fsGroup"] == 10001
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    (container,) = pod["containers"]
    sec = container["securityContext"]
    assert sec["readOnlyRootFilesystem"] is True
    assert sec["allowPrivilegeEscalation"] is False
    assert sec["capabilities"] == {"drop": ["ALL"]}
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["lifecycle"]["preStop"]["exec"]["command"] == ["sleep", "5"]
    assert container["resources"]["requests"] and container["resources"]["limits"]
    mounts = {m["mountPath"] for m in container["volumeMounts"]}
    assert {"/tmp", "/var/lib/omega-prime", "/run/secrets"} <= mounts
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert "emptyDir" in volumes["tmp"] and "emptyDir" in volumes["state"]
    assert volumes["token-src"]["secret"]["secretName"] == "omega-prime-token"
    (init,) = pod["initContainers"]
    assert init["securityContext"]["readOnlyRootFilesystem"] is True
    assert service["spec"]["type"] == "ClusterIP"
    (rule,) = policy["spec"]["ingress"]
    assert rule["from"] == [
        {"podSelector": {"matchLabels": {"omega-prime-mcp-client": "true"}}}
    ]
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    assert pdb["spec"]["maxUnavailable"] == 1


def test_k8s_grace_exceeds_prestop_plus_drain(tmp_path: Path) -> None:
    text = _main_file(_rendered(tmp_path, "k8s")).read_text(encoding="utf-8")
    deployment = next(yaml.safe_load_all(text))
    pod = deployment["spec"]["template"]["spec"]
    args = pod["containers"][0]["args"]
    grace = int(args[args.index("--shutdown-grace") + 1])
    prestop = int(pod["containers"][0]["lifecycle"]["preStop"]["exec"]["command"][1])
    assert pod["terminationGracePeriodSeconds"] > prestop + grace


@pytest.mark.parametrize("target", ["compose", "k8s"])
def test_subset_reader_matches_pyyaml_on_rendered_output(
    tmp_path: Path, target: str
) -> None:
    text = _main_file(
        _rendered(
            tmp_path,
            target,
            public_url="https://mcp.example.com/a b".replace(" ", "%20"),
        )
    ).read_text(encoding="utf-8")
    expected = [d for d in yaml.safe_load_all(text) if d is not None]
    assert deploy._load_yaml(text) == expected


def test_subset_reader_edge_cases() -> None:
    text = """\
# comment
a: 1
b:
- x
- y: [1, "two", {k: v}]
  z: 'it''s'
c: |
  line one
  line two
"q: k": null  # trailing
d: {}
e: []
"""
    assert deploy._load_yaml(text) == list(yaml.safe_load_all(text))
    for bad in ("a: &x 1\n", "a: *x\n", "<<: {a: 1}\n", "a: 1\na: 2\n", "\tb: 1\n"):
        with pytest.raises(deploy._YamlError):
            deploy._load_yaml(bad)


# ----------------------------------------------------------------------- check


def _drop_docker_healthcheck(path: Path) -> None:
    _mutate_file(path, r"^HEALTHCHECK .*\n  CMD .*\n", "", regex=True)


def _k8s_root(path: Path) -> None:
    _mutate_file(path, "runAsUser: 10001", "runAsUser: 0")
    _mutate_file(path, "runAsNonRoot: true", "runAsNonRoot: false")


def _add_k8s_secret(path: Path) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "---\napiVersion: v1\nkind: Secret\nmetadata:\n  name: leaked\n"
            "stringData:\n  omega_prime_token: whatever\n"
        )


_MUTATIONS: list[tuple[str, str, str, Any]] = [
    (
        "docker",
        "run-as-root",
        "root user",
        lambda p: _mutate_file(p, "USER 10001:10001", "USER root"),
    ),
    (
        "docker",
        "missing-healthcheck",
        "no healthcheck",
        _drop_docker_healthcheck,
    ),
    (
        "docker",
        "latest-tag",
        "latest base image",
        lambda p: _mutate_file(
            p,
            "FROM python:3.12-slim-bookworm AS runtime",
            "FROM python:latest AS runtime",
        ),
    ),
    (
        "docker",
        "inline-secret",
        "inline token env",
        lambda p: _mutate_file(
            p,
            "STOPSIGNAL SIGTERM",
            "ENV MCP_AUTH_TOKEN=hunter2hunter2\nSTOPSIGNAL SIGTERM",
        ),
    ),
    (
        "compose",
        "run-as-root",
        "root user",
        lambda p: _mutate_file(p, '"10001:10001"', '"0:0"'),
    ),
    (
        "compose",
        "privileged",
        "privileged",
        lambda p: _mutate_file(
            p, "    read_only: true\n", "    read_only: true\n    privileged: true\n"
        ),
    ),
    (
        "compose",
        "host-network",
        "host network",
        lambda p: _mutate_file(
            p, "    read_only: true\n", "    read_only: true\n    network_mode: host\n"
        ),
    ),
    (
        "compose",
        "host-pid",
        "host pid",
        lambda p: _mutate_file(
            p, "    read_only: true\n", "    read_only: true\n    pid: host\n"
        ),
    ),
    (
        "compose",
        "inline-secret",
        "inline token env",
        lambda p: _mutate_file(
            p,
            "    read_only: true\n",
            '    read_only: true\n    environment:\n      MCP_AUTH_TOKEN: "abc123def456"\n',
        ),
    ),
    (
        "compose",
        "missing-healthcheck",
        "no healthcheck",
        lambda p: _mutate_file(p, "    healthcheck:", "    xhealthcheck:"),
    ),
    (
        "compose",
        "latest-tag",
        "latest image",
        lambda p: _mutate_file(p, ":0.1.0", ":latest"),
    ),
    (
        "compose",
        "missing-no-new-privileges",
        "no-new-privileges removed",
        lambda p: _mutate_file(p, '["no-new-privileges:true"]', "[]"),
    ),
    (
        "systemd",
        "run-as-root",
        "no dynamic user",
        lambda p: _mutate_file(p, "DynamicUser=yes\n", ""),
    ),
    (
        "systemd",
        "inline-secret",
        "inline token env",
        lambda p: _mutate_file(
            p,
            "DynamicUser=yes\n",
            "DynamicUser=yes\nEnvironment=MCP_AUTH_TOKEN=abc123def456\n",
        ),
    ),
    (
        "systemd",
        "missing-no-new-privileges",
        "no NoNewPrivileges",
        lambda p: _mutate_file(p, "NoNewPrivileges=yes\n", ""),
    ),
    (
        "k8s",
        "run-as-root",
        "runAsUser 0",
        lambda p: _k8s_root(p),
    ),
    (
        "k8s",
        "privileged",
        "privileged",
        lambda p: _mutate_file(
            p,
            r"^(\s*)readOnlyRootFilesystem: true$",
            r"\1readOnlyRootFilesystem: true\n\1privileged: true",
            regex=True,
        ),
    ),
    (
        "k8s",
        "host-network",
        "hostNetwork",
        lambda p: _mutate_file(
            p,
            r"^(\s*)automountServiceAccountToken: false$",
            r"\1automountServiceAccountToken: false\n\1hostNetwork: true",
            regex=True,
        ),
    ),
    (
        "k8s",
        "host-pid",
        "hostPID",
        lambda p: _mutate_file(
            p,
            r"^(\s*)automountServiceAccountToken: false$",
            r"\1automountServiceAccountToken: false\n\1hostPID: true",
            regex=True,
        ),
    ),
    (
        "k8s",
        "missing-probe",
        "no readiness probe",
        lambda p: _mutate_file(p, "readinessProbe:", "xReadinessProbe:"),
    ),
    (
        "k8s",
        "latest-tag",
        "latest image",
        lambda p: _mutate_file(p, ":0.1.0", ":latest"),
    ),
    (
        "k8s",
        "privilege-escalation",
        "allowPrivilegeEscalation true",
        lambda p: _mutate_file(
            p, "allowPrivilegeEscalation: false", "allowPrivilegeEscalation: true"
        ),
    ),
    (
        "k8s",
        "missing-no-new-privileges",
        "allowPrivilegeEscalation missing",
        lambda p: _mutate_file(
            p, r"^\s*allowPrivilegeEscalation: false\n", "", regex=True
        ),
    ),
    (
        "k8s",
        "inline-secret",
        "inline token env",
        lambda p: _mutate_file(
            p,
            r"^(\s*)ports:$",
            '\\1env:\n\\1  - name: MCP_AUTH_TOKEN\n\\1    value: "abc123def456"\n\\1ports:',
            regex=True,
        ),
    ),
    ("k8s", "inline-secret", "Secret object", _add_k8s_secret),
]


@pytest.mark.parametrize(
    ("target", "expected", "label", "mutate"),
    _MUTATIONS,
    ids=[f"{m[0]}-{m[1]}-{m[2]}" for m in _MUTATIONS],
)
def test_each_mutation_yields_its_error_finding(
    tmp_path: Path, target: str, expected: str, label: str, mutate: Any
) -> None:
    del label
    out = _rendered(tmp_path, target)
    mutate(_main_file(out))
    findings = check(out)
    assert expected in _ids(findings, "error"), findings
    assert FINDING_CATALOG[expected][0] == "error"


def test_the_headline_mutations_have_distinct_error_ids() -> None:
    headline = {
        "run-as-root",
        "privileged",
        "host-network",
        "inline-secret",
        "missing-healthcheck",
        "missing-probe",
        "latest-tag",
        "missing-no-new-privileges",
    }
    seen = {m[1] for m in _MUTATIONS}
    assert headline <= seen
    assert len(headline) == 8


def test_secret_values_are_never_echoed(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "compose")
    _mutate_file(
        _main_file(out),
        "    read_only: true\n",
        '    read_only: true\n    environment:\n      MCP_AUTH_TOKEN: "abc123def456"\n',
    )
    findings = check(out)
    assert findings
    assert all("abc123def456" not in f.message for f in findings)


def test_references_are_not_inline_secrets(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "compose")
    _mutate_file(
        _main_file(out),
        "    read_only: true\n",
        "    read_only: true\n    environment:\n"
        '      OMEGA_PRIME_API_TOKEN: "${OMEGA_PRIME_API_TOKEN}"\n'
        "      SOME_TOKEN_FILE: /run/secrets/omega_prime_token\n",
    )
    assert "inline-secret" not in _ids(check(out))


def test_token_looking_literals_are_found_anywhere(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "docker")
    _mutate_file(
        out / "Dockerfile",
        "STOPSIGNAL SIGTERM",
        "LABEL note=ghp_" + "a" * 30 + "\nSTOPSIGNAL SIGTERM",
    )
    assert "inline-secret" in _ids(check(out), "error")


def test_token_argument_literal_is_an_inline_secret(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "systemd")
    _mutate_file(
        _main_file(out),
        "--shutdown-grace 20",
        "--shutdown-grace 20 --token s3cr3t-value",
    )
    assert "inline-secret" in _ids(check(out), "error")


def test_warnings_for_a_weaker_but_not_broken_k8s_workload(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "k8s")
    path = _main_file(out)
    _mutate_file(path, "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false")
    findings = check(out)
    assert _ids(findings, "error") == set()
    assert "writable-rootfs" in _ids(findings, "warning")


def test_missing_limits_and_network_policy_are_warnings(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "k8s")
    path = _main_file(out)
    text = path.read_text(encoding="utf-8")
    kept = [d for d in text.split("\n---\n") if 'kind: "NetworkPolicy"' not in d]
    path.write_text("\n---\n".join(kept), encoding="utf-8")
    _mutate_file(path, r"^( +)limits:\n(?:\1 +\S.*\n)+", "", regex=True)
    findings = check(out)
    assert _ids(findings, "error") == set()
    assert {"missing-network-policy", "missing-resource-limits"} <= _ids(
        findings, "warning"
    )


def test_network_policy_in_a_sibling_file_satisfies_the_directory_check(
    tmp_path: Path,
) -> None:
    out = _rendered(tmp_path, "k8s")
    path = _main_file(out)
    docs = path.read_text(encoding="utf-8").split("\n---\n")
    policy = next(d for d in docs if 'kind: "NetworkPolicy"' in d)
    path.write_text(
        "\n---\n".join(d for d in docs if d is not policy), encoding="utf-8"
    )
    assert "missing-network-policy" in _ids(check(out))
    (out / "policy.yaml").write_text(policy, encoding="utf-8")
    assert "missing-network-policy" not in _ids(check(out))


def test_compose_weaknesses_are_warnings(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "compose")
    path = _main_file(out)
    _mutate_file(path, "    read_only: true\n", "")
    _mutate_file(path, '    mem_limit: "1g"\n', "")
    findings = check(out)
    assert _ids(findings, "error") == set()
    assert {"writable-rootfs", "missing-resource-limits"} <= _ids(findings, "warning")


def test_pod_level_security_context_is_inherited(tmp_path: Path) -> None:
    manifest = tmp_path / "pod.yaml"
    manifest.write_text(
        """\
apiVersion: v1
kind: Pod
metadata:
  name: p
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
  containers:
  - name: c
    image: example.com/app:1.0
    livenessProbe: {httpGet: {path: /healthz, port: 80}}
    readinessProbe: {httpGet: {path: /readyz, port: 80}}
    securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true}
    resources: {limits: {memory: 1Gi}}
""",
        encoding="utf-8",
    )
    assert _ids(check(manifest), "error") == set()


def test_unparseable_and_unrecognized_files(tmp_path: Path) -> None:
    anchors = tmp_path / "bad.yaml"
    anchors.write_text("a: &x 1\nb: *x\n", encoding="utf-8")
    assert _ids(check(anchors), "error") == {"unparseable"}
    other = tmp_path / "notes.txt"
    other.write_text("hello\n", encoding="utf-8")
    assert _ids(check(other)) == {"unrecognized-file"}
    in_dir = tmp_path / "dir"
    in_dir.mkdir()
    (in_dir / "notes.txt").write_text("hello\n", encoding="utf-8")
    (in_dir / "values.yaml").write_text("replicas: 2\n", encoding="utf-8")
    assert check(in_dir) == []
    with pytest.raises(FileNotFoundError):
        check(tmp_path / "missing")


def test_findings_are_sorted_and_stable(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "compose")
    path = _main_file(out)
    _mutate_file(path, '"10001:10001"', '"0:0"')
    _mutate_file(path, "    read_only: true\n", "")
    first = check(out)
    assert first == check(out)
    assert [f.severity for f in first] == sorted(
        (f.severity for f in first), key=lambda s: s != "error"
    )


# ------------------------------------------------------------- inputs and writes


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "Upper"},
        {"name": "under_score"},
        {"name": "-lead"},
        {"name": "a" * 64},
        {"name": ""},
        {"name": "x\nevil: true"},
        {"image": "has space:1"},
        {"image": "img:"},
        {"image": "img:1;rm -rf /"},
        {"image": 'img:"1'},
        {"image": ""},
        {"port": 0},
        {"port": 65536},
        {"port": True},
        {"uid": 999},
        {"uid": 0},
        {"public_url": "ftp://example.com"},
        {"public_url": "https://exa mple.com"},
        {"public_url": 'https://example.com/"quote'},
        {"public_url": "https://example.com/\nnewline"},
    ],
)
@pytest.mark.parametrize("target", TARGETS)
def test_invalid_inputs_are_rejected_before_anything_is_written(
    tmp_path: Path, target: str, kwargs: dict[str, Any]
) -> None:
    out = tmp_path / "out"
    with pytest.raises(DeployError):
        render(target, out, **kwargs)
    assert not out.exists()


def test_unknown_target_and_file_out_dir(tmp_path: Path) -> None:
    with pytest.raises(DeployError):
        render("nomad", tmp_path / "x")
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(DeployError):
        render("docker", blocker)


def test_render_never_overwrites_a_foreign_file_without_force(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    foreign = out / "Dockerfile"
    foreign.write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(DeployError):
        render("docker", out)
    assert foreign.read_text(encoding="utf-8") == "FROM scratch\n"
    assert not (out / ".dockerignore").exists()
    render("docker", out, overwrite=True)
    assert "USER 10001:10001" in foreign.read_text(encoding="utf-8")


def test_rendered_files_are_world_readable_and_not_executable(tmp_path: Path) -> None:
    for target in TARGETS:
        for path in render(target, tmp_path / target):
            assert path.stat().st_mode & 0o777 == 0o644


def test_custom_name_and_uid_flow_into_the_artifacts(tmp_path: Path) -> None:
    paths = render("k8s", tmp_path / "k", name="grok-2", uid=20000)
    assert paths[0].name == "grok-2.yaml"
    text = paths[0].read_text(encoding="utf-8")
    assert "runAsUser: 20000" in text and "fsGroup: 20000" in text
    docker = (render("docker", tmp_path / "d", uid=20000)[0]).read_text(
        encoding="utf-8"
    )
    assert "USER 20000:20000" in docker


# ------------------------------------------------------------ committed artifacts


def test_committed_image_definition_matches_render(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "docker")
    for name in ("Dockerfile", ".dockerignore"):
        committed = (REPO / name).read_bytes()
        assert committed == (out / name).read_bytes(), (
            f"{name} drifted from `deploy.render('docker')`; run "
            "`python -m omega_prime.grokbot.deploy render --target docker --out . --force`"
        )


# ------------------------------------------------------------------------- CLI


def test_cli_render_and_check_roundtrip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for target in TARGETS:
        out = tmp_path / target
        assert deploy.main(["render", "--target", target, "--out", str(out)]) == 0
        printed = capsys.readouterr().out.splitlines()
        assert printed and all(Path(line).exists() for line in printed)
        assert deploy.main(["check", str(out)]) == 0
        assert "0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_cli_check_exit_codes_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _rendered(tmp_path, "compose")
    capsys.readouterr()
    _mutate_file(_main_file(out), '"10001:10001"', '"0:0"')
    assert deploy.main(["check", str(out), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["errors"] >= 1
    assert {"id", "severity", "file", "message"} == set(payload["findings"][0])
    assert any(f["id"] == "run-as-root" for f in payload["findings"])

    warn = _rendered(tmp_path / "w", "compose")
    capsys.readouterr()
    _mutate_file(_main_file(warn), "    read_only: true\n", "")
    assert deploy.main(["check", str(warn), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["warnings"] >= 1


def test_cli_check_rejects_weakened_image_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _rendered(tmp_path, "docker")
    capsys.readouterr()
    _mutate_file(out / "Dockerfile", "USER 10001:10001", "USER 0:0")
    assert deploy.main(["check", str(out), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["errors"] >= 1
    assert any(f["id"] == "run-as-root" for f in payload["findings"])


def test_cli_usage_errors_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert deploy.main([]) == 2
    assert deploy.main(["render", "--target", "nomad", "--out", str(tmp_path)]) == 2
    assert deploy.main(["render", "--target", "docker"]) == 2
    assert deploy.main(["render", "--target", "docker", "--out", str(blocker)]) == 2
    assert (
        deploy.main(
            [
                "render",
                "--target",
                "docker",
                "--out",
                str(tmp_path / "o"),
                "--port",
                "0",
            ]
        )
        == 2
    )
    assert deploy.main(["check", str(tmp_path / "missing")]) == 2
    assert "deploy:" in capsys.readouterr().err


def test_cli_force_replaces_foreign_files(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    args = ["render", "--target", "docker", "--out", str(out)]
    assert deploy.main(args) == 2
    assert deploy.main([*args, "--force"]) == 0


def test_cli_passes_name_image_port_and_public_url(tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = deploy.main(
        [
            "render",
            "--target",
            "k8s",
            "--out",
            str(out),
            "--name",
            "bot",
            "--image",
            "example.com/bot:2.0",
            "--port",
            "9000",
            "--public-url",
            "https://bot.example.com",
        ]
    )
    assert code == 0
    text = (out / "bot.yaml").read_text(encoding="utf-8")
    assert "example.com/bot:2.0" in text
    assert "containerPort: 9000" in text
    assert "https://bot.example.com" in text


def test_promotion_without_staging_or_approval_warns_twice(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "k8s")
    assert check(out) == []
    (out / "promotion.yaml").write_text("promotion: true\n", encoding="utf-8")
    findings = check(out)
    assert _ids(findings, "error") == set()
    assert _ids(findings, "warning") == {
        "promote-without-staging-evidence",
        "missing-promotion-approval-record",
    }
    assert len(findings) == 2
    assert FINDING_CATALOG["promote-without-staging-evidence"][0] == "warning"
    assert FINDING_CATALOG["missing-promotion-approval-record"][0] == "warning"
    assert deploy.main(["check", str(out)]) == 0


def test_promotion_with_staging_and_approval_is_quiet(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "k8s")
    (out / "promotion.yaml").write_text("promotion: true\n", encoding="utf-8")
    assert "promote-without-staging-evidence" in _ids(check(out), "warning")
    (out / "staging-receipt.json").write_text('{"ok": true}\n', encoding="utf-8")
    (out / "approval.json").write_text('{"approver": "oncall"}\n', encoding="utf-8")
    assert check(out) == []


# ------------------------------------------------- staged rollout and promotion

_DRILL_DIGEST = "sha256:" + hashlib.sha256(b"omega-prime-80-02-drill").hexdigest()
_OTHER_DIGEST = "sha256:" + hashlib.sha256(b"omega-prime-80-02-other").hexdigest()


def _promotion_dir(tmp_path: Path, *, digest: str = _DRILL_DIGEST) -> Path:
    out = _rendered(tmp_path, "k8s")
    (out / "promotion.yaml").write_text("promotion: true\n", encoding="utf-8")
    (out / "staging-receipt.json").write_text('{"ok": true}\n', encoding="utf-8")
    (out / "approval.json").write_text('{"approver": "oncall"}\n', encoding="utf-8")
    (out / "image.digest").write_text(digest + "\n", encoding="utf-8")
    return out


def _evidence(out: Path) -> list[dict[str, Any]]:
    paths = sorted(out.glob("*promotion*evidence*.json"))
    assert paths, "expected a promotion evidence file"
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_promote_staged_without_staging_evidence_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(deploy.ROLLOUT_ENV_VAR, raising=False)
    out = _rendered(tmp_path, "k8s")
    (out / "promotion.yaml").write_text("promotion: true\n", encoding="utf-8")
    approval = out / "approval.json"
    approval.write_text('{"approver": "oncall"}\n', encoding="utf-8")
    assert deploy.promote(_DRILL_DIGEST, out, approval) == 1
    assert list(out.glob("*promotion*evidence*.json")) == []


def test_promote_staged_success_writes_evidence_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(deploy.ROLLOUT_ENV_VAR, raising=False)
    out = _promotion_dir(tmp_path)
    assert deploy.promote(_DRILL_DIGEST, out, out / "approval.json") == 0
    first = _evidence(out)[-1]
    assert first["promoted_digest"] == _DRILL_DIGEST
    assert first["previous_digest"] is None
    assert first["approval"]["content"]
    assert "timestamp" in first
    (out / "image.digest").write_text(_OTHER_DIGEST + "\n", encoding="utf-8")
    assert deploy.promote(_OTHER_DIGEST, out, out / "approval.json") == 0
    second = _evidence(out)[-1]
    assert second["promoted_digest"] == _OTHER_DIGEST
    assert second["previous_digest"] == _DRILL_DIGEST


def test_promote_digest_mismatch_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(deploy.ROLLOUT_ENV_VAR, raising=False)
    out = _promotion_dir(tmp_path)
    assert deploy.promote(_OTHER_DIGEST, out, out / "approval.json") == 1
    assert list(out.glob("*promotion*evidence*.json")) == []


def test_promote_direct_requires_env_and_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _promotion_dir(tmp_path)
    approval = out / "approval.json"
    monkeypatch.delenv(deploy.ROLLOUT_ENV_VAR, raising=False)
    assert deploy.promote(_DRILL_DIGEST, out, approval, direct=True) == 1
    monkeypatch.setenv(deploy.ROLLOUT_ENV_VAR, "direct")
    assert deploy.promote(_DRILL_DIGEST, out, approval) == 1
    assert deploy.promote(_DRILL_DIGEST, out, approval, direct=True) == 0
    assert _evidence(out)[-1]["rollout"] == "direct"


def test_healthcheck_dead_port_fails(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "k8s", port=_free_port())
    assert deploy.healthcheck("k8s", out) == 1


def test_healthcheck_unresolvable_dir_exits_2(tmp_path: Path) -> None:
    assert deploy.healthcheck("k8s", tmp_path / "missing") == 2
    assert deploy.healthcheck("nope", tmp_path) == 2


def test_promotion_warn_then_clear_evidence_sequence(tmp_path: Path) -> None:
    out = _rendered(tmp_path, "k8s")
    (out / "promotion.yaml").write_text("promotion: true\n", encoding="utf-8")
    assert _ids(check(out), "warning") == {
        "promote-without-staging-evidence",
        "missing-promotion-approval-record",
    }
    assert deploy.main(["check", str(out)]) == 0
    (out / "staging-receipt.json").write_text('{"ok": true}\n', encoding="utf-8")
    (out / "approval.json").write_text('{"approver": "oncall"}\n', encoding="utf-8")
    assert "promote-without-staging-evidence" not in _ids(check(out), "warning")
    assert "missing-promotion-approval-record" not in _ids(check(out), "warning")
