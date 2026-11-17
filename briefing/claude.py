"""Claude calls: per-item summaries and the spoken script."""
import json

import anthropic

from .common import log

FALLBACK_BETA = "server-side-fallback-2026-07-01"

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One-line hook for this episode/article (max ~15 words)."},
        "guests": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "takeaways": {"type": "array", "items": {"type": "string"}},
        "quote": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "speaker": {"type": "string"},
                "context": {"type": "string", "description": "One short sentence of context for the quote."},
            },
            "required": ["text", "speaker", "context"],
            "additionalProperties": False,
        },
    },
    "required": ["headline", "guests", "summary", "takeaways", "quote"],
    "additionalProperties": False,
}

SUMMARY_SYSTEM = """You prepare a busy investor/operator's daily briefing on podcasts and research pieces they follow.
For the content given, produce:
- headline: a crisp one-line hook.
- guests: names of guests (empty list if none / not applicable).
- summary: {summary_len} in plain prose. What was it about, who was involved, the core argument or story.
- takeaways: {n_takeaways} key takeaways. Each is one or two sentences, concrete and specific (numbers, names,
  frameworks, decisions), useful to someone who will never listen to the full episode. No generic platitudes.
- quote: the single most interesting, memorable, or insightful line actually said/written in the content.
  Copy it verbatim from the transcript (light cleanup of filler words like "um" or false starts is fine);
  never invent or paraphrase. Name the speaker as best you can tell from context.
If you only have show notes rather than a transcript, base everything on the notes, say so in the summary,
and pick the quote from the notes if one exists (use speaker "Show notes" if unattributed)."""

SCRIPT_SYSTEM = """You write the script for a one-host daily audio briefing that will be read aloud by a text-to-speech voice
while the listener drives to work. Voice: direct but friendly, like a sharp colleague catching them up.

Rules:
- Plain spoken English only. No markdown, bullets, headings, emoji, URLs, or stage directions.
- Write numbers and abbreviations the way they should be spoken when it helps (e.g. "twenty twenty-six", "P E" is fine as "private equity").
- Open with a short greeting with the day and date and a one-sentence preview of what's coming. Keep the intro under 40 words.
- Cover EVERY item in the order given. Do not skip or merge items.
- For each item: name the show (or site) and the episode title or topic, the guests if any, then a tight summary,
  then the key takeaways (signpost them naturally: "First...", "Second...", "And finally..."),
  then the quote, read verbatim and clearly attributed ("Here's the line that stuck out, from ...: ...").
- Items marked priority get a deeper treatment (roughly 500 to 700 words). Others roughly 250 to 400 words.
- If an item is based only on show notes, mention that briefly.
- Use short, varied transitions between items. Separate items with a blank line.
- Close with a one or two sentence sign-off. Nothing after it."""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4)


def _call(model: str, system: str, user: str, *, effort: str, schema: dict | None = None, max_tokens: int = 32000) -> str:
    output_config: dict = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    with _client().beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config=output_config,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined: {getattr(msg.stop_details, 'explanation', '')}")
    if msg.stop_reason == "max_tokens":
        log.warning("Claude hit max_tokens; output may be truncated")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def summarize(item: dict, text: str, basis: str, model: str) -> dict:
    priority = item.get("priority")
    system = SUMMARY_SYSTEM.format(
        summary_len="5 to 7 sentences" if priority else "3 to 5 sentences",
        n_takeaways="5 to 7" if priority else "3 to 5",
    )
    user = (
        f"Source: {item['source']}\nTitle: {item['title']}\nPublished: {item['published']}\n"
        f"Content type: {'podcast episode' if item['kind'] == 'podcast' else 'article / research piece'}\n"
        f"Basis: {basis}\n\n"
        f"Show notes / description:\n{item.get('notes', '')[:6000]}\n\n"
        f"<content>\n{text}\n</content>"
    )
    out = _call(model, system, user, effort="medium", schema=SUMMARY_SCHEMA, max_tokens=16000)
    return json.loads(out)


def write_script(items: list[dict], date_str: str, model: str) -> str:
    payload = [
        {
            "priority": bool(it.get("priority")),
            "source": it["source"],
            "kind": it["kind"],
            "title": it["title"],
            "basis": it.get("basis"),
            **{k: it["summary"][k] for k in ("headline", "guests", "summary", "takeaways", "quote")},
        }
        for it in items
    ]
    user = f"Today is {date_str}. Write today's briefing script covering these {len(items)} items in order:\n\n" + json.dumps(
        payload, indent=2, ensure_ascii=False
    )
    return _call(model, SCRIPT_SYSTEM, user, effort="medium", max_tokens=64000)
