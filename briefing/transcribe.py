"""Get a transcript for a podcast episode: publisher transcript first, else speech-to-text."""
import json
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .common import USER_AGENT, WORK_DIR, log, strip_html

CHUNK_SECONDS = 600  # 10-minute chunks keep each upload small and each response complete


def _parse_vtt_srt(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit() or line.startswith(("NOTE", "STYLE")):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if not lines or lines[-1] != line:
            lines.append(line)
    return " ".join(lines)


def publisher_transcript(urls: list) -> str | None:
    order = {"text/plain": 0, "application/json": 1, "text/vtt": 2, "application/x-subrip": 3, "application/srt": 3, "text/html": 4}
    for url, typ in sorted(urls, key=lambda u: order.get(u[1], 9)):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
            r.raise_for_status()
            body = r.text
            if "json" in typ:
                data = json.loads(body)
                segs = data.get("segments", [])
                text = " ".join(f"{s.get('speaker', '')}: {s.get('body', '')}".strip(": ") for s in segs)
            elif "vtt" in typ or "srt" in typ or "subrip" in typ:
                text = _parse_vtt_srt(body)
            elif "html" in typ:
                text = strip_html(body)
            else:
                text = body
            if len(text) > 2000:
                log.info("Using publisher transcript (%s, %d chars)", typ, len(text))
                return text
        except Exception as e:  # noqa: BLE001
            log.warning("Publisher transcript %s failed: %s", url, e)
    return None


def _download(url: str, dest: Path) -> None:
    with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def speech_to_text(audio_url: str, model: str) -> str:
    from openai import OpenAI

    client = OpenAI()
    WORK_DIR.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=WORK_DIR))
    try:
        src = tmp / "episode"
        _download(audio_url, src)
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
             "-f", "segment", "-segment_time", str(CHUNK_SECONDS), str(tmp / "chunk%03d.mp3")],
            check=True,
        )
        chunks = sorted(tmp.glob("chunk*.mp3"))
        log.info("Transcribing %d chunks (~%d min)", len(chunks), len(chunks) * CHUNK_SECONDS // 60)

        def run(p: Path) -> str:
            for attempt in range(3):
                try:
                    with open(p, "rb") as f:
                        return client.audio.transcriptions.create(model=model, file=f, response_format="text")
                except Exception as e:  # noqa: BLE001
                    log.warning("Chunk %s attempt %d failed: %s", p.name, attempt + 1, e)
            return ""

        with ThreadPoolExecutor(max_workers=6) as pool:
            parts = list(pool.map(run, chunks))
        return "\n".join(str(p).strip() for p in parts if p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def get_transcript(item: dict, model: str) -> tuple[str, str]:
    """Return (text, basis) where basis is 'transcript' or 'show notes'."""
    text = publisher_transcript(item.get("transcript_urls") or [])
    if text:
        return text, "transcript"
    if item.get("audio"):
        try:
            text = speech_to_text(item["audio"], model)
            if len(text) > 1000:
                return text, "transcript"
        except Exception as e:  # noqa: BLE001
            log.error("Transcription failed for %s: %s", item["title"], e)
    return item.get("notes", ""), "show notes"
