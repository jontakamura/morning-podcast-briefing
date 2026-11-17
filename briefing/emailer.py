"""Send the written briefing by email (Gmail SMTP with an app password)."""
import os
import smtplib
from email.message import EmailMessage

from .common import log
from .publish import PAGE_CSS, _e, items_html


def send(subject: str, items: list[dict], audio_url: str, page_url: str, minutes: int) -> None:
    user = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("EMAIL_TO") or user
    if not (user and password):
        log.warning("Email not configured (GMAIL_ADDRESS / GMAIL_APP_PASSWORD); skipping email")
        return
    body = (
        f"<html><head><style>{PAGE_CSS}</style></head><body>"
        f"<p><a href='{_e(audio_url)}'>▶ Listen ({minutes} min)</a> · <a href='{_e(page_url)}'>Open in browser</a> · "
        f"{len(items)} item{'s' if len(items) != 1 else ''}</p>"
        f"{items_html(items)}</body></html>"
    )
    plain = []
    plain.append("KEY QUOTES\n")
    for it in items:
        q = it["summary"]["quote"]
        plain.append(f"“{q['text']}”\n  - {q['speaker']}, {it['source']}\n")
    plain.append("\nKEY TAKEAWAYS\n")
    for it in items:
        plain.append(f"\n{it['source']}: {it['title']}")
        plain += [f"  - {t}" for t in it["summary"]["takeaways"]]
    plain.append("\n\nSUMMARIES\n")
    for it in items:
        plain.append(f"\n{it['source']}: {it['title']}\n{it['summary']['summary']}\n{it['link'] or ''}")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Morning Briefing <{user}>"
    msg["To"] = to
    msg.set_content("\n".join(plain) + f"\n\nListen: {audio_url}\n")
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, password)
        s.send_message(msg)
    log.info("Email sent")
