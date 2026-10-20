"""Phase 43: read-only surface probes stay read-only and secret-free."""

from __future__ import annotations

from pathlib import Path

from omega_prime.providers.base import ProviderError
from omega_prime.substrate import probes
from omega_prime.substrate.probes import (
    config_from_env,
    format_report,
    main,
    run_probes,
)

SUB_TOKEN = "sub-secret-1"
HS_TOKEN = "hs-secret-2"
ROOT = Path(__file__).resolve().parents[2]


class _FakeTransport:
    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[dict] = []

    def _next(self, method: str, url: str, headers: dict, body: dict):
        self.requests.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def post(self, url: str, headers: dict, body: dict):
        return self._next("POST", url, headers, body)

    def post_text(self, url: str, headers: dict, body: dict):
        return self._next("POST", url, headers, body)

    def get(self, url: str, headers: dict, params: dict):
        return self._next("GET", url, headers, params)


def _config(**overrides) -> dict:
    config = config_from_env(
        {
            "SUBSTRATE_URL": "https://sub.local",
            "SUBSTRATE_TOKEN": SUB_TOKEN,
            "HINDSIGHT_URL": "https://hs.local",
            "HINDSIGHT_API_KEY": HS_TOKEN,
        }
    )
    config.update(overrides)
    return config


def test_all_probes_pass_with_shape() -> None:
    transport = _FakeTransport(
        [
            {"index": True, "greptime": True, "eventsWritable": True},
            "# brief\n- one\n",
            {"status": "ok"},
            {"api_version": "0.9.1"},
        ]
    )
    report = run_probes(_config(), transport)
    assert report["passed"] is True
    assert report["probes"]["substrate_health"] == {
        "ok": True,
        "detail": "index=True, greptime=True, eventsWritable=True",
    }
    assert report["probes"]["substrate_brief"]["detail"].startswith("brief received")
    assert report["probes"]["hindsight_version"] == {
        "ok": True,
        "detail": "api_version=0.9.1",
    }


def test_every_probe_attempted_despite_failure() -> None:
    transport = _FakeTransport(
        [ProviderError("down"), "", {"status": "ok"}, {"api_version": "0.9.1"}]
    )
    report = run_probes(_config(), transport)
    assert report["passed"] is False
    assert report["probes"]["substrate_health"]["ok"] is False
    assert "down" in report["probes"]["substrate_health"]["detail"]
    assert report["probes"]["substrate_brief"]["detail"].startswith("empty brief")
    assert len(transport.requests) == 4


def test_probes_are_read_only() -> None:
    transport = _FakeTransport([{}, "", {}, {}])
    run_probes(_config(), transport)
    calls = [(request["method"], request["url"]) for request in transport.requests]
    assert calls == [
        ("GET", "https://sub.local/healthz"),
        ("POST", "https://sub.local/brief"),
        ("GET", "https://hs.local/health"),
        ("GET", "https://hs.local/version"),
    ]
    assert transport.requests[1]["body"] == {"surface": "grok-bot"}
    assert transport.requests[0]["headers"] == {"Authorization": f"Bearer {SUB_TOKEN}"}
    assert transport.requests[2]["headers"] == {"Authorization": f"Bearer {HS_TOKEN}"}
    for request in transport.requests:
        assert "/events" not in request["url"]
        assert "/mcp" not in request["url"]
        assert "/memories" not in request["url"]
        assert "/reflect" not in request["url"]


def test_secrets_never_in_report_or_output(capsys) -> None:
    transport = _FakeTransport(
        [
            ProviderError(f"bad {SUB_TOKEN}"),
            f"leaked {HS_TOKEN} brief",
            {"status": "ok"},
            {"api_version": "0.9.1"},
        ]
    )
    config = _config()
    report = run_probes(config, transport)
    text = format_report(report, config)
    assert SUB_TOKEN not in text
    assert HS_TOKEN not in text
    assert "SUBSTRATE_TOKEN" in text and "HINDSIGHT_API_KEY" in text
    env = {
        "SUBSTRATE_URL": "https://sub.local",
        "SUBSTRATE_TOKEN": SUB_TOKEN,
        "HINDSIGHT_URL": "https://hs.local",
        "HINDSIGHT_API_KEY": HS_TOKEN,
    }
    assert main([], env, _FakeTransport([{}, "", {}, {}])) == 0
    out = capsys.readouterr().out
    assert SUB_TOKEN not in out and HS_TOKEN not in out


def test_main_exit_code_on_failure() -> None:
    transport = _FakeTransport([ProviderError("down"), "", {}, {}])
    assert main([], {}, transport) == 1


def test_unset_hindsight_url_skips_a_broken_credential_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing-key"
    config = config_from_env(
        {"SUBSTRATE_URL": "local", "HINDSIGHT_API_KEY_FILE": str(missing)}
    )
    assert config["hindsight_url"] == ""
    assert config["hindsight_token"] == ""
    transport = _FakeTransport([{}, ""])
    report = run_probes(config, transport)
    assert report["passed"] is True
    assert report["probes"]["hindsight_health"]["detail"] == (
        "disabled (HINDSIGHT_URL unset)"
    )


def test_unset_hindsight_url_does_not_call_remote_bank_and_unset_substrate() -> None:
    config = config_from_env({})
    assert config["hindsight_url"] == ""
    transport = _FakeTransport([])
    report = run_probes(config, transport)
    assert report["passed"] is False
    assert report["probes"]["hindsight_health"]["detail"] == (
        "disabled (HINDSIGHT_URL unset)"
    )
    assert report["probes"]["substrate_health"]["detail"] == "not_configured"
    assert transport.requests == []
    watched = (
        ROOT.joinpath("omega_prime/memory/hindsight_service.py"),
        ROOT.joinpath("omega_prime/substrate/probes.py"),
        ROOT.joinpath("docs/substrate.md"),
        ROOT.joinpath("omega_prime/grokbot/SETUP.md"),
    )
    for path in watched:
        text = path.read_text(encoding="utf-8")
        assert "hindsight-api-production-014d" not in text, path
        assert "up.railway.app" not in text, path


def test_config_defaults_and_precedence() -> None:
    config = config_from_env({})
    assert config["substrate_url"] is None
    assert config["substrate_error"] == "not_configured"
    assert config["hindsight_url"] == probes.DEFAULT_HINDSIGHT_URL
    assert config["substrate_token"] == ""
    fallback = config_from_env(
        {"SUBSTRATE_TOKEN_GROK_BOT": "g", "HINDSIGHT_API_TOKEN": "t"}
    )
    assert fallback["substrate_token"] == "g"
    assert fallback["substrate_token_name"] == "SUBSTRATE_TOKEN_GROK_BOT"
    assert fallback["hindsight_url"] == ""
    assert fallback["hindsight_token"] == ""
    generic_wins = config_from_env(
        {"SUBSTRATE_TOKEN": "generic", "SUBSTRATE_TOKEN_GROK_BOT": "fallback"}
    )
    assert generic_wins["substrate_token"] == "generic"
    assert generic_wins["substrate_token_name"] == "SUBSTRATE_TOKEN"
    report = run_probes(config, _FakeTransport([]))
    assert not report["passed"]
    assert report["probes"]["substrate_health"]["detail"] == "not_configured"
    assert _FakeTransport([]).requests == []


def test_substrate_token_file_uses_existing_secret_resolution(tmp_path: Path) -> None:
    secret_file = tmp_path / "substrate-token"
    secret_file.write_text("file-token\n", encoding="utf-8")
    secret_file.chmod(0o600)
    config = config_from_env(
        {"SUBSTRATE_URL": "https://sub.local", "SUBSTRATE_TOKEN_FILE": str(secret_file)}
    )
    assert config["substrate_token"] == "file-token"
    assert config["substrate_token_name"] == "SUBSTRATE_TOKEN"


def test_substrate_urls_are_validated_without_requests() -> None:
    for raw in ("http://sub.local", "https://user:secret@sub.local", "not a url"):
        config = config_from_env({"SUBSTRATE_URL": raw})
        transport = _FakeTransport([])
        report = run_probes(config, transport)
        assert report["probes"]["substrate_health"] == {
            "ok": False,
            "detail": "unsafe substrate URL",
        }
        assert raw not in format_report(report, config)
        assert transport.requests == []


def test_explicit_local_probe_opt_in_uses_loopback_fake_transport() -> None:
    config = config_from_env({"SUBSTRATE_URL": "local"})
    assert config["substrate_url"] == "http://127.0.0.1:7410"
    transport = _FakeTransport([{}, ""])
    run_probes(config, transport)
    assert [request["url"] for request in transport.requests] == [
        "http://127.0.0.1:7410/healthz",
        "http://127.0.0.1:7410/brief",
    ]
