import re
import json
import subprocess
import logging
from pathlib import Path
from datetime import datetime

INVALID_FS_CHARS = r'[<>:"/\\|?*\x00-\x1F]'

logger = logging.getLogger(__name__)


def sanitize(text: str) -> str:
    text = re.sub(INVALID_FS_CHARS, "_", text)
    return re.sub(r"\s+", "_", text).strip("_")


def human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1_048_576:
        return f"{num_bytes/1024:.1f} KB"
    if num_bytes < 1_073_741_824:
        return f"{num_bytes/1_048_576:.1f} MB"
    return f"{num_bytes/1_073_741_824:.2f} GB"


def human_time(sec: int) -> str:
    if sec < 60:
        return f"{sec}s"
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    minutes %= 60
    return f"{hours} h {minutes} min"


def convert_ts(ts_path: Path):
    mp4 = ts_path.with_suffix(".mp4")
    ok = False
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(ts_path), "-c", "copy", str(mp4)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3600,
        )
        ok = r.returncode == 0
        logger.info("ffmpeg retornou %s para %s", r.returncode, ts_path)

        if ok:
            if mp4.exists() and mp4.stat().st_size > 0:
                try:
                    ffprobe = subprocess.run(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-count_frames",
                            "-select_streams",
                            "v:0",
                            "-show_entries",
                            "stream=nb_read_frames",
                            "-of",
                            "default=nokey=1:noprint_wrappers=1",
                            str(mp4),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=60,
                    )
                    if ffprobe.returncode == 0:
                        pkt = ffprobe.stdout.decode().strip()
                        if pkt.isdigit() and int(pkt) > 0:
                            ok = True
                        elif not pkt.isdigit():
                            ok = True
                        else:
                            ok = False
                    else:
                        logger.warning(
                            "ffprobe retornou %s para %s: %s",
                            ffprobe.returncode,
                            mp4,
                            ffprobe.stderr.decode().strip(),
                        )
                        ok = False
                except Exception as e:
                    logger.warning("ffprobe falhou para %s: %s", mp4, e)
            else:
                ok = False
                logger.error("Arquivo MP4 inexistente ou vazio: %s", mp4)
    except Exception as e:
        mp4 = "ERRO"
        logger.error("Erro ao converter %s: %s", ts_path, e)

    if ok:
        ts_path.unlink(missing_ok=True)
    return ok, mp4


def streamlink_json(url: str):
    try:
        r = subprocess.run(
            ["streamlink", "--json", url],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            logger.error(
                "streamlink retornou %s para %s: %s",
                r.returncode,
                url,
                r.stderr.strip(),
            )
            raise subprocess.CalledProcessError(
                r.returncode, r.args, output=r.stdout, stderr=r.stderr
            )
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        logger.error("Tempo excedido ao obter JSON do Streamlink: %s", url)
        raise
    except json.JSONDecodeError as e:
        logger.error("Resultado inválido do Streamlink para %s: %s", url, e)
        raise


def is_live(url: str):
    """Check if a stream is live using streamlink.

    When the first check fails and the URL looks like it belongs to
    YouTube, try again appending ``/live``. This helps when monitoring
    channel URLs instead of direct live links.
    """

    try:
        data = streamlink_json(url)
    except Exception:
        if "youtube" in url.lower():
            alt_url = url.rstrip("/") + "/live"
            data = streamlink_json(alt_url)
        else:
            raise

    streams = data.get("streams", {})
    if not streams:
        return False, data
    return True, data
