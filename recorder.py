import logging
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from utils import convert_ts, sanitize

EXEC_CONV = ProcessPoolExecutor(max_workers=4)
logger = logging.getLogger(__name__)


@dataclass
class HLSRecoveryConfig:
    backend: str = "streamlink"
    fallback_backend: Optional[str] = "yt-dlp"
    segment_retry_limit: int = 5
    segment_retry_backoff_sec: float = 1.5
    consecutive_403_limit: int = 3
    reopen_limit: int = 4
    max_total_restarts: int = 8
    streamlink_retry_streams: int = 5
    streamlink_retry_open: int = 5
    yt_dlp_fragment_retries: int = 20
    yt_dlp_retries: int = 20
    yt_dlp_live_from_start: bool = False
    flush_every_chunks: int = 4


class ManagedRecordingProcess:
    """Supervisiona a captura HLS e reabre sessão em caso de expiração/403."""

    SEGMENT_RE = re.compile(r"(?P<url>https?://\S+)")

    def __init__(
        self,
        *,
        url: str,
        quality: str,
        output_file: Path,
        config: HLSRecoveryConfig,
        mode: str,
        on_output_change: Callable[[Path], None],
        log_path: Path,
    ):
        self.url = url
        self.quality = quality
        self.base_output_file = output_file
        self.config = config
        self.mode = mode
        self.on_output_change = on_output_change
        self.log_path = log_path

        self.returncode: Optional[int] = None
        self._active_proc: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()
        self._done_event = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run_supervisor, daemon=True)

        self.segments_downloaded = 0
        self.consecutive_403 = 0
        self.restart_count = 0
        self.error_code: Optional[str] = None
        self.backend_in_use = self.config.backend

    def start(self):
        self._thread.start()

    def poll(self):
        if self._done_event.is_set():
            return self.returncode
        return None

    def wait(self, timeout: Optional[float] = None):
        self._done_event.wait(timeout)
        if not self._done_event.is_set():
            raise subprocess.TimeoutExpired("managed-recording", timeout)
        return self.returncode

    def terminate(self):
        self._stop_event.set()
        with self._lock:
            if self._active_proc and self._active_proc.poll() is None:
                self._active_proc.terminate()

    def kill(self):
        self._stop_event.set()
        with self._lock:
            if self._active_proc and self._active_proc.poll() is None:
                self._active_proc.kill()

    def _build_output_for_attempt(self, attempt: int, backend: str) -> Path:
        suffix = self.base_output_file.suffix or ".ts"
        if attempt == 0 and backend == self.config.backend:
            return self.base_output_file
        stem = self.base_output_file.with_suffix("")
        return Path(f"{stem}.part{attempt + 1:03d}{suffix}")

    def _build_command(self, backend: str, output_file: Path):
        if backend == "streamlink":
            cmd = [
                "streamlink",
                self.url,
                self.quality,
                "-o",
                str(output_file),
                "--retry-streams",
                str(self.config.streamlink_retry_streams),
                "--retry-open",
                str(self.config.streamlink_retry_open),
            ]
            return cmd

        if backend == "yt-dlp":
            cmd = [
                "yt-dlp",
                "-o",
                str(output_file),
                "--hls-use-mpegts",
                "--fragment-retries",
                str(self.config.yt_dlp_fragment_retries),
                "--retries",
                str(self.config.yt_dlp_retries),
                "-f",
                self.quality if self.quality != "best" else "best",
            ]
            if self.config.yt_dlp_live_from_start:
                cmd.append("--live-from-start")
            cmd.append(self.url)
            return cmd

        raise RuntimeError(f"Backend não suportado: {backend}")

    def _short_url(self, line: str) -> str:
        m = self.SEGMENT_RE.search(line)
        if not m:
            return ""
        url = m.group("url")
        return (url[:100] + "…") if len(url) > 100 else url

    def _is_segment_403(self, line: str) -> bool:
        low = line.lower()
        return "403" in low and ("segment" in low or "fragment" in low or ".ts" in low or "forbidden" in low)

    def _run_supervisor(self):
        reopen_failures = 0
        backends = [self.config.backend]
        if self.config.fallback_backend and self.config.fallback_backend not in backends:
            backends.append(self.config.fallback_backend)

        attempt = 0
        while not self._stop_event.is_set() and self.restart_count < self.config.max_total_restarts:
            backend = backends[min(reopen_failures, len(backends) - 1)]
            self.backend_in_use = backend
            output_file = self._build_output_for_attempt(attempt, backend)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            self.on_output_change(output_file)

            cmd = self._build_command(backend, output_file)
            with self.log_path.open("a", encoding="utf-8") as logf:
                logf.write(
                    f"{datetime.now().isoformat()} event=LIVE_SESSION_START mode={self.mode} backend={backend} quality={self.quality} output={output_file}\n"
                )
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                with self._lock:
                    self._active_proc = proc

                for line in proc.stdout or []:
                    logf.write(line)
                    logf.flush()
                    line = line.strip()
                    if not line:
                        continue

                    if self._is_segment_403(line):
                        self.consecutive_403 += 1
                        self.error_code = "HLS_SEGMENT_403"
                        short = self._short_url(line)
                        logger.warning(
                            "event=HLS_SEGMENT_403 backend=%s url=%s quality=%s output=%s consecutive_403=%s restarts=%s segment_url=%s",
                            backend,
                            self.url,
                            self.quality,
                            output_file,
                            self.consecutive_403,
                            self.restart_count,
                            short,
                        )
                        if self.consecutive_403 >= self.config.consecutive_403_limit:
                            proc.terminate()
                            break
                    elif "downloaded" in line.lower() or "wrote" in line.lower():
                        self.segments_downloaded += 1
                        self.consecutive_403 = 0

                rc = proc.wait()
                with self._lock:
                    self._active_proc = None

                if self._stop_event.is_set():
                    self.returncode = rc if rc is not None else 0
                    self._done_event.set()
                    return

                if rc == 0 and self.consecutive_403 == 0:
                    self.returncode = 0
                    self._done_event.set()
                    return

                self.restart_count += 1
                attempt += 1
                reopen_failures += 1
                self.error_code = "HLS_SESSION_EXPIRED" if self.consecutive_403 else "LIVE_REOPEN_FAILED"
                logger.warning(
                    "event=LIVE_REOPEN backend=%s url=%s quality=%s output=%s restarts=%s consecutive_403=%s reason=%s",
                    backend,
                    self.url,
                    self.quality,
                    output_file,
                    self.restart_count,
                    self.consecutive_403,
                    self.error_code,
                )
                if reopen_failures > self.config.reopen_limit:
                    break
                time.sleep(min(self.config.segment_retry_backoff_sec * max(1, reopen_failures), 10))

        self.returncode = 75
        self.error_code = self.error_code or "LIVE_REOPEN_FAILED"
        logger.error(
            "event=LIVE_TERMINATED mode=%s backend=%s url=%s quality=%s output=%s segments=%s restarts=%s consecutive_403=%s error_code=%s",
            self.mode,
            self.backend_in_use,
            self.url,
            self.quality,
            self.base_output_file,
            self.segments_downloaded,
            self.restart_count,
            self.consecutive_403,
            self.error_code,
        )
        self._done_event.set()


class Recorder:
    def __init__(self, hls_config: Optional[HLSRecoveryConfig] = None):
        self.proc = {}
        self.start = {}
        self.ts = {}
        self.aproc = {}
        self.astart = {}
        self.ats = {}
        self.hls_config = hls_config or HLSRecoveryConfig()

    def _tail_log(self, log_path: Path, max_lines: int = 10) -> str:
        if not log_path.exists():
            return ""
        lines = deque(maxlen=max_lines)
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                lines.append(line.rstrip())
        return "\n".join(lines)

    def _ensure_streamlink_available(self):
        if shutil.which("streamlink") is None:
            raise RuntimeError(
                "streamlink não foi encontrado no PATH. Instale o streamlink e reinicie o programa."
            )

    def _start_streamlink_with_fallback(self, url: str, qual: str, ts: Path):
        """Compatibilidade: valida inicialização do streamlink e fallback para best."""
        self._ensure_streamlink_available()
        qualities = [qual]
        if qual != "best":
            qualities.append("best")

        last_return_code = None
        last_quality = qual
        log_path = ts.with_suffix(".streamlink.log")
        for q in qualities:
            log_file = log_path.open("a", encoding="utf-8")
            p = subprocess.Popen(
                [
                    "streamlink",
                    url,
                    q,
                    "-o",
                    str(ts),
                    "--retry-streams",
                    str(self.hls_config.streamlink_retry_streams),
                    "--retry-open",
                    str(self.hls_config.streamlink_retry_open),
                ],
                stdout=log_file,
                stderr=log_file,
            )

            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                log_file.close()
                return p, q

            log_file.close()

            if p.poll() is None:
                return p, q

            last_return_code = p.returncode
            last_quality = q

        details = self._tail_log(log_path)
        if details:
            details = f"\nDetalhes streamlink (últimas linhas):\n{details}"
        raise RuntimeError(
            f"streamlink encerrou com código {last_return_code} ao iniciar (qualidade: {last_quality}){details}"
        )

    def _normalize_live_url(self, url: str) -> str:
        if "youtube" in url.lower() and "/live" not in url.lower() and "watch?v=" not in url.lower():
            return url.rstrip("/") + "/live"
        return url

    def _start_managed_capture(self, key: str, url: str, qual: str, ts: Path, mode: str, target_dict: dict):
        url = self._normalize_live_url(url)
        log_path = ts.with_suffix(f".{mode}.hls.log")

        # Mantém fallback de qualidade para "best" se a qualidade específica falhar cedo.
        quality = qual
        if qual != "best":
            try:
                probe_proc, _ = self._start_streamlink_with_fallback(url, qual, ts.with_suffix(".probe.ts"))
                probe_proc.terminate()
                probe_proc.wait(timeout=5)
                ts.with_suffix(".probe.ts").unlink(missing_ok=True)
            except Exception:
                quality = "best"

        proc = ManagedRecordingProcess(
            url=url,
            quality=quality,
            output_file=ts,
            config=self.hls_config,
            mode=mode,
            on_output_change=lambda new_ts: target_dict.__setitem__(key, new_ts),
            log_path=log_path,
        )
        proc.start()
        return proc

    # Manual recording
    def start_manual(self, key: str, label: str, url: str, qual: str, output_dir: Path) -> Path:
        subdir = output_dir / sanitize(label)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        p = self._start_managed_capture(key, url, qual, ts, "manual", self.ts)

        self.proc[key] = p
        self.start[key] = time.time()
        self.ts[key] = ts
        return ts

    def stop_manual(self, key: str, callback: Callable):
        proc = self.proc.get(key)
        ts_file = self.ts.get(key)
        if not proc or not ts_file:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        fut = EXEC_CONV.submit(convert_ts, ts_file)
        fut.add_done_callback(lambda f, k=key: callback(f, k))

    def finish_manual(self, key: str):
        for d in (self.proc, self.start, self.ts):
            d.pop(key, None)

    # Automatic recording
    def start_auto(self, key: str, name: str, url: str, qual: str, output_dir: Path) -> Path:
        subdir = output_dir / sanitize(name)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        p = self._start_managed_capture(key, url, qual, ts, "auto", self.ats)

        self.aproc[key] = p
        self.astart[key] = time.time()
        self.ats[key] = ts
        return ts

    def stop_auto(self, key: str, callback: Callable):
        proc = self.aproc.get(key)
        ts_file = self.ats.get(key)
        if not proc or not ts_file:
            return
        if proc.poll() is None:
            proc.terminate()
            proc.wait(5)
        fut = EXEC_CONV.submit(convert_ts, ts_file)
        fut.add_done_callback(lambda f, k=key: callback(f, k))

    def finish_auto(self, key: str):
        for d in (self.aproc, self.astart, self.ats):
            d.pop(key, None)
