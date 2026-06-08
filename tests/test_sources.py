"""Smoke tests against live public Greenhouse + Lever boards.

These hit real APIs so they're slow-ish (~3s) and depend on the boards being up.
They're worth keeping because they catch schema drift (Greenhouse renaming a
field, Lever returning a wrapper object, etc.) before the scoring node does.

Run with:  uv run --with httpx --with pydantic pytest tests/ -v
"""
from __future__ import annotations

import asyncio

from src.sources import greenhouse, lever


def test_greenhouse_fetches_real_board():
    # Anthropic publishes its board publicly. If they ever rename it, swap to
    # any other known board (vercel, supabase, replicate are stable picks).
    jobs = asyncio.run(greenhouse.fetch_all(["anthropic"]))
    assert len(jobs) > 0, "Anthropic should always have open roles"
    j = jobs[0]
    assert j.source == "greenhouse"
    assert j.title
    assert j.url.startswith("https://job-boards")  or j.url.startswith("https://boards")
    assert j.source_id


def test_lever_fetches_real_board():
    # Try a list of known public Lever customers. As long as ONE responds with
    # postings the connector is healthy. This guards against any single company
    # migrating ATSes.
    candidates = ["palantir", "wealthsimple", "faire", "matterport", "samsara"]
    for slug in candidates:
        jobs = asyncio.run(lever.fetch_all([slug]))
        if jobs:
            j = jobs[0]
            assert j.source == "lever"
            assert j.title
            assert "lever.co" in j.url or "jobs." in j.url
            return
    raise AssertionError(f"none of {candidates} returned Lever postings")


def test_unknown_board_returns_empty_not_crash():
    """We must NEVER crash the graph because one company removed its board."""
    jobs = asyncio.run(greenhouse.fetch_all(["this-company-does-not-exist-9z9z9z"]))
    assert jobs == []

    jobs2 = asyncio.run(lever.fetch_all(["this-company-does-not-exist-9z9z9z"]))
    assert jobs2 == []
