import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)


def load_config(file_path: Path) -> Tuple[Path, Path, List[dict], Optional[str], Optional[str], Dict[str, Any]]:
    out_manual = Path.home() / "GRAVACOES MANUAIS"
    out_monitor = Path.home() / "MONITORAMENTO"
    monitored = []
    telegram_token = None
    telegram_chat_id = None
    hls_recovery = {}
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text())
            out_manual = Path(data.get("output_dir_manual", str(out_manual)))
            out_monitor = Path(data.get("output_dir_monitor", str(out_monitor)))
            monitored = data.get("monitored", [])
            telegram_token = data.get("telegram_token")
            telegram_chat_id = data.get("telegram_chat_id")
            hls_recovery = data.get("hls_recovery", {})
        except Exception as e:
            logger.error("Erro ao carregar configuração: %s", e)
    return out_manual, out_monitor, monitored, telegram_token, telegram_chat_id, hls_recovery


def save_config(
    file_path: Path,
    output_dir_manual: Path,
    output_dir_monitor: Path,
    monitored: List[dict],
    telegram_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    hls_recovery: Optional[Dict[str, Any]] = None,
):
    data = {
        "output_dir_manual": str(output_dir_manual),
        "output_dir_monitor": str(output_dir_monitor),
        "monitored": monitored,
    }
    prev = {}
    if file_path.exists():
        try:
            prev = json.loads(file_path.read_text())
        except Exception:
            prev = {}
        if telegram_token is None:
            telegram_token = prev.get("telegram_token")
        if telegram_chat_id is None:
            telegram_chat_id = prev.get("telegram_chat_id")
    data["telegram_token"] = telegram_token
    data["telegram_chat_id"] = telegram_chat_id
    data["hls_recovery"] = hls_recovery if hls_recovery is not None else prev.get("hls_recovery", {})
    tmp_file = file_path.with_suffix('.json.tmp')
    try:
        tmp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp_file.replace(file_path)
    except Exception as e:
        logger.error("Falha ao salvar configuração: %s", e)
        tmp_file.unlink(missing_ok=True)
