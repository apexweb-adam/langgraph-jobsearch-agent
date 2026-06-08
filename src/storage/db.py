"""SQLite ledger of seen jobs and approval state.

Why SQLite: zero external deps, works on the client's laptop or any server.
Phase 2 can swap to Postgres without touching the graph nodes.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..state import ScoredJob


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    canonical_url TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    description TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    fit_reasoning TEXT,
    strengths TEXT,
    gaps TEXT,
    hard_rejected INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    digest_sent_at TEXT,
    user_decision TEXT
        CHECK (user_decision IS NULL OR user_decision IN ('approved', 'rejected', 'snoozed'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_digest ON jobs(digest_sent_at);
"""


@contextmanager
def conn(db_path: str) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db_path, isolation_level=None, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
    finally:
        c.close()


def init(db_path: str) -> None:
    with conn(db_path) as c:
        c.executescript(SCHEMA)


def upsert_scored(db_path: str, scored: list[ScoredJob]) -> tuple[int, int]:
    """Insert new, update existing. Returns (inserted, updated)."""
    inserted = 0
    updated = 0
    with conn(db_path) as c:
        for s in scored:
            cur = c.execute(
                "SELECT canonical_url FROM jobs WHERE canonical_url=?",
                (s.job.url,),
            ).fetchone()
            if cur:
                c.execute(
                    "UPDATE jobs SET score=?, fit_reasoning=?, strengths=?, gaps=?, "
                    "hard_rejected=?, last_scored_at=CURRENT_TIMESTAMP "
                    "WHERE canonical_url=?",
                    (
                        s.score, s.fit_reasoning,
                        "|".join(s.strengths), "|".join(s.gaps),
                        1 if s.hard_rejected else 0, s.job.url,
                    ),
                )
                updated += 1
            else:
                c.execute(
                    "INSERT INTO jobs "
                    "(canonical_url, source, source_id, company, title, location, "
                    " description, score, fit_reasoning, strengths, gaps, hard_rejected) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        s.job.url, s.job.source, s.job.source_id, s.job.company,
                        s.job.title, s.job.location, s.job.description,
                        s.score, s.fit_reasoning,
                        "|".join(s.strengths), "|".join(s.gaps),
                        1 if s.hard_rejected else 0,
                    ),
                )
                inserted += 1
    return inserted, updated


def pending_for_digest(db_path: str, top_n: int = 15, min_score: int = 60) -> list[dict]:
    """Top-N jobs that haven't been digested yet AND aren't hard-rejected."""
    with conn(db_path) as c:
        rows = c.execute(
            "SELECT canonical_url, company, title, location, score, fit_reasoning, "
            "       strengths, gaps "
            "FROM jobs "
            "WHERE digest_sent_at IS NULL "
            "  AND hard_rejected=0 "
            "  AND score >= ? "
            "  AND user_decision IS NULL "
            "ORDER BY score DESC "
            "LIMIT ?",
            (min_score, top_n),
        ).fetchall()
    return [
        {
            "url": r[0], "company": r[1], "title": r[2], "location": r[3],
            "score": r[4], "reasoning": r[5],
            "strengths": (r[6] or "").split("|") if r[6] else [],
            "gaps": (r[7] or "").split("|") if r[7] else [],
        }
        for r in rows
    ]


def mark_digested(db_path: str, urls: list[str]) -> None:
    if not urls:
        return
    with conn(db_path) as c:
        c.executemany(
            "UPDATE jobs SET digest_sent_at=CURRENT_TIMESTAMP WHERE canonical_url=?",
            [(u,) for u in urls],
        )
