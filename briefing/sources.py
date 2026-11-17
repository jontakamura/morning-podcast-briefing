"""Find podcast/website feeds and list new items."""
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import requests

from .common import USER_AGENT, load_state, log, norm, save_state, strip_html, utcnow

PODCAST_NS = "https://podcastindex.org/namespace/1.0"
HEADERS = {"User-Agent": USER_AGENT}


def http_get(url: str, timeout: int = 60) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def _id(prefix: str, *parts: str) -> str:
    return prefix + ":" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- resolving
def _lookup_apple(p: dict) -> dict:
    r = http_get(
        "https://itunes.apple.com/search?"
        + requests.compat.urlencode(
            {"term": p.get("search", p["name"]), "media": "podcast", "entity": "podcast", "limit": 25}
        )
    )
    target = norm(p["name"])
    best, best_score = None, -1
    for res in r.json().get("results", []):
        if not res.get("feedUrl"):
            continue
        cn = norm(res.get("collectionName", ""))
        score = 0
        if cn == target:
            score += 10
        elif cn.startswith(target) or target in cn:
            score += 5
        if p.get("author") and norm(p["author"]) in norm(res.get("artistName", "")):
            score += 6
        if score > best_score:
            best, best_score = res, score
    if best is None or best_score < 10:
        raise LookupError(f"Could not confidently find '{p['name']}' in Apple Podcasts; add a feed: URL in config.yaml")
    return {"feed": best["feedUrl"], "found_name": best["collectionName"], "found_author": best.get("artistName", "")}


def _discover_site_feed(url: str) -> str | None:
    try:
        html = http_get(url).text
    except Exception as e:  # noqa: BLE001
        log.warning("Could not load %s: %s", url, e)
        return None
    for m in re.finditer(r"<link[^>]+>", html, flags=re.I):
        tag = m.group(0)
        if re.search(r"application/(rss|atom)\+xml", tag, re.I):
            href = re.search(r'href=["\']([^"\']+)', tag)
            if href:
                return urljoin(url, href.group(1))
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    for path in ("/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml", "/blog/rss.xml"):
        for root in (url.rstrip("/"), base):
            try:
                d = feedparser.parse(http_get(root + path, timeout=20).content)
                if d.entries:
                    return root + path
            except Exception:  # noqa: BLE001
                continue
    return None


def resolve_sources(cfg: dict) -> list[dict]:
    """Return enabled sources with a resolved feed (or page) URL. Cached in state/feeds.json."""
    cache = load_state("feeds", {})
    out = []
    for kind, entries in (("podcast", cfg["podcasts"]), ("website", cfg["websites"])):
        for i, src in enumerate(entries):
            if src.get("enabled") is False:
                continue
            key = f"{kind}:{src['name']}"
            info = dict(cache.get(key, {}))
            if src.get("feed"):
                info = {"feed": src["feed"]}
            elif not info.get("feed") and not info.get("page"):
                try:
                    if kind == "podcast":
                        info = _lookup_apple(src)
                    else:
                        feed = _discover_site_feed(src["url"])
                        info = {"feed": feed} if feed else {"page": src["url"]}
                    log.info("Resolved %s -> %s", key, info)
                except Exception as e:  # noqa: BLE001
                    log.error("Could not resolve %s: %s", key, e)
                    continue
                cache[key] = info
            out.append({**src, **info, "kind": kind, "key": key, "order": i, "priority": bool(src.get("priority"))})
    save_state("feeds", cache)
    return out


# ---------------------------------------------------------------- listing items
def _published(entry) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def _transcripts_by_guid(raw: bytes) -> dict:
    """Map item guid/title -> list of (url, type) from <podcast:transcript> tags."""
    out = {}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        tags = [(t.get("url"), t.get("type", "")) for t in item.findall(f"{{{PODCAST_NS}}}transcript") if t.get("url")]
        if not tags:
            continue
        for k in (item.findtext("guid"), item.findtext("title")):
            if k:
                out[k.strip()] = tags
    return out


def list_new_items(src: dict, known: dict, max_age_days: int, cap: int) -> list[dict]:
    cutoff = utcnow() - timedelta(days=max_age_days)
    if src.get("page"):
        return _list_page_links(src, known, cap)
    raw = http_get(src["feed"]).content
    d = feedparser.parse(raw)
    transcripts = _transcripts_by_guid(raw) if src["kind"] == "podcast" else {}
    items = []
    for e in d.entries[:30]:
        pub = _published(e)
        if not pub or pub < cutoff:
            continue
        link = e.get("link", "")
        audio = next(
            (l["href"] for l in e.get("links", []) if l.get("rel") == "enclosure" and "audio" in l.get("type", "audio")),
            None,
        )
        if src["kind"] == "podcast" and not audio:
            continue
        guid = e.get("id") or audio or link
        item_id = _id("pod" if src["kind"] == "podcast" else "web", src["feed"], guid)
        if item_id in known:
            continue
        content = ""
        if e.get("content"):
            content = max((c.get("value", "") for c in e["content"]), key=len)
        notes = strip_html(content or e.get("summary", ""))
        items.append({
            "id": item_id,
            "kind": src["kind"],
            "source": src["name"],
            "source_key": src["key"],
            "priority": src["priority"],
            "order": src["order"],
            "title": e.get("title", "(untitled)"),
            "link": link,
            "audio": audio,
            "published": pub.isoformat(),
            "duration": e.get("itunes_duration", ""),
            "notes": notes[:20000],
            "transcript_urls": transcripts.get((e.get("id") or "").strip()) or transcripts.get(e.get("title", "").strip()) or [],
        })
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:cap]


def _list_page_links(src: dict, known: dict, cap: int) -> list[dict]:
    """For sites without a feed: treat new same-site links under the page path as new articles."""
    seen = load_state("page_links", {})
    page = src["page"]
    html = http_get(page).text
    parsed = urlparse(page)
    links = []
    for href in re.findall(r'href=["\']([^"\'#]+)', html):
        u = urljoin(page, href)
        pu = urlparse(u)
        if pu.netloc != parsed.netloc or u.rstrip("/") == page.rstrip("/"):
            continue
        if not pu.path.startswith(parsed.path.rstrip("/") + "/") or len(pu.path.strip("/").split("/")) < 2:
            continue
        if u not in links:
            links.append(u)
    prev = set(seen.get(src["key"], []))
    first_time = src["key"] not in seen
    seen[src["key"]] = sorted(prev | set(links))[-2000:]
    save_state("page_links", seen)
    if first_time:  # baseline: don't flood with the whole archive
        return []
    new = [u for u in links if u not in prev][:cap]
    now = utcnow().isoformat()
    return [{
        "id": _id("web", page, u), "kind": "website", "source": src["name"], "source_key": src["key"],
        "priority": src["priority"], "order": src["order"], "title": "", "link": u, "audio": None,
        "published": now, "duration": "", "notes": "", "transcript_urls": [],
    } for u in new if _id("web", page, u) not in known]


def article_text(item: dict) -> str:
    """Full article text for a website item (falls back to feed content)."""
    import trafilatura

    text = ""
    if item.get("link"):
        try:
            downloaded = trafilatura.fetch_url(item["link"])
            if downloaded:
                text = trafilatura.extract(downloaded, include_comments=False) or ""
                if not item.get("title"):
                    meta = trafilatura.extract_metadata(downloaded)
                    if meta and meta.title:
                        item["title"] = meta.title
        except Exception as e:  # noqa: BLE001
            log.warning("Article fetch failed for %s: %s", item["link"], e)
    if len(text) < len(item.get("notes", "")):
        text = item["notes"]
    return text
