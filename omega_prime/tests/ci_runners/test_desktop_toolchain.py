"""00056: the runner image's toolchain layer stays pinned, verified and wired.

Static checks on ``deploy/ci-runner-desktop``: every download is checksum
verified, every pin the self-check reads exists, Python CLIs come from
hash-locked requirement files, and the job environment (runner-env) points
the setup-* actions at the read-only tool cache. The image build itself runs
``toolchain-check.sh`` as the job identity; this keeps the inputs honest.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DESKTOP = REPO / "deploy" / "ci-runner-desktop"
DOCKERFILE = (DESKTOP / "Dockerfile").read_text()
CHECK = DESKTOP / "toolchain-check.sh"
RUNNER_ENV = DESKTOP / "runner-env"
SUPERVISOR_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def args() -> dict[str, str]:
    return dict(re.findall(r"^ARG (\w+)=(\S+)$", DOCKERFILE, re.M))


def runner_env() -> dict[str, str]:
    out = {}
    for line in RUNNER_ENV.read_text().splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_every_checksum_pin_is_a_full_digest() -> None:
    pins = {k: v for k, v in args().items() if k.endswith(("_SHA256", "_SHA512"))}
    assert len(pins) >= 14
    for name, value in pins.items():
        size = 64 if name.endswith("_SHA256") else 128
        assert re.fullmatch(rf"[0-9a-f]{{{size}}}", value), name
        # ...and each one is actually checked by sha256sum/sha512sum -c.
        assert "${" + name + "}" in DOCKERFILE, f"{name} is never verified"


def test_every_toolchain_download_is_verified_in_its_layer() -> None:
    start = DOCKERFILE.index("# --- 3b.")
    end = DOCKERFILE.index("# --- 4.")
    for layer in DOCKERFILE[start:end].split("\nRUN ")[1:]:
        downloads = len(re.findall(r"curl -fsSL -o", layer))
        if downloads:
            assert re.search(r"sha(256|512)sum -c -", layer), layer[:120]


def test_apt_comes_from_a_dated_snapshot() -> None:
    assert re.fullmatch(r"\d{8}T\d{6}Z", args()["APT_SNAPSHOT"])
    section = DOCKERFILE[DOCKERFILE.index("# --- 3b.") : DOCKERFILE.index("# --- 4.")]
    for line in section.splitlines():
        if "apt-get update" in line or "apt-get install" in line:
            assert '--snapshot "${APT_SNAPSHOT}"' in line, line


def test_python_clis_are_hash_locked() -> None:
    reqs = sorted((DESKTOP / "requirements").glob("*.txt"))
    assert {p.stem for p in reqs} == {"semgrep", "bandit", "podman-compose"}
    for req in reqs:
        text = req.read_text()
        pins = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", text, re.M)
        assert pins and any(n == req.stem for n, _ in pins), req.name
        blocks = [b for b in re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text) if "==" in b]
        assert all("--hash=sha256:" in b for b in blocks), req.name
    assert "--require-hashes --no-deps --only-binary=:all:" in DOCKERFILE


def test_runner_env_points_jobs_at_the_shared_tools() -> None:
    env = runner_env()
    assert (
        env["RUNNER_TOOL_CACHE"]
        == env["AGENT_TOOLSDIRECTORY"]
        == "/opt/hostedtoolcache"
    )
    assert env["DOTNET_ROOT"] == env["DOTNET_INSTALL_DIR"] == "/usr/share/dotnet"
    assert env["ANDROID_HOME"] == env["ANDROID_SDK_ROOT"] == "/opt/android-sdk"
    assert env["JAVA_HOME"].startswith("/usr/lib/jvm/java-17-")
    assert env["RUSTUP_HOME"] == "/opt/rust/rustup"
    assert "CARGO_HOME" not in env  # cargo's registry stays under the job's HOME
    assert env["PATH"].endswith(SUPERVISOR_PATH)
    for part in (
        "/opt/rust/cargo/bin",
        "/usr/lib/postgresql/16/bin",
        "/usr/share/dotnet",
    ):
        assert part in env["PATH"].split(":")
    # The image ENV and runner-env agree (same PATH for the agent and jobs).
    image_path = re.search(r"^\s+PATH=(\S+)$", DOCKERFILE, re.M)
    assert image_path and image_path.group(1) == env["PATH"]
    # No secrets in the job environment file.
    assert not any(re.search(r"TOKEN|SECRET|PASSWORD|KEY", k) for k in env)
    assert (
        "COPY deploy/ci-runner-desktop/runner-env /opt/actions-runner/.env"
        in DOCKERFILE
    )


def test_self_check_runs_as_the_job_and_reads_every_pin() -> None:
    assert (
        '/opt/omega-ci/toolchain-check.sh --as-job "${JOB_UID}" "${JOB_GID}"'
        in DOCKERFILE
    )
    text = CHECK.read_text()
    written = re.findall(
        r"(\w+) \"\$\{(\w+)\}\"",
        DOCKERFILE[DOCKERFILE.index("toolchain-versions") - 2000 :],
    )
    keys = {k for k, _ in written}
    for key in (
        "GH",
        "GITLEAKS",
        "TRIVY",
        "GREPTIME",
        "PY310",
        "PY312",
        "NODE22",
        "NODE24",
        "DOTNET_SDK",
        "ANDROID_API",
        "ANDROID_BUILD_TOOLS",
        "RUST",
        "PG_MAJOR",
    ):
        assert key in keys, key
        assert f"want {key}" in text or 'want "$key"' in text, key
    assert subprocess.run(["sh", "-n", str(CHECK)], check=False).returncode == 0


def test_self_check_fails_closed_on_a_missing_tool(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.write_text("GH=0.0.0-not-installed\n")
    done = subprocess.run(
        ["/bin/sh", str(CHECK)],
        env={"PATH": "/usr/bin:/bin", "OMEGA_CI_TOOLCHAIN_VERSIONS": str(versions)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode != 0
    assert "toolchain-check: FAIL" in done.stderr  # names the tool, exits non-zero
