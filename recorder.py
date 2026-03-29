import subprocess
import time
from datetime import datetime
from pathlib import Path
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

    # Manual recording
    def start_manual(self, key: str, label: str, url: str, qual: str, output_dir: Path) -> Path:
        # Ajusta URLs de canal do YouTube para apontarem diretamente para o video ao vivo
        if "youtube" in url.lower() and "/live" not in url.lower() and "watch?v=" not in url.lower():
            url = url.rstrip("/") + "/live"
        subdir = output_dir / sanitize(label)
        subdir.mkdir(parents=True, exist_ok=True)
        ts = subdir / f"{datetime.now():%d%m%y_%H%M}.ts"
        p = subprocess.Popen(
            ["streamlink", url, qual, "-o", str(ts), "--retry-streams", "5"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # aguarda brevemente para checar falha imediata do streamlink
        try:
            p.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

        if p.poll() is not None and p.returncode != 0:
            raise RuntimeError(
                f"streamlink encerrou com código {p.returncode} ao iniciar"
            )

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
        p = subprocess.Popen(
            ["streamlink", url, qual, "-o", str(ts), "--retry-streams", "5"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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
