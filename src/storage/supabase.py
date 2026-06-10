"""Supabase storage adapter. Writes scored jobs to a `jobs` table so the
dashboard can read them.

Why Supabase and not just SQLite: the dashboard needs to be hosted (Vercel
free tier), and Vercel serverless functions can't share a SQLite file. Postgres
in Supabase free tier covers this use case with zero setup beyond the SQL
migration in setup_sql() below.

Required env:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY    # service role so the runner can write; the
                                # dashboard uses the anon key with RLS for read
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone

import httpx

from ..state import ScoredJob


SETUP_SQL = """
-- Apply this once (Supabase SQL editor) to create the table the runner writes
-- and the dashboard reads. Keep both keys around: service_role for the runner,
-- anon for the dashboard.
create table if not exists public.jobs (
    canonical_url text primary key,
    source text not null,
    source_id text not null,
    company text not null,
    title text not null,
    location text,
    description text,
    score int not null default 0,
    fit_reasoning text,
    strengths text[] default '{}',
    gaps text[] default '{}',
    hard_rejected bool not null default false,
    hard_rejected_reason text,
    user_decision text check (user_decision in ('applied','snoozed','rejected') or user_decision is null),
    first_seen_at timestamptz not null default now(),
    last_scored_at timestamptz not null default now(),
    digest_sent_at timestamptz
);
create index if not exists idx_jobs_score on public.jobs (score desc);
create index if not exists idx_jobs_decision on public.jobs (user_decision);

-- Read-only RLS so the dashboard with the anon key can list, and only the
-- service role can write. The dashboard updates user_decision through a
-- Supabase RPC or a server-side route, never directly with the anon key.
alter table public.jobs enable row level security;
create policy "anon can read" on public.jobs for select using (true);
"""


class SupabaseStore:
    """Minimal write-only adapter. Read-side is the dashboard."""

    def __init__(self):
        self.url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.key = os.environ.get(
            "SUPABASE_SERVICE_ROLE_KEY",
            os.environ.get("SUPABASE_KEY", ""),
        )
        # Table name is configurable so the same code can target a shared DB
        # (default "langgraph_jobs") or a dedicated client DB (default "jobs").
        self.table = os.environ.get("SUPABASE_TABLE", "langgraph_jobs")
        self.enabled = bool(self.url and self.key)
        if self.enabled:
            self._client = httpx.Client(
                base_url=f"{self.url}/rest/v1",
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                timeout=30,
            )

    def upsert(self, scored: list[ScoredJob]) -> int:
        if not self.enabled or not scored:
            return 0
        rows = [
            {
                "canonical_url": s.job.url,
                "source": s.job.source,
                "source_id": s.job.source_id,
                "company": s.job.company,
                "title": s.job.title,
                "location": s.job.location,
                "description": (s.job.description or "")[:8000],
                "score": int(s.score),
                "fit_reasoning": s.fit_reasoning,
                "strengths": s.strengths,
                "gaps": s.gaps,
                "hard_rejected": bool(s.hard_rejected),
                "hard_rejected_reason": s.hard_rejected_reason or None,
                "salary_text": s.job.salary_text or None,
                "salary_min": s.job.salary_min,
                "salary_max": s.job.salary_max,
                "last_scored_at": datetime.now(timezone.utc).isoformat(),
            }
            for s in scored
        ]
        try:
            r = self._client.post(
                f"/{self.table}", content=json.dumps(rows),
                params={"on_conflict": "canonical_url"},
            )
            r.raise_for_status()
            return len(rows)
        except Exception as e:
            print(f"  [supabase] upsert failed: {e}")
            return 0

    def get_digest_sent_urls(self) -> set[str]:
        """URLs that have already received an alert email.

        Used to avoid double-notifying on every re-scoring pass.
        """
        if not self.enabled:
            return set()
        try:
            r = self._client.get(
                f"/{self.table}",
                params={
                    "select": "canonical_url",
                    "digest_sent_at": "not.is.null",
                },
            )
            r.raise_for_status()
            return {row["canonical_url"] for row in r.json()}
        except Exception as e:
            print(f"  [supabase] get_digest_sent_urls failed: {e}")
            return set()

    def mark_digest_sent(self, urls: list[str]) -> int:
        """Stamp digest_sent_at on the supplied URLs."""
        if not self.enabled or not urls:
            return 0
        stamp = datetime.now(timezone.utc).isoformat()
        n = 0
        try:
            # PostgREST supports `in.(...)` filters but quoting URLs with
            # commas/special chars is annoying; one PATCH per URL is fine
            # at this scale (we alert on a handful of rows per run).
            for url in urls:
                r = self._client.patch(
                    f"/{self.table}",
                    content=json.dumps({"digest_sent_at": stamp}),
                    params={"canonical_url": f"eq.{url}"},
                )
                r.raise_for_status()
                n += 1
            return n
        except Exception as e:
            print(f"  [supabase] mark_digest_sent failed: {e}")
            return n
