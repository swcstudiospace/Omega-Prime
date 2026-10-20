import json

import pytest

from omega_prime.dashboard.__main__ import main


def test_next_id_is_non_consuming_and_assign_id_reuses(tmp_path, capsys):
    path = tmp_path / ".omega-prime" / "dashboard" / "tasks.json"
    assert main(["next-id"], home=tmp_path) == 0
    assert capsys.readouterr().out == "00001\n"
    assert not path.exists()
    assert main(["next-id"], home=tmp_path) == 0
    assert capsys.readouterr().out == "00001\n"
    assert not path.exists()
    assert main(["assign-id", "--id", "tasks:example", "--title", "Example"], home=tmp_path) == 0
    assert capsys.readouterr().out == "00001 - Example\n"
    before = path.read_bytes()
    assert main(["assign-id", "--id", "tasks:example", "--title", "Example"], home=tmp_path) == 0
    assert capsys.readouterr().out == "00001 - Example\n"
    assert json.loads(path.read_text())["identity_registry"]
    assert main(["next-id"], home=tmp_path) == 0
    assert capsys.readouterr().out == "00002\n"


def test_next_id_does_not_rewrite_existing_document(tmp_path, capsys):
    path = tmp_path / ".omega-prime" / "dashboard" / "tasks.json"
    main(["assign-id", "--id", "tasks:a", "--title", "A"], home=tmp_path)
    capsys.readouterr()
    raw = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    assert main(["next-id"], home=tmp_path) == 0
    assert capsys.readouterr().out == "00002\n"
    assert path.read_bytes() == raw
    assert path.stat().st_mtime_ns == stamp


def test_corrupt_bridge_does_not_get_reset_by_cli(tmp_path, capsys):
    path = tmp_path / ".omega-prime" / "dashboard" / "tasks.json"
    path.parent.mkdir(parents=True)
    raw = b"{broken"
    path.write_bytes(raw)
    with pytest.raises((ValueError, OSError)):
        main(["next-id"], home=tmp_path)
    assert path.read_bytes() == raw
