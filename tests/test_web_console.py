from pathlib import Path

from web_console import build_script_command, normalize_workers


def test_build_script_command_adds_workers_for_s2():
    cmd = build_script_command("s2", funding_workers=7)
    assert Path(cmd[-3]).name == "s2_okx_oi_funding_rate_scanner.py"
    assert cmd[-2:] == ["--funding-workers", "7"]


def test_build_script_command_uses_plain_script_for_s3():
    cmd = build_script_command("s3", funding_workers=7)
    assert Path(cmd[-1]).name == "s3_okx_accumulation_radar.py"
    assert "--funding-workers" not in cmd


def test_normalize_workers_bounds_values():
    assert normalize_workers("0") == 1
    assert normalize_workers("99") == 50
    assert normalize_workers("abc") == 20

from web_console import pick_server_port


def test_pick_server_port_skips_busy_port(monkeypatch):
    def fake_available(_host, port):
        return port != 8787

    monkeypatch.setattr("web_console.port_is_available", fake_available)
    assert pick_server_port("127.0.0.1", 8787, attempts=2) == 8788
