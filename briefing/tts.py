"""Turn the script into a single MP3 with OpenAI text-to-speech."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import WORK_DIR, dry_run, log

MAX_CHARS = 3000


def chunk_script(script: str, max_chars: int = MAX_CHARS) -> list[str]:
    chunks, cur = [], ""
    for para in [p.strip() for p in script.split("\n") if p.strip()]:
        pieces = [para] if len(para) <= max_chars else re.split(r"(?<=[.!?])\s+", para)
        for piece in pieces:
            if cur and len(cur) + len(piece) + 2 > max_chars:
                chunks.append(cur)
                cur = ""
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks


def synthesize(script: str, out_path: Path, cfg: dict) -> float:
    """Write MP3 to out_path; return duration in seconds."""
    b = cfg["briefing"]
    WORK_DIR.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=WORK_DIR))
    try:
        parts = []
        chunks = chunk_script(script)
        log.info("Synthesizing %d audio chunks", len(chunks))
        if not dry_run():
            from openai import OpenAI

            client = OpenAI(max_retries=4)
        for i, text in enumerate(chunks):
            p = tmp / f"part{i:03d}.mp3"
            if dry_run():
                secs = max(1, len(text.split()) / 2.6)
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                                "-t", f"{secs:.1f}", "-b:a", "64k", str(p)], check=True)
            else:
                with client.audio.speech.with_streaming_response.create(
                    model=b["tts_model"], voice=b["tts_voice"], input=text,
                    instructions=b.get("tts_style", ""), response_format="mp3",
                ) as resp:
                    resp.stream_to_file(p)
            parts.append(p)
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-ac", "1", "-ar", "24000", "-b:a", "64k", str(out_path)], check=True, cwd=tmp)
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_path)],
                             check=True, capture_output=True, text=True).stdout.strip()
        return float(dur)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
