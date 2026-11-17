"""CLI: python -m briefing [resolve|ingest|build]"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

from .common import SITE_DIR, dry_run, load_config, load_state, local_now, log, save_state, utcnow

MAX_ATTEMPTS = 3


def _sort_key(it: dict):
    # priority sources first (in config order), then other podcasts oldest->newest, then websites
    return (0 if it.get("priority") else 1 if it["kind"] == "podcast" else 2,
            it["order"] if it.get("priority") else 0, it["published"])


def cmd_resolve(cfg: dict) -> None:
    from .sources import resolve_sources

    for s in resolve_sources(cfg):
        print(f"{s['key']:<75} {s.get('found_name', '')!s:<45} {s.get('feed') or s.get('page')}")


def _stub_summary(it: dict, text: str, basis: str) -> dict:
    words = (text or it.get("notes") or "No description.").split()
    return {"headline": it["title"], "guests": [], "summary": " ".join(words[:80]),
            "takeaways": [" ".join(words[i:i + 20]) or "Takeaway" for i in (0, 20, 40)],
            "quote": {"text": " ".join(words[:25]), "speaker": "Host", "context": "Dry run"}}


def cmd_ingest(cfg: dict, budget_minutes: float) -> None:
    from .sources import article_text, list_new_items, resolve_sources

    b = cfg["briefing"]
    deadline = time.monotonic() + budget_minutes * 60
    items = load_state("items", {})
    known = {k for k, v in items.items() if not (v.get("status") == "failed" and v.get("attempts", 0) < MAX_ATTEMPTS)}
    candidates = []
    for src in resolve_sources(cfg):
        try:
            new = list_new_items(src, known, b["max_age_days"], b["per_source_cap"])
            if new:
                log.info("%s: %d new", src["name"], len(new))
            candidates += new
        except Exception as e:  # noqa: BLE001
            log.error("Failed to read %s: %s", src["key"], e)
    candidates.sort(key=_sort_key)
    for it in candidates:
        if time.monotonic() > deadline:
            log.warning("Time budget reached; %d items left for next run", len(candidates))
            break
        prev = items.get(it["id"], {})
        log.info("Processing [%s] %s", it["source"], it["title"] or it["link"])
        try:
            if it["kind"] == "podcast":
                if dry_run():
                    text, basis = it["notes"], "show notes"
                else:
                    from .transcribe import get_transcript
                    text, basis = get_transcript(it, b["transcribe_model"])
            else:
                text, basis = article_text(it), "article"
            if not it["title"]:
                it["title"] = "(untitled)"
            if dry_run():
                summary = _stub_summary(it, text, basis)
            else:
                from .claude import summarize
                summary = summarize(it, text, basis, b["claude_model"])
            it.update(summary=summary, basis=basis, status="ready", ingested=utcnow().isoformat())
        except Exception as e:  # noqa: BLE001
            log.exception("Failed on %s", it["title"])
            it.update(status="failed", attempts=prev.get("attempts", 0) + 1, error=str(e)[:500])
        it.pop("transcript_urls", None)
        it["notes"] = it.get("notes", "")[:1500]
        items[it["id"]] = it
        save_state("items", items)
    # prune old briefed/failed items
    cutoff = (utcnow() - timedelta(days=60)).isoformat()
    items = {k: v for k, v in items.items() if v.get("status") == "ready" or v["published"] > cutoff}
    save_state("items", items)


def _template_script(items: list[dict], date_str: str) -> str:
    out = [f"Good morning. It's {date_str}. Here's your briefing, with {len(items)} items today."]
    for it in items:
        s = it["summary"]
        out.append(f"From {it['source']}: {it['title']}. {s['summary']}")
        out.append("Key takeaways. " + " ".join(f"{n}. {t}" for n, t in zip(("First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh"), s["takeaways"])))
        out.append(f"The quote, from {s['quote']['speaker']}: {s['quote']['text']}")
    out.append("That's the briefing. Have a great day.")
    return "\n\n".join(out)


def cmd_build(cfg: dict) -> None:
    from . import emailer
    from .publish import base_url, write_episode_page, write_feed
    from .tts import synthesize

    b = cfg["briefing"]
    items = load_state("items", {})
    ready = sorted((v for v in items.values() if v.get("status") == "ready"), key=_sort_key)
    if not ready:
        log.info("Nothing new since the last briefing; no episode today.")
        return
    now = local_now(cfg)
    date_str = now.strftime("%A, %B %-d, %Y")
    slug = now.strftime("%Y-%m-%d")
    episodes = load_state("episodes", [])
    n = sum(1 for e in episodes if e["date"].startswith(slug))
    if n:
        slug += f"-{n + 1}"
    script = None
    if not dry_run():
        try:
            from .claude import write_script
            script = write_script(ready, date_str, b["claude_model"])
        except Exception:  # noqa: BLE001
            log.exception("Script writing failed; using simple template")
    script = script or _template_script(ready, date_str)
    audio_rel, page_rel = f"episodes/{slug}.mp3", f"episodes/{slug}.html"
    audio_path = SITE_DIR / audio_rel
    duration = synthesize(script, audio_path, cfg)
    title = f"{b['title']} — {now.strftime('%a %b %-d')}"
    url = base_url()
    write_episode_page(SITE_DIR / page_rel, title, f"{url}/{audio_rel}", ready)
    (SITE_DIR / f"episodes/{slug}.txt").write_text(script)
    sources = ", ".join(dict.fromkeys(it["source"] for it in ready))
    episodes.append({
        "date": slug, "guid": f"briefing-{slug}", "title": title, "published": datetime.now().astimezone().isoformat(),
        "audio": audio_rel, "page": page_rel, "bytes": audio_path.stat().st_size, "duration": duration,
        "description": f"{len(ready)} items: {sources}",
    })
    episodes.sort(key=lambda e: e["date"])
    keep, drop = episodes[-b["keep_episodes"]:], episodes[:-b["keep_episodes"]]
    for e in drop:
        for rel in (e["audio"], e["page"], e["page"].replace(".html", ".txt")):
            (SITE_DIR / rel).unlink(missing_ok=True)
    write_feed(cfg, keep)
    save_state("episodes", keep)
    minutes = round(duration / 60)
    try:
        emailer.send(f"{title} · {minutes} min · {len(ready)} items", ready, f"{url}/{audio_rel}", f"{url}/{page_rel}", minutes)
    except Exception:  # noqa: BLE001
        log.exception("Email failed")
    for it in ready:
        it.update(status="briefed", briefed_on=slug)
    save_state("items", items)
    log.info("Built %s (%d min, %d items)", audio_rel, minutes, len(ready))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser(prog="briefing")
    ap.add_argument("command", choices=["resolve", "ingest", "build"])
    ap.add_argument("--budget-minutes", type=float, default=float(os.environ.get("INGEST_BUDGET_MINUTES", 90)))
    args = ap.parse_args()
    cfg = load_config()
    {"resolve": lambda: cmd_resolve(cfg), "ingest": lambda: cmd_ingest(cfg, args.budget_minutes),
     "build": lambda: cmd_build(cfg)}[args.command]()


if __name__ == "__main__":
    main()
