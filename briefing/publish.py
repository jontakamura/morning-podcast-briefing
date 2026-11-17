"""Build the private podcast feed and the written web version of each briefing."""
import html
import os
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

from .common import ROOT, SITE_DIR


def base_url() -> str:
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}"


def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def items_html(items: list[dict]) -> str:
    """Written version: quotes first, then takeaways, then summaries (same order as the email)."""
    q = "".join(
        f'<blockquote><p>&ldquo;{_e(it["summary"]["quote"]["text"])}&rdquo;</p>'
        f'<cite>— {_e(it["summary"]["quote"]["speaker"])}, <em>{_e(it["source"])}</em></cite></blockquote>'
        for it in items
    )
    t = "".join(
        f'<h3>{_e(it["source"])}: {_e(it["title"])}</h3><ul>'
        + "".join(f"<li>{_e(x)}</li>" for x in it["summary"]["takeaways"])
        + "</ul>"
        for it in items
    )
    s = "".join(
        f'<h3><a href="{_e(it["link"] or it.get("audio") or "#")}">{_e(it["source"])}: {_e(it["title"])}</a></h3>'
        + (f'<p class="meta">Guests: {_e(", ".join(it["summary"]["guests"]))}</p>' if it["summary"]["guests"] else "")
        + ('<p class="meta">Based on show notes only (no transcript available).</p>' if it.get("basis") == "show notes" else "")
        + f'<p>{_e(it["summary"]["summary"])}</p>'
        for it in items
    )
    return f"<h2>Key quotes</h2>{q}<h2>Key takeaways</h2>{t}<h2>Summaries</h2>{s}"


PAGE_CSS = """body{font:17px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:680px;margin:0 auto;padding:16px;color:#1d1d1f;background:#fff}
h1{font-size:26px;margin:8px 0}h2{margin-top:32px;border-bottom:2px solid #e5e5ea;padding-bottom:4px}h3{font-size:17px;margin:20px 0 6px}
blockquote{margin:14px 0;padding:10px 14px;border-left:4px solid #c8102e;background:#f6f6f8;border-radius:6px}blockquote p{margin:0 0 6px;font-size:18px}
cite{color:#555;font-style:normal;font-size:15px}.meta{color:#666;font-size:14px;margin:0}a{color:#0a58ca}audio{width:100%;margin:12px 0}
@media (prefers-color-scheme:dark){body{background:#111;color:#eee}blockquote{background:#1c1c1e}cite,.meta{color:#aaa}h2{border-color:#333}a{color:#6ea8fe}}"""


def write_episode_page(path: Path, title: str, audio_url: str, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex'><title>{_e(title)}</title><style>{PAGE_CSS}</style></head>"
        f"<body><h1>{_e(title)}</h1><audio controls preload='none' src='{_e(audio_url)}'></audio>{items_html(items)}</body></html>"
    )


def write_feed(cfg: dict, episodes: list[dict]) -> None:
    url = base_url()
    title = cfg["briefing"]["title"]
    entries = []
    for ep in sorted(episodes, key=lambda e: e["date"], reverse=True):
        pub = datetime.fromisoformat(ep["published"])
        desc = f'<p>{_e(ep["description"])}</p><p><a href="{url}/{ep["page"]}">Read the written version</a></p>'
        entries.append(f"""    <item>
      <title>{_e(ep["title"])}</title>
      <guid isPermaLink="false">{_e(ep["guid"])}</guid>
      <pubDate>{format_datetime(pub)}</pubDate>
      <link>{url}/{ep["page"]}</link>
      <description><![CDATA[{desc}]]></description>
      <enclosure url="{url}/{ep["audio"]}" length="{ep["bytes"]}" type="audio/mpeg"/>
      <itunes:duration>{int(ep["duration"])}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{_e(title)}</title>
    <link>{url}/</link>
    <language>en-us</language>
    <description>Private daily briefing: summaries, key takeaways and quotes from the podcasts and research I follow.</description>
    <itunes:author>Morning Briefing</itunes:author>
    <itunes:image href="{url}/cover.png"/>
    <itunes:category text="News"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>yes</itunes:block>
    <itunes:type>episodic</itunes:type>
{chr(10).join(entries)}
  </channel>
</rss>
"""
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "feed.xml").write_text(feed)
    cover = ROOT / "assets" / "cover.png"
    if cover.exists():
        (SITE_DIR / "cover.png").write_bytes(cover.read_bytes())
    (SITE_DIR / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (SITE_DIR / ".nojekyll").write_text("")
    latest = sorted(episodes, key=lambda e: e["date"], reverse=True)
    links = "".join(f'<li><a href="{_e(e["page"])}">{_e(e["title"])}</a></li>' for e in latest)
    (SITE_DIR / "index.html").write_text(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex'><title>{_e(title)}</title><style>{PAGE_CSS}</style></head><body><h1>{_e(title)}</h1>"
        f"<p>Add to Apple Podcasts: Library → ••• → Follow a Show by URL → <code>{url}/feed.xml</code></p><ul>{links}</ul></body></html>"
    )
