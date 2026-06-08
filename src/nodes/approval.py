"""Human-in-the-loop approval node.

Two modes, picked by env at runtime:

  DASHBOARD (default): upsert all scored jobs to Supabase. The dashboard at
  ./dashboard reads from Supabase via the anon key. No emails sent. The user
  reviews on their own schedule and updates user_decision from the dashboard.

  DIGEST (legacy): upsert to SQLite ledger and send a daily HTML email digest.
  Kept for portfolio + simpler self-hosted setups. Enable with
  APPROVAL_MODE=digest.

Both modes are idempotent: re-running the graph re-upserts the same rows
without duplicating.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

from jinja2 import Template

from ..state import GraphState
from ..storage import db
from ..storage.supabase import SupabaseStore


HTML_TPL = Template("""\
<!doctype html>
<html><body style="font-family:-apple-system,Helvetica Neue,sans-serif;color:#0f172a;
                   background:#f8fafc;padding:24px;max-width:720px;margin:auto;">
  <h2 style="margin:0 0 4px;">Job matches for {{ date }}</h2>
  <p style="color:#64748b;margin:0 0 18px;">
    {{ jobs|length }} role(s) at or above your score floor.
  </p>
  {% for j in jobs %}
  <div style="background:white;border:1px solid #e2e8f0;border-radius:8px;
              padding:14px 16px;margin-bottom:10px;">
    <div style="display:flex;justify-content:space-between;align-items:baseline;">
      <a href="{{ j.url }}" style="color:#0f172a;text-decoration:none;font-weight:600;
                                   font-size:15px;">{{ j.title }}</a>
      <span style="background:{{ '#16a34a' if j.score >= 80 else '#0891b2' if j.score >= 70 else '#6b7280' }};
                   color:white;font-size:12px;padding:2px 8px;border-radius:10px;">
        {{ j.score }}/100
      </span>
    </div>
    <div style="color:#475569;font-size:13px;margin-top:2px;">
      {{ j.company }}{% if j.location %} . {{ j.location }}{% endif %}
    </div>
    <div style="font-size:13px;margin-top:8px;color:#334155;">{{ j.reasoning }}</div>
  </div>
  {% endfor %}
</body></html>
""")


def _approval_mode() -> str:
    return os.environ.get("APPROVAL_MODE", "dashboard").lower()


def _send_email(html: str, subject: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER")
    pw   = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM", user or "")
    to_addr = os.environ.get("DIGEST_TO")

    if not all([host, user, pw, to_addr]):
        print("  [approval] SMTP not configured, printing digest preview instead")
        print(html[:1500])
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content("Your email client does not support HTML.")
    msg.add_alternative(html, subtype="html")
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as smtp:
            smtp.login(user, pw)
            smtp.send_message(msg)
        print(f"  [approval] digest sent to {to_addr}")
        return True
    except Exception as e:
        print(f"  [approval] send failed: {e}")
        return False


def _dashboard_mode(state: GraphState) -> GraphState:
    """Default. Upsert to Supabase. Dashboard reads from there."""
    scored = state.get("scored", [])
    if not scored:
        print("  [approval] no scored jobs to write")
        return {"digest_sent": False}

    store = SupabaseStore()
    if not store.enabled:
        print("  [approval] SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set.")
        print("            Scored jobs not persisted. Set them to enable the dashboard,")
        print("            or set APPROVAL_MODE=digest to use email instead.")
        return {"digest_sent": False}

    n = store.upsert(scored)
    print(f"  [approval] supabase upsert: {n} rows (dashboard now has them)")
    return {"digest_sent": False, "rows_upserted": n}


def _digest_mode(state: GraphState) -> GraphState:
    """Legacy email path. SQLite-backed."""
    db_path = os.environ.get("DB_PATH", "./data/jobs.db")
    top_n = int(os.environ.get("DIGEST_TOP_N", "15"))
    min_score = int(os.environ.get("DIGEST_MIN_SCORE", "60"))

    db.init(db_path)
    scored = state.get("scored", [])
    if scored:
        inserted, updated = db.upsert_scored(db_path, scored)
        print(f"  [approval] db: +{inserted} new, ~{updated} updated")

    pending = db.pending_for_digest(db_path, top_n=top_n, min_score=min_score)
    if not pending:
        print("  [approval] nothing above score floor, no digest sent")
        return {"digest_sent": False}

    today = datetime.now(timezone.utc).strftime("%a %b %d")
    html = HTML_TPL.render(date=today, jobs=pending)
    sent = _send_email(html, subject=f"Job matches, {today} ({len(pending)})")
    if sent:
        db.mark_digested(db_path, [j["url"] for j in pending])
    return {"digest_sent": sent}


def approval_node(state: GraphState) -> GraphState:
    mode = _approval_mode()
    if mode == "digest":
        return _digest_mode(state)
    return _dashboard_mode(state)
