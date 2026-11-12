"""Read MX, SPF, DKIM, and DMARC for the sending domain."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    status: str
    label: str
    detail: str

    @property
    def blocking(self) -> bool:
        return self.status == "missing"


def interpret(
    mx: str,
    spf: str,
    dkim_private: str,
    dkim_default: str,
    dmarc: str,
) -> list[Finding]:
    findings: list[Finding] = []
    mx_text = mx.lower()
    has_mx = "mx1.privateemail.com" in mx_text and "mx2.privateemail.com" in mx_text
    findings.append(
        Finding(
            "ok" if has_mx else "missing",
            "MX",
            "mx1.privateemail.com and mx2.privateemail.com are present."
            if has_mx
            else "Add MX 10 mx1.privateemail.com and MX 10 mx2.privateemail.com.",
        )
    )

    has_spf = "spf.privateemail.com" in spf.lower()
    findings.append(
        Finding(
            "ok" if has_spf else "missing",
            "SPF",
            "SPF includes spf.privateemail.com."
            if has_spf
            else "Add one TXT record: v=spf1 include:spf.privateemail.com ~all. Merge it into an existing SPF record instead of adding a second one.",
        )
    )

    private_ok = "v=dkim1" in dkim_private.lower()
    default_ok = "v=dkim1" in dkim_default.lower()
    if private_ok or default_ok:
        selector = "privateemail._domainkey" if private_ok else "default._domainkey"
        findings.append(Finding("ok", "DKIM", f"Found a DKIM key at {selector}."))
    else:
        findings.append(
            Finding(
                "missing",
                "DKIM",
                "Generate DKIM in the Private Email panel. Subscriptions from 2 June 2026 use host privateemail._domainkey. Older ones use default._domainkey. DKIM is what aligns mail with your domain.",
            )
        )

    has_dmarc = "v=dmarc1" in dmarc.lower()
    findings.append(
        Finding(
            "ok" if has_dmarc else "warn",
            "DMARC",
            "A DMARC record is present."
            if has_dmarc
            else "Recommended: a TXT record at _dmarc, for example v=DMARC1; p=none; rua=mailto:you@your-domain.",
        )
    )
    return findings


def lookup(kind: str, name: str) -> str:
    try:
        completed = subprocess.run(
            ["nslookup", f"-type={kind}", name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"lookup failed: {exc}"
    return f"{completed.stdout}\n{completed.stderr}"


def check_domain(domain: str) -> list[Finding]:
    return interpret(
        lookup("MX", domain),
        lookup("TXT", domain),
        lookup("TXT", f"privateemail._domainkey.{domain}"),
        lookup("TXT", f"default._domainkey.{domain}"),
        lookup("TXT", f"_dmarc.{domain}"),
    )


def format_findings(findings: list[Finding]) -> str:
    lines = []
    for finding in findings:
        lines.append(f"{finding.status.upper():7} {finding.label}: {finding.detail}")
    return "\n".join(lines)
