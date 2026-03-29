import os
import stat
import textwrap
import time

import pytest

from recorder import Recorder


class _Proc:
    def __init__(self, returncode=None, timeout=False):
        self.returncode = returncode
        self._timeout = timeout

    def wait(self, timeout=None):
        if self._timeout:
            raise TimeoutError()
        return self.returncode

    def poll(self):
        return None if self._timeout else self.returncode


def _patch_timeout_expired(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "TimeoutExpired", TimeoutError)


def test_start_streamlink_fallback_para_best(monkeypatch, tmp_path):
    rec = Recorder()
    _patch_timeout_expired(monkeypatch)
    calls = []

    def fake_popen(args, stdout=None, stderr=None):
        calls.append(args)
        # Primeira tentativa (1080p) falha imediata; segunda (best) continua rodando.
        if args[2] == "1080p":
            return _Proc(returncode=1, timeout=False)
        return _Proc(returncode=None, timeout=True)

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    proc, used_qual = rec._start_streamlink_with_fallback("https://x", "1080p", tmp_path / "a.ts")

    assert proc is not None
    assert used_qual == "best"
    assert len(calls) == 2
    assert calls[0][2] == "1080p"
    assert calls[1][2] == "best"


def test_start_streamlink_sem_fallback_quando_best(monkeypatch, tmp_path):
    rec = Recorder()
    _patch_timeout_expired(monkeypatch)
    calls = []

    def fake_popen(args, stdout=None, stderr=None):
        calls.append(args)
        return _Proc(returncode=None, timeout=True)

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _, used_qual = rec._start_streamlink_with_fallback("https://x", "best", tmp_path / "a.ts")
    assert used_qual == "best"
    assert len(calls) == 1


def test_start_streamlink_erro_quando_todas_qualidades_falham(monkeypatch, tmp_path):
    rec = Recorder()
    _patch_timeout_expired(monkeypatch)

    def fake_popen(args, stdout=None, stderr=None):
        return _Proc(returncode=1, timeout=False)

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(RuntimeError):
        rec._start_streamlink_with_fallback("https://x", "720p", tmp_path / "a.ts")


def test_start_streamlink_saida_imediata_codigo_zero_tambem_falha(monkeypatch, tmp_path):
    rec = Recorder()
    _patch_timeout_expired(monkeypatch)
    calls = []

    def fake_popen(args, stdout=None, stderr=None):
        calls.append(args[2])
        return _Proc(returncode=0, timeout=False)

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(RuntimeError):
        rec._start_streamlink_with_fallback("https://x", "720p", tmp_path / "a.ts")
    assert calls == ["720p", "best"]


def test_start_manual_grava_ts_com_streamlink_fake(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_streamlink = fake_bin / "streamlink"
    fake_streamlink.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import pathlib, sys, time
            out = None
            for i, arg in enumerate(sys.argv):
                if arg == "-o" and i + 1 < len(sys.argv):
                    out = pathlib.Path(sys.argv[i + 1])
                    break
            if out is None:
                sys.exit(2)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"TS-DATA")
            time.sleep(2.5)
            """
        ),
        encoding="utf-8",
    )
    fake_streamlink.chmod(fake_streamlink.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")

    rec = Recorder()
    ts_file = rec.start_manual("k1", "Teste", "https://canal.exemplo/live", "best", tmp_path)
    time.sleep(0.2)
    assert ts_file.exists()
    assert ts_file.stat().st_size > 0

    proc = rec.proc["k1"]
    proc.terminate()
    proc.wait(timeout=5)


def test_erro_quando_streamlink_nao_existe(monkeypatch, tmp_path):
    rec = Recorder()
    monkeypatch.setenv("PATH", "")
    with pytest.raises(RuntimeError, match="streamlink não foi encontrado"):
        rec._start_streamlink_with_fallback("https://x", "best", tmp_path / "a.ts")
