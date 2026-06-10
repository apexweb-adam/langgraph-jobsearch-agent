"""Email alerts when newly-scored Great-fit roles land in Supabase.

The candidate explicitly said "no email digest" during intake, so this is
NOT a daily digest — it only fires when at least one role with score >=80
is *newly seen* this run. That keeps her inbox empty on slow days while
still pinging her the moment a hot match shows up.

Provider: Resend. Cheapest provider with a clean REST API; the env var
RESEND_API_KEY is the only setup needed. Falls back silently to no-op if
RESEND_API_KEY or DIAMOND_NOTIFY_EMAIL aren't set, so the pipeline never
breaks when alerts are off.

Idempotency: we rely on the `digest_sent_at` column on each row. If a row
already has digest_sent_at set, we don't notify again for it. After a
successful send we stamp all alerted rows.
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone

import httpx

from ..state import ScoredJob


RESEND_ENDPOINT = "https://api.resend.com/emails"


def _format_html(jobs: list[ScoredJob], dashboard_url: str) -> str:
    rows = []
    for j in jobs[:10]:
        sal = ""
        if j.job.salary_text:
            sal = f" <em>({j.job.salary_text})</em>"
        rows.append(
            f"<li><strong>{j.score}/100</strong> "
            f"<a href=\"{j.job.url}\">{j.job.title}</a> at "
            f"<strong>{j.job.company}</strong>{sal}<br>"
            f"<small>{j.fit_reasoning}</small></li>"
        )
    listing = "\n".join(rows)
    return f"""
<!doctype html>
<html><body style="font-family: -apple-system, sans-serif; max-width: 640px; margin: 24px auto; color: #0b1220;">
  <h2 style="margin-bottom: 4px;">New strong-fit roles</h2>
  <p style="color: #64748b; margin-top: 0;">
    {len(jobs)} new role{'s' if len(jobs) != 1 else ''} scored 80 or higher
    against your profile.
  </p>
  <ol>{listing}</ol>
  <p style="margin-top: 24px;">
    <a href="{dashboard_url}"
       style="background: #4338ca; color: white; padding: 10px 20px;
              border-radius: 6px; text-decoration: none;">
      Open the dashboard
    </a>
  </p>
  <p style="color: #94a3b8; font-size: 12px; margin-top: 32px;">
    You'll only receive this when at least one new Great-fit role arrives.
    No daily digest.
  </p>
</body></html>
"""


def notify_great_fits(scored: list[ScoredJob], already_notified_urls: set[str]) -> list[str]:
    """Send the alert email if there are newly-scored Great-fit roles.

    Returns the list of URLs that were alerted on (caller stamps them as
    digest_sent_at so we don't double-notify).
    """
    api_key = os.environ.get("RESEND_API_KEY")
    to_email = os.environ.get("DIAMOND_NOTIFY_EMAIL")
    if not api_key or not to_email:
        return []

    new_great = [
        s for s in scored
        if s.score >= 80 and not s.hard_rejected
        and s.job.url not in already_notified_urls
    ]
    if not new_great:
        return []

    dashboard_url = os.environ.get(
        "DASHBOARD_URL",
        "https://langgraph-jobsearch-dashboard.vercel.app",
    )
    from_addr = os.environ.get(
        "RESEND_FROM",
        "Job Pipeline <onboarding@resend.dev>",
    )

    html = _format_html(new_great, dashboard_url)
    subject = (
        f"{len(new_great)} new {'role' if len(new_great)==1 else 'roles'} "
        f"scored 80+ today"
    )

    try:
        r = httpx.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            content=json.dumps({
                "from": from_addr,
                "to": [to_email],
                "subject": subject,
                "html": html,
            }),
            timeout=15,
        )
        if r.status_code >= 300:
            print(f"  [email] alert failed: {r.status_code} {r.text[:200]}")
            return []
        print(f"  [email] alerted on {len(new_great)} Great-fit roles to {to_email}")
        return [s.job.url for s in new_great]
    except Exception as e:
        print(f"  [email] alert error: {e}")
        return []
