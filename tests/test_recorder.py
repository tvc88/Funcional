from pathlib import Path

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

