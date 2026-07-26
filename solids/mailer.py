"""Getting the daily email out.

Three transports, because the obvious one is the worst one:

  outbox  Write the message to a tab on the sheet. A small Apps Script on the
          sheet mails it. No new credential of any kind, because the sheet
          already belongs to you. See appsscript/Code.gs.
  resend  An HTTP API key that can only send email. Cannot read anything.
  smtp    A Gmail app password. Works, but grants full IMAP read access to the
          entire mailbox, which is far more than sending one message a day needs.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
RESEND_URL = "https://api.resend.com/emails"

OUTBOX_HEADER = ["Queued", "Send after", "To", "Subject", "Status", "HTML"]


def send_email(
    subject: str,
    html_body: str,
    text_body: str,
    to: str,
    sender: str,
    sender_name: str = "Solids",
    transport: str = "outbox",
    store=None,
    outbox_tab: str = "Outbox",
) -> None:
    if transport == "smtp":
        _send_smtp(subject, html_body, text_body, to, sender, sender_name)
    elif transport == "resend":
        _send_resend(subject, html_body, text_body, to, sender, sender_name)
    elif transport == "outbox":
        _queue_outbox(subject, html_body, to, store, outbox_tab)
    else:
        raise SystemExit(f"Unknown mail transport {transport!r}.")


def _send_smtp(subject, html_body, text_body, to, sender, sender_name) -> None:
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        raise SystemExit(
            "GMAIL_APP_PASSWORD is not set.\n"
            "Create one at https://myaccount.google.com/apppasswords "
            "(requires 2-step verification), then export it.\n"
            "Note that an app password grants full mailbox access, not just sending."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


def _send_resend(subject, html_body, text_body, to, sender, sender_name) -> None:
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise SystemExit(
            "RESEND_API_KEY is not set. Create one at https://resend.com/api-keys "
            "with sending permission only."
        )
    payload = json.dumps({
        "from": f"{sender_name} <{sender}>",
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }).encode()
    req = urllib.request.Request(
        RESEND_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Cloudflare sits in front of the API and blocks urllib's default
            # user agent outright, which surfaces as a confusing 403.
            "User-Agent": "solids/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body).get("message", body)
        except json.JSONDecodeError:
            pass
        raise SystemExit(f"Resend rejected the message ({e.code}): {body}")


def _queue_outbox(subject, html_body, to, store, outbox_tab) -> None:
    """Leave the message on the sheet for the Apps Script trigger to send.

    The Send after timestamp lets the script ignore anything stale, so a late or
    failed run does not mail yesterday's plan tomorrow morning.
    """
    if store is None:
        raise SystemExit("The outbox transport needs the spreadsheet, but none was open.")
    now = dt.datetime.now()
    store.ensure_tab(outbox_tab, OUTBOX_HEADER)
    store.append(
        outbox_tab,
        [[
            now.strftime("%Y-%m-%d %H:%M"),
            now.strftime("%Y-%m-%d"),
            to,
            subject,
            "queued",
            html_body,
        ]],
    )
