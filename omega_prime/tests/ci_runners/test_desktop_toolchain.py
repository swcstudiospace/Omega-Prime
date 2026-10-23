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
        "PY313",
        "PY314",
        "CODEQL",
        "NODE22",
        "NODE24",
        "DOTNET_SDK",
        "ANDROID_API",
        "ANDROID_BUILD_TOOLS",
        "ANDROID_PLATFORM_REV",
        "ANDROID_PLATFORM_TOOLS",
        "ILLINK_TASKS",
        "RUST",
        "RUST_PARITY",
        "CARGO_DENY",
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


# --- 00058: Greptile follow-ups on PR #92 ------------------------------------


def android_layer() -> str:
    start = DOCKERFILE.index("# (e) Android SDK")
    return DOCKERFILE[start : DOCKERFILE.index("# (f) Rust:")]


def test_android_packages_are_fixed_pinned_archives() -> None:
    a = args()
    layer = android_layer()
    # Every Android package is a named archive with its own sha256 pin...
    for name in (
        "ANDROID_CMDLINE_TOOLS_SHA256",
        "ANDROID_PLATFORM_SHA256",
        "ANDROID_BUILD_TOOLS_SHA256",
        "ANDROID_PLATFORM_TOOLS_SHA256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", a[name]), name
        assert "${" + name + "}" in layer, name
    assert (
        a["ANDROID_PLATFORM_ARCHIVE"]
        == f"platform-{a['ANDROID_API']}_r0{a['ANDROID_PLATFORM_REV']}.zip"
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", a["ANDROID_PLATFORM_TOOLS_VERSION"])
    assert "platform-tools_r${ANDROID_PLATFORM_TOOLS_VERSION}-linux.zip" in layer
    assert layer.count("curl -fsSL -o") == 4
    assert "sha256sum -c -" in layer
    # ...and sdkmanager resolves nothing: it only records licence acceptance.
    for line in layer.splitlines():
        if "sdkmanager" in line and not line.lstrip().startswith("#"):
            assert "--licenses" in line, line
    assert "platform-tools >" not in layer and '"platforms;' not in layer
    # The unpacked revisions are asserted from each archive's source.properties.
    for pin in (
        "ANDROID_PLATFORM_REV",
        "ANDROID_BUILD_TOOLS",
        "ANDROID_PLATFORM_TOOLS_VERSION",
    ):
        assert f'grep -qx "Pkg.Revision=${{{pin}}}"' in layer, pin


def test_self_check_asserts_exact_android_revisions() -> None:
    text = CHECK.read_text()
    assert "want ANDROID_PLATFORM_REV" in text
    assert 'pkgrev "$ANDROID_HOME/platform-tools"' in text
    assert 'check "adb" "Version $(want ANDROID_PLATFORM_TOOLS)" adb version' in text


def test_illink_tasks_ships_in_the_sdk_offline_source() -> None:
    a = args()
    assert re.fullmatch(r"[0-9a-f]{128}", a["ILLINK_TASKS_SHA512"])
    # Same version as the .NET runtime the pinned SDK bundles (checked at build).
    assert (
        '[ -d "${DOTNET_ROOT}/shared/Microsoft.NETCore.App/${ILLINK_TASKS_VERSION}" ]'
        in DOCKERFILE
    )
    assert '"${DOTNET_ROOT}/library-packs/"' in DOCKERFILE
    # Its own layer, after (d), so the 4 GB .NET layer stays cached.
    assert (
        DOCKERFILE.index("# (d) .NET SDK")
        < DOCKERFILE.index("# (d2)")
        < DOCKERFILE.index("# (e) Android")
    )


def test_self_check_builds_net10_android_offline_as_the_job() -> None:
    text = CHECK.read_text()
    build = text[text.index("if section android-build") :]
    build = build[: build.index("\nfi\n")]
    assert "dotnet new android" in build
    assert "<clear />" in build  # nuget.org removed: library-packs only
    assert "dotnet build" in build and "-nodeReuse:false" in build
    assert '-o "$app/out"' in build and "-Signed.apk" in build
    # Everything the build writes stays under the run's scratch dir.
    assert 'NUGET_PACKAGES="$scratch/nuget"' in build
    assert 'DOTNET_CLI_HOME="$scratch/dotnet-home"' in build


def test_self_check_cleans_up_databases_before_files() -> None:
    text = CHECK.read_text()
    cleanup = text[text.index("cleanup() {") :]
    cleanup = cleanup[: cleanup.index("\n}\n")]
    assert cleanup.index('"$db_helper" stop --all') < cleanup.index('rm -rf "$scratch"')
    assert "trap cleanup EXIT" in text
    for sig, code in (("HUP", 129), ("INT", 130), ("TERM", 143)):
        assert f"trap 'exit {code}' {sig}" in text
    # Databases only ever start through the helper (free ports, scratch data).
    assert "pg_ctl -D" not in text and "greptime standalone start" not in text
    assert "4000" not in text and "54321" not in text


def test_db_helper_ships_on_the_job_path() -> None:
    assert (
        "COPY deploy/ci-runner-desktop/omega-ci-db /usr/local/bin/omega-ci-db"
        in DOCKERFILE
    )
    assert "chmod 0755 /usr/local/bin/omega-ci-db" in DOCKERFILE
    assert "/usr/local/bin" in runner_env()["PATH"].split(":")
    assert (
        subprocess.run(
            ["sh", "-n", str(DESKTOP / "omega-ci-db")], check=False
        ).returncode
        == 0
    )


def test_docs_examples_use_the_helper_not_fixed_ports() -> None:
    docs = (REPO / "docs" / "ci-runners.md").read_text()
    assert "omega-ci-db start postgres" in docs
    assert "omega-ci-db start greptime" in docs
    assert "omega-ci-db stop --all" in docs
    # The old examples passed fixed ports (postgres -p 54321, greptime
    # 127.0.0.1 port 4000). "[:]" keeps test_store_lock's endpoint scan quiet.
    assert not re.search(r"-p 54321\b|127\.0\.0\.1[:]4000\b", docs)
