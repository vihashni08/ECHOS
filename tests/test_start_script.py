"""scripts/start.sh: argument handling, port guard, safe defaults (static), and one real --check start on spare ports (needs the UI dev database and web/node_modules)."""
import os
import re
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "scripts" / "start.sh"


def run(*args, env=None, timeout=60):
    return subprocess.run([str(SH), *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_unknown_option_and_help():
    r = run("--nope")
    assert r.returncode == 2 and "unknown option" in r.stdout
    h = run("--help")
    assert h.returncode == 0 and "--keep" in h.stdout and "NEVER printed" in h.stdout


def test_refuses_a_port_that_is_in_use():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        r = run("--check", env={"ECHOS_API_PORT": str(port), "ECHOS_WEB_PORT": str(free_port())})
    assert r.returncode == 1 and f"port {port} is already in use" in r.stdout


def test_safe_defaults_in_the_script_text():
    t = SH.read_text()
    code = "\n".join(ln for ln in t.splitlines() if not ln.lstrip().startswith("#"))
    assert "--dry" in t and "ECHOS_DRY_RUN=1" in t                              # mail is a dry run by default
    assert "--real sends REAL e-mails: add --yes" in t                          # real mail needs an explicit --yes
    assert not re.search(r"send_real|(cat|source)\s+\S*\.env", code)            # never edits mail settings or prints .env
    assert "ECHOS_TOKEN" not in code                                              # no token variable is ever expanded here
    assert not re.search(r"(cat|head|tail|less|grep|sed|awk)[^\n]*auth_tokens", code)


@pytest.mark.skipif(not (ROOT / "models" / "v2" / "meta.json").exists() or not (ROOT / "web" / "node_modules").exists() or not (ROOT / ".venv").exists(),
                    reason="needs models/v2, web/node_modules and .venv")
def test_check_start_prints_only_urls_and_stops_everything(monkeypatch):
    api, web = free_port(), free_port()
    demo = ROOT / "data" / "echos_demo.db"                                       # a demo may be running: --check must not touch its database
    before = demo.stat().st_ino if demo.exists() else None
    r = run("--check", "--auth", env={"ECHOS_API_PORT": str(api), "ECHOS_WEB_PORT": str(web)}, timeout=200)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"http://localhost:{web}" in r.stdout and f"http://127.0.0.1:{api}" in r.stdout
    assert "dry-run mail" in r.stdout
    assert (demo.stat().st_ino if demo.exists() else None) == before
    tok = (ROOT / "data" / "auth_tokens.txt").read_text().split()[1::2]
    assert tok and not any(t in r.stdout + r.stderr for t in tok)                # tokens are never printed
    for p in (api, web):                                                         # everything was stopped again
        with socket.socket() as s:
            s.settimeout(1)
            assert s.connect_ex(("127.0.0.1", p)) != 0
