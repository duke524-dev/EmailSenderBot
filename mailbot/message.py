"""Build one multipart message for one recipient."""

from __future__ import annotations

import re
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape, unescape
from urllib.parse import quote

from mailbot.config import Settings
from mailbot.hiring_templates import LOGO_CID, LOGO_FILENAME, logo_bytes, logo_path
from mailbot.render import PROTECTED_FIELDS, first_name, header_safe, render, with_required_footer
from mailbot.store import Subscriber


TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
BLOCK_BREAK_RE = re.compile(
    r"</\s*(p|div|tr|table|h[1-6]|li|section|article|header|footer)\s*>",
    re.IGNORECASE,
)
BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)


def content_to_html(body: str) -> str:
    """Turn plain typed content into a simple HTML body."""
    paragraphs = [part.strip() for part in body.replace("\r\n", "\n").split("\n\n")]
    blocks = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        lines = "<br>\n".join(escape(line) for line in paragraph.split("\n"))
        blocks.append(f"<p style=\"margin:0 0 16px;\">{lines}</p>")
    inner = "\n".join(blocks) or "<p></p>"
    return wrap_html_document(inner)


def looks_like_html(body: str) -> bool:
    """True when the typed content already contains HTML markup."""
    return bool(
        re.search(
            r"<\s*(html|body|div|p|br|h[1-6]|a|span|table|tr|td|ul|ol|li|strong|em|b|i|img|hr)\b",
            body or "",
            flags=re.IGNORECASE,
        )
    )


def wrap_html_document(inner_or_document: str) -> str:
    """Leave a full HTML document alone; otherwise wrap a fragment."""
    stripped = inner_or_document.strip()
    lower = stripped.lower()
    if "<html" in lower:
        return stripped
    return (
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="utf-8"></head>'
        '<body style="font-family:Georgia,serif;font-size:16px;'
        f'line-height:1.5;color:#1c2430;">{stripped}</body></html>'
    )


def html_to_text(html: str) -> str:
    """Build a plain-text fallback from HTML content."""
    cleaned = SCRIPT_STYLE_RE.sub("", html)
    cleaned = BR_RE.sub("\n", cleaned)
    cleaned = BLOCK_BREAK_RE.sub("\n", cleaned)
    cleaned = TAG_RE.sub("", cleaned)
    cleaned = unescape(cleaned)
    lines = [line.rstrip() for line in cleaned.replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def append_postal_html(html: str, address: str) -> str:
    footer = (
        f'<p style="margin-top:24px;font-size:12px;color:#5c6773;">'
        f"{escape(address)}</p>"
    )
    index = html.lower().rfind("</body>")
    if index == -1:
        return html + footer
    return html[:index] + footer + html[index:]


def resolve_body_format(body: str, body_format: str = "plain") -> str:
    """Honor an explicit format, and promote obvious HTML pasted as plain."""
    fmt = (body_format or "plain").strip().lower()
    if fmt not in {"plain", "html", "auto"}:
        raise ValueError("body_format must be plain, html, or auto.")
    if fmt == "html":
        return "html"
    if fmt == "auto":
        return "html" if looks_like_html(body) else "plain"
    # Explicit plain still upgrades when the body is clearly markup. That stops
    # Gmail from showing escaped tags when HTML was pasted without switching mode.
    if looks_like_html(body):
        return "html"
    return "plain"


def attach_inline_logo(message: EmailMessage, html: str) -> None:
    """Attach the HireLens mark when the HTML references cid:hirelens-logo."""
    if f"cid:{LOGO_CID}" not in html:
        return
    if not logo_path().is_file():
        return
    html_part = message.get_body(preferencelist=("html",))
    if html_part is None:
        return
    html_part.add_related(
        logo_bytes(),
        maintype="image",
        subtype="png",
        cid=f"<{LOGO_CID}>",
        filename=LOGO_FILENAME,
        disposition="inline",
    )


def build_compose_message(
    settings: Settings,
    *,
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
    body_format: str = "plain",
) -> EmailMessage:
    """Build a one-off message from the web compose form."""
    raw = body.strip()
    fmt = resolve_body_format(raw, body_format)

    if fmt == "html":
        html = wrap_html_document(SCRIPT_STYLE_RE.sub("", raw))
        # Preview uses /static/...; rewrite to cid so the mark travels with the mail.
        html = html.replace("/static/hirelens-logo.png", f"cid:{LOGO_CID}")
        text = html_to_text(raw) or " "
    else:
        html = content_to_html(raw)
        text = raw or " "

    if settings.physical_address:
        text = f"{text}\n\n{settings.physical_address}".strip()
        html = append_postal_html(html, settings.physical_address)

    message = EmailMessage()
    message["From"] = formataddr((header_safe(settings.from_name), settings.from_email))
    display = header_safe(to_name)
    message["To"] = formataddr((display, to_email)) if display else to_email
    message["Subject"] = header_safe(subject)
    message["Reply-To"] = settings.reply_to
    message["Date"] = formatdate(localtime=True)
    message["MIME-Version"] = "1.0"
    domain = settings.from_email.split("@", 1)[-1] or "localhost"
    message["Message-ID"] = make_msgid(domain=domain)
    # Plain first, HTML last: clients should prefer the last alternative.
    message.set_content(text, subtype="plain", charset="utf-8")
    message.add_alternative(html, subtype="html", charset="utf-8")
    if fmt == "html":
        attach_inline_logo(message, html)
    return message



def unsubscribe_target(settings: Settings, token: str) -> str:
    if settings.public_base_url:
        return f"{settings.public_base_url}/u/{token}"
    subject = quote(f"Unsubscribe {token}")
    return f"mailto:{settings.reply_to}?subject={subject}"


def merge_context(settings: Settings, subscriber: Subscriber, *, subject: str) -> dict[str, str]:
    context = {
        "email": subscriber.email,
        "name": subscriber.name,
        "first_name": first_name(subscriber.name),
        "company": subscriber.company,
        "from_name": settings.from_name,
        "physical_address": settings.physical_address,
        "unsubscribe_url": unsubscribe_target(settings, subscriber.unsubscribe_token),
        "subject": subject,
    }
    for key, value in subscriber.fields.items():
        if key in PROTECTED_FIELDS or key == "first_name":
            continue
        context[key] = value
    custom_first = subscriber.fields.get("first_name", "").strip()
    if custom_first:
        context["first_name"] = custom_first
    return context


def build_message(
    settings: Settings,
    subscriber: Subscriber,
    *,
    subject_template: str,
    html_template: str,
    text_template: str,
) -> EmailMessage:
    html_template = with_required_footer(html_template, html=True)
    text_template = with_required_footer(text_template, html=False)
    subject_context = merge_context(settings, subscriber, subject=subject_template)
    subject = header_safe(render(subject_template, subject_context, html=False))
    context = merge_context(settings, subscriber, subject=subject)
    text = render(text_template, context, html=False)
    html = render(html_template, context, html=True)

    message = EmailMessage()
    message["From"] = formataddr((header_safe(settings.from_name), settings.from_email))
    display = header_safe(subscriber.name)
    message["To"] = formataddr((display, subscriber.email)) if display else subscriber.email
    message["Subject"] = subject
    message["Reply-To"] = settings.reply_to
    message["Date"] = formatdate(localtime=True)
    domain = settings.from_email.split("@", 1)[1]
    message["Message-ID"] = make_msgid(domain=domain)

    mailto = f"mailto:{settings.reply_to}?subject=Unsubscribe%20{subscriber.unsubscribe_token}"
    parts = [f"<{mailto}>"]
    if settings.public_base_url:
        parts.append(f"<{unsubscribe_target(settings, subscriber.unsubscribe_token)}>")
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message["List-Unsubscribe"] = ", ".join(parts)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message
