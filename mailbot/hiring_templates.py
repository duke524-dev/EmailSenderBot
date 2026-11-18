"""Built-in HireLens hiring email HTML and plain-text templates."""

from __future__ import annotations

import base64
import json
import random
from functools import lru_cache
from pathlib import Path

from mailbot.config import load_settings, project_root


SUBJECT = "Account Executive Manager — Part-Time Opportunity at HireLens"
DEFAULT_APPLY_URL = "https://forms.gle/d5ifR5Htk7jwRxmC9"
LOGO_CID = "hirelens-logo"
LOGO_FILENAME = "hirelens-logo.png"
LOGO_STATIC_PATH = f"/static/{LOGO_FILENAME}"

COPY = {
    "eyebrow": "Hiring at HireLens",
    "title": "Account Executive Manager",
    "lede": "Thank you for your interest in joining our team!",
    "intro": (
        "We are looking for motivated, talented, and results-driven professionals "
        "to join us as an Account Executive Manager."
    ),
    "details_heading": "Position Details",
    "details": [
        ("Employment Type", "Part-Time"),
        ("Time Commitment", "10 hours per week"),
        ("Work Arrangement", "Flexible schedule"),
        ("Position", "Account Executive Manager"),
    ],
    "team_heading": "Work With Talented Developers",
    "team_p1": (
        "As an Account Executive Manager, you will have the opportunity to work closely "
        "with our talented software developers, engineers, and technology professionals."
    ),
    "team_p2": (
        "You'll collaborate with an experienced and diverse technical team, helping connect "
        "business opportunities with innovative technology solutions."
    ),
    "responsibilities_heading": "Your responsibilities will include:",
    "responsibilities": [
        "Building and maintaining relationships with potential clients and business partners.",
        "Identifying new business opportunities and developing client relationships.",
        "Collaborating with our software development team to understand technical capabilities and project requirements.",
        "Communicating client needs, project expectations, and business objectives to developers.",
        "Supporting business development initiatives and exploring opportunities for long-term collaboration.",
    ],
    "values": (
        "We value teamwork, clear communication, initiative, and the ability to build strong "
        "professional relationships."
    ),
    "who_heading": "Who We're Looking For",
    "who_p1": (
        "We welcome candidates with strong communication, relationship-building, business "
        "development, and sales management skills who are passionate about technology and "
        "working with talented engineering professionals."
    ),
    "who_p2": (
        "Previous experience in technology sales, account management, business development, "
        "or client relations is a plus."
    ),
    "apply_heading": "Application Instructions",
    "apply_p1": (
        "Please complete this application form with accurate and up-to-date information. "
        "Include your contact details, professional background, and relevant experience."
    ),
    "apply_p2": (
        "Our recruitment team will carefully review your application and contact shortlisted "
        "candidates regarding the next steps in the hiring process."
    ),
    "closing": (
        "We appreciate your time and interest in exploring this opportunity. "
        "We look forward to learning more about you and potentially welcoming you to our team!"
    ),
    "cta": "Apply Now",
    "cta_url": DEFAULT_APPLY_URL,
    "footer": "HireLens · Building teams that ship",
}


def resolve_apply_url(apply_url: str | None = None) -> str:
    if apply_url and apply_url.strip():
        return apply_url.strip()
    try:
        return load_settings().apply_url or DEFAULT_APPLY_URL
    except Exception:
        return DEFAULT_APPLY_URL


THEMES: list[dict[str, str]] = [
    {
        "id": "horizon",
        "name": "Horizon",
        "blurb": "Teal brand band with a clear hiring layout",
        "bg": "#e8f1f4",
        "card": "#ffffff",
        "ink": "#102a36",
        "muted": "#4d6470",
        "accent": "#0f766e",
        "accent_text": "#f4fffd",
        "hero": "#0b3d4a",
        "hero_text": "#e7f6f4",
        "rule": "#d5e3e8",
        "chip": "#d9f3ef",
    },
    {
        "id": "night-shift",
        "name": "Night Shift",
        "blurb": "Dark navy canvas with a bright lime CTA",
        "bg": "#0b1220",
        "card": "#141c2b",
        "ink": "#e8eef7",
        "muted": "#9aabc0",
        "accent": "#b6f56c",
        "accent_text": "#0b1220",
        "hero": "#101826",
        "hero_text": "#e8eef7",
        "rule": "#243247",
        "chip": "#1c2738",
    },
    {
        "id": "signal",
        "name": "Signal",
        "blurb": "Cool slate frame with a vivid coral accent",
        "bg": "#edf1f5",
        "card": "#ffffff",
        "ink": "#1c2430",
        "muted": "#5b6b7a",
        "accent": "#e11d48",
        "accent_text": "#fff7f8",
        "hero": "#1f2937",
        "hero_text": "#f8fafc",
        "rule": "#dbe3ec",
        "chip": "#ffe4e8",
    },
    {
        "id": "grove",
        "name": "Grove",
        "blurb": "Forest green hiring letter on soft mint",
        "bg": "#e7f2ea",
        "card": "#fbfffc",
        "ink": "#143026",
        "muted": "#4d6a5b",
        "accent": "#1b7a4c",
        "accent_text": "#f3fff8",
        "hero": "#1b4332",
        "hero_text": "#e9f7ef",
        "rule": "#cfe0d5",
        "chip": "#d8f3e3",
    },
    {
        "id": "blueprint",
        "name": "Blueprint",
        "blurb": "High-contrast black, white, and cyan blocks",
        "bg": "#dfe7ee",
        "card": "#ffffff",
        "ink": "#0a0a0a",
        "muted": "#4b5563",
        "accent": "#0891b2",
        "accent_text": "#f0fdff",
        "hero": "#000000",
        "hero_text": "#ffffff",
        "rule": "#d1d5db",
        "chip": "#cffafe",
    },
    {
        "id": "clear-day",
        "name": "Clear Day",
        "blurb": "Airy sky-blue letter with soft depth",
        "bg": "#e9f4fb",
        "card": "#ffffff",
        "ink": "#16324a",
        "muted": "#5b7388",
        "accent": "#0284c7",
        "accent_text": "#f0f9ff",
        "hero": "#0c4a6e",
        "hero_text": "#e0f2fe",
        "rule": "#d5e6f2",
        "chip": "#e0f2fe",
    },
    {
        "id": "atelier",
        "name": "Atelier",
        "blurb": "Deep burgundy and gold for a polished invite",
        "bg": "#f3ece6",
        "card": "#fffaf6",
        "ink": "#2a1218",
        "muted": "#7a5a62",
        "accent": "#b45309",
        "accent_text": "#fff7ed",
        "hero": "#4c0519",
        "hero_text": "#fdf2f8",
        "rule": "#e8d5cf",
        "chip": "#fde68a",
    },
    {
        "id": "beacon",
        "name": "Beacon",
        "blurb": "Charcoal body with amber highlights",
        "bg": "#eceff3",
        "card": "#ffffff",
        "ink": "#111827",
        "muted": "#6b7280",
        "accent": "#d97706",
        "accent_text": "#fffbeb",
        "hero": "#1f2937",
        "hero_text": "#f9fafb",
        "rule": "#e5e7eb",
        "chip": "#fef3c7",
    },
    {
        "id": "split-focus",
        "name": "Split Focus",
        "blurb": "Two-tone teal and ink split header",
        "bg": "#e6eef2",
        "card": "#ffffff",
        "ink": "#12232d",
        "muted": "#56707c",
        "accent": "#0e7490",
        "accent_text": "#ecfeff",
        "hero": "#155e75",
        "hero_text": "#ecfeff",
        "rule": "#cfe0e7",
        "chip": "#cffafe",
    },
    {
        "id": "lattice",
        "name": "Lattice",
        "blurb": "Minimal Swiss-inspired ice-blue composition",
        "bg": "#f3f6f8",
        "card": "#ffffff",
        "ink": "#0f172a",
        "muted": "#64748b",
        "accent": "#334155",
        "accent_text": "#f8fafc",
        "hero": "#0f172a",
        "hero_text": "#f8fafc",
        "rule": "#e2e8f0",
        "chip": "#e2e8f0",
    },
]


def hiring_dir() -> Path:
    path = project_root() / "templates" / "hiring"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logo_path() -> Path:
    return project_root() / "web" / "static" / LOGO_FILENAME


@lru_cache(maxsize=1)
def logo_bytes() -> bytes:
    path = logo_path()
    if not path.is_file():
        raise FileNotFoundError(f"Missing HireLens logo at {path}")
    return path.read_bytes()


def logo_data_uri() -> str:
    encoded = base64.b64encode(logo_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def resolve_logo_src(logo_src: str | None = None) -> str:
    """Preview uses /static; sent mail uses cid:; tests may pass a data URI."""
    if logo_src and logo_src.strip():
        return logo_src.strip()
    return LOGO_STATIC_PATH


def _details_rows(theme: dict[str, str]) -> str:
    rows = []
    for label, value in COPY["details"]:
        rows.append(
            f"""
            <tr>
              <td style="padding:8px 0;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:{theme['muted']};width:42%;">{label}</td>
              <td style="padding:8px 0;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:{theme['ink']};font-weight:700;">{value}</td>
            </tr>
            """
        )
    return "".join(rows)


def _responsibilities(theme: dict[str, str]) -> str:
    items = []
    for item in COPY["responsibilities"]:
        items.append(
            f"""
            <tr>
              <td valign="top" style="padding:0 10px 12px 0;color:{theme['accent']};font-size:16px;line-height:1.4;">▸</td>
              <td style="padding:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;color:{theme['ink']};">{item}</td>
            </tr>
            """
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        + "".join(items)
        + "</table>"
    )


PLAIN_TEXT_BODY = """Hello,
I hope you’re doing well.
I came across your profile and found it quite impressive. As you may be aware, there has been a sharp increase in demand for tasks related to AI training. However, most platforms impose various restrictions.
Since these platforms typically allow only one account per user, our engineers are facing difficulties in carrying out their work.
For this reason, we are looking to secure additional accounts. If you would be willing to assist us, we would be happy to offer you 50% of the earnings generated from those accounts as compensation.
Please feel free to reach out if you are interested.
"""


def render_plain_template(*, apply_url: str | None = None) -> str:
    """Build the shared plain-text email body."""
    _ = apply_url  # Kept so existing call sites can still pass apply_url.
    return PLAIN_TEXT_BODY


def render_template(
    theme: dict[str, str],
    *,
    apply_url: str | None = None,
    logo_src: str | None = None,
) -> str:
    """Build one multipart-friendly HTML email for a theme."""
    details = _details_rows(theme)
    responsibilities = _responsibilities(theme)
    cta_url = resolve_apply_url(apply_url)
    mark = resolve_logo_src(logo_src)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{COPY['title']}</title>
</head>
<body style="margin:0;padding:0;background:{theme['bg']};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{theme['bg']};">
    <tr>
      <td align="center" style="padding:28px 12px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;background:{theme['card']};border-radius:18px;overflow:hidden;">
          <tr>
            <td style="padding:22px 32px 18px;background:#000000;color:#f5f5f5;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td valign="middle" style="padding:0;">
                    <img src="{mark}" width="168" height="40" alt="HireLens" style="display:block;width:168px;max-width:60%;height:auto;border:0;outline:none;text-decoration:none;">
                  </td>
                  <td valign="middle" align="right" style="padding:0 0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:#c4c4c4;">
                    {COPY['eyebrow']}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td background="{mark}" bgcolor="{theme['hero']}" style="padding:28px 32px 22px;background-color:{theme['hero']};background-image:url('{mark}');background-repeat:no-repeat;background-position:center;background-size:72% auto;color:{theme['hero_text']};">
              <!--[if gte mso 9]>
              <v:rect xmlns:v="urn:schemas-microsoft-com:vml" fill="true" stroke="false" style="width:640px;">
                <v:fill type="frame" src="{mark}" color="{theme['hero']}" />
                <v:textbox style="mso-fit-shape-to-text:true" inset="0,0,0,0">
              <![endif]-->
              <div style="background:rgba(0,0,0,0.28);border-radius:14px;padding:18px 18px 16px;">
                <p style="margin:0 0 8px;font-family:Arial,Helvetica,sans-serif;font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:{theme['hero_text']};opacity:0.9;">HireLens</p>
                <h1 style="margin:0;font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:1.2;font-weight:500;color:{theme['hero_text']};">{COPY['title']}</h1>
              </div>
              <!--[if gte mso 9]>
                </v:textbox>
              </v:rect>
              <![endif]-->
            </td>
          </tr>
          <tr>
            <td style="padding:28px 32px 8px;">
              <p style="margin:0 0 14px;font-family:Georgia,'Times New Roman',serif;font-size:22px;line-height:1.35;color:{theme['ink']};">{COPY['lede']}</p>
              <p style="margin:0 0 18px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;color:{theme['muted']};">{COPY['intro']}</p>
              <span style="display:inline-block;padding:8px 12px;border-radius:999px;background:{theme['chip']};font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;color:{theme['ink']};">Part-Time · 10 hrs/week · Flexible</span>
            </td>
          </tr>
          <tr>
            <td style="padding:22px 32px;">
              <h2 style="margin:0 0 12px;font-family:Georgia,'Times New Roman',serif;font-size:20px;color:{theme['ink']};">{COPY['details_heading']}</h2>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid {theme['rule']};border-bottom:1px solid {theme['rule']};">
                {details}
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 32px 10px;">
              <h2 style="margin:0 0 12px;font-family:Georgia,'Times New Roman',serif;font-size:20px;color:{theme['ink']};">{COPY['team_heading']}</h2>
              <p style="margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:{theme['muted']};">{COPY['team_p1']}</p>
              <p style="margin:0 0 18px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:{theme['muted']};">{COPY['team_p2']}</p>
              <p style="margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;color:{theme['ink']};">{COPY['responsibilities_heading']}</p>
              {responsibilities}
              <p style="margin:8px 0 0;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:{theme['ink']};">{COPY['values']}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{theme['chip']};border-radius:14px;">
                <tr>
                  <td style="padding:18px 20px;">
                    <h2 style="margin:0 0 10px;font-family:Georgia,'Times New Roman',serif;font-size:18px;color:{theme['ink']};">{COPY['who_heading']}</h2>
                    <p style="margin:0 0 10px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:{theme['ink']};">{COPY['who_p1']}</p>
                    <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:{theme['muted']};">{COPY['who_p2']}</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:10px 32px 8px;">
              <h2 style="margin:0 0 12px;font-family:Georgia,'Times New Roman',serif;font-size:20px;color:{theme['ink']};">{COPY['apply_heading']}</h2>
              <p style="margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:{theme['muted']};">{COPY['apply_p1']}</p>
              <p style="margin:0 0 18px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:{theme['muted']};">{COPY['apply_p2']}</p>
              <p style="margin:0 0 22px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:{theme['ink']};">{COPY['closing']}</p>
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td bgcolor="{theme['accent']}" style="border-radius:12px;">
                    <a href="{cta_url}" style="display:inline-block;padding:14px 22px;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;color:{theme['accent_text']};text-decoration:none;">{COPY['cta']}</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 32px 28px;border-top:1px solid {theme['rule']};">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td valign="middle" style="padding:0 12px 0 0;">
                    <img src="{mark}" width="120" height="28" alt="HireLens" style="display:block;width:120px;max-width:48%;height:auto;border:0;outline:none;text-decoration:none;opacity:0.92;">
                  </td>
                  <td valign="middle" style="padding:0;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.5;color:{theme['muted']};">
                    {COPY['footer']}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def list_templates() -> list[dict[str, str]]:
    return [
        {
            "id": theme["id"],
            "name": theme["name"],
            "blurb": theme["blurb"],
            "subject": SUBJECT,
            "path": f"templates/hiring/{theme['id']}.html",
            "text_path": "templates/hiring/hiring.txt",
            "body_format": "html",
        }
        for theme in THEMES
    ]


def get_theme(template_id: str) -> dict[str, str] | None:
    for theme in THEMES:
        if theme["id"] == template_id:
            return theme
    return None


def get_template(template_id: str, *, apply_url: str | None = None) -> dict[str, str] | None:
    theme = get_theme(template_id)
    if theme is None:
        return None
    cta_url = resolve_apply_url(apply_url)
    html = render_template(theme, apply_url=cta_url, logo_src=LOGO_STATIC_PATH)
    text = render_plain_template(apply_url=cta_url)
    folder = hiring_dir()
    (folder / f"{theme['id']}.html").write_text(html, encoding="utf-8")
    (folder / "hiring.txt").write_text(text, encoding="utf-8")
    return {
        "id": theme["id"],
        "name": theme["name"],
        "blurb": theme["blurb"],
        "subject": SUBJECT,
        "body_format": "html",
        "apply_url": cta_url,
        "logo_src": LOGO_STATIC_PATH,
        "html": html,
        "text": text,
        "text_path": "templates/hiring/hiring.txt",
    }


def pick_random_template(
    *,
    apply_url: str | None = None,
    rng: random.Random | None = None,
) -> dict[str, str]:
    """Pick one hiring theme at random and return rendered HTML (no disk write)."""
    chooser = rng.choice if rng is not None else random.choice
    theme = chooser(THEMES)
    cta_url = resolve_apply_url(apply_url)
    logo_src = f"cid:{LOGO_CID}"
    text = render_plain_template(apply_url=cta_url)
    return {
        "id": theme["id"],
        "name": theme["name"],
        "blurb": theme["blurb"],
        "subject": SUBJECT,
        "body_format": "html",
        "apply_url": cta_url,
        "logo_src": logo_src,
        "html": render_template(theme, apply_url=cta_url, logo_src=logo_src),
        "text": text,
        "text_path": "templates/hiring/hiring.txt",
    }


def write_template_files(root: Path | None = None, *, apply_url: str | None = None) -> list[Path]:
    """Write all hiring HTML templates, the shared plain-text body, and a manifest."""
    root = root or project_root()
    folder = root / "templates" / "hiring"
    folder.mkdir(parents=True, exist_ok=True)
    cta_url = resolve_apply_url(apply_url)
    written: list[Path] = []
    for theme in THEMES:
        path = folder / f"{theme['id']}.html"
        path.write_text(
            render_template(theme, apply_url=cta_url, logo_src=LOGO_STATIC_PATH),
            encoding="utf-8",
        )
        written.append(path)
    text_path = folder / "hiring.txt"
    text_path.write_text(render_plain_template(apply_url=cta_url), encoding="utf-8")
    written.append(text_path)
    manifest = {
        "subject": SUBJECT,
        "apply_url": cta_url,
        "text_path": "templates/hiring/hiring.txt",
        "templates": list_templates(),
    }
    manifest_path = folder / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written.append(manifest_path)
    return written


if __name__ == "__main__":
    paths = write_template_files()
    print(f"Wrote {len(paths)} files")
    for path in paths:
        print(path)
