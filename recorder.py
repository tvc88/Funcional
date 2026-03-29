import subprocess
import time
import shutil
from datetime import datetime
from pathlib import Path
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from typing import Callable

from utils import sanitize, convert_ts

EXEC_CONV = ProcessPoolExecutor(max_workers=4)


class Recorder:
    def __init__(self):
        self.proc = {}
        self.start = {}
        self.ts = {}
        self.aproc = {}
        self.astart = {}
        self.ats = {}

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
        """Inicia o streamlink e tenta fallback para 'best' quando necessário."""
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
                ["streamlink", url, q, "-o", str(ts), "--retry-streams", "5"],
                stdout=log_file,
                stderr=log_file,
            )

            # aguarda brevemente para checar falha imediata do streamlink
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

    # Manual recording
    def start_manual(self, key: str, label: str, url: str, qual: str, output_dir: Path) -> Path:
        # Ajusta URLs de canal do YouTube para apontarem diretamente para o video ao vivo
        if "youtube" in url.lower() and "/live" not in url.lower() and "watch?v=" not in url.lower():
            url = url.rstrip("/") + "/live"
        subdir = output_dir / sanitize(label)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        p, _used_quality = self._start_streamlink_with_fallback(url, qual, ts)

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
        # Mesma correção para URLs de canais do YouTube monitorados
        if "youtube" in url.lower() and "/live" not in url.lower() and "watch?v=" not in url.lower():
            url = url.rstrip("/") + "/live"
        subdir = output_dir / sanitize(name)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        p, _used_quality = self._start_streamlink_with_fallback(url, qual, ts)

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
