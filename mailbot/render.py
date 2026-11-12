"""Fill {{placeholders}} in a campaign template."""

from __future__ import annotations

import re
from html import escape


PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
PROTECTED_FIELDS = {"unsubscribe_url", "physical_address", "from_name", "email"}


class TemplateError(Exception):
    """A template asks for a value the recipient does not have."""


def first_name(name: str) -> str:
    parts = name.strip().split()
    return parts[0] if parts else "there"


def header_safe(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).strip()


def with_required_footer(template: str, *, html: bool) -> str:
    """Keep a postal address and an unsubscribe link on every message."""
    missing = []
    if "physical_address" not in template:
        missing.append("{{physical_address}}")
    if "unsubscribe_url" not in template:
        missing.append("Unsubscribe: {{unsubscribe_url}}")
    if not missing:
        return template
    if not html:
        return template.rstrip() + "\n\n" + "\n".join(missing) + "\n"
    block = "".join(f"<p>{line}</p>" for line in missing)
    snippet = f'<div style="font-size:12px;color:#5c6773;">{block}</div>'
    index = template.lower().rfind("</body>")
    if index == -1:
        return template + snippet
    return template[:index] + snippet + template[index:]


def unknown_placeholders(template: str, context: dict[str, str]) -> set[str]:
    keys = {match.group(1).lower() for match in PLACEHOLDER.finditer(template)}
    return {key for key in keys if key not in context}


def render(template: str, context: dict[str, str], *, html: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        if key not in context:
            raise TemplateError(f"Unknown placeholder {key}.")
        value = context[key]
        return escape(value, quote=True) if html else value

    return PLACEHOLDER.sub(replace, template)


def check_templates(templates: list[str], contexts: list[tuple[str, dict[str, str]]]) -> list[str]:
    problems: list[str] = []
    for label, context in contexts:
        missing: set[str] = set()
        for template in templates:
            missing.update(unknown_placeholders(template, context))
        if missing:
            keys = ", ".join(sorted(missing))
            problems.append(f"{label}: missing {keys}")
    return problems
