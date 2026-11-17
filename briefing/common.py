import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
WORK_DIR = ROOT / "work"
SITE_DIR = ROOT / "site"
USER_AGENT = "MorningBriefing/1.0 (personal podcast digest)"

log = logging.getLogger("briefing")


def dry_run() -> bool:
    return os.environ.get("DRY_RUN") == "1"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("podcasts", [])
    cfg["websites"] = cfg.get("websites") or []
    return cfg


def load_state(name: str, default):
    path = STATE_DIR / f"{name}.json"
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def save_state(name: str, data) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_now(cfg: dict) -> datetime:
    return datetime.now(ZoneInfo(cfg["briefing"]["timezone"]))


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()
