"""Free multi-board scraper via python-jobspy (Indeed + LinkedIn + Google Jobs).

Why this exists: the Apify Indeed actor costs residential-proxy credits on
every run. JobSpy hits Indeed's mobile API, LinkedIn's guest endpoints, and
Google Jobs directly from the runner's IP for free. It's been running in the
chris_job_tracker project on a 12-hour cron for weeks without blocks, so the
pattern is proven on GitHub-hosted runners.

Division of labor:
  - JobSpy: broad, free discovery on every run (the volume engine).
  - Apify actor: once-daily supplement with full JD detail pages (the
    quality engine). Both feed the same dedup + scoring pipeline.

Config in profile.yaml:
  jobspy_searches:
    - "National Program Director nonprofit"
    - "Director of Volunteer Engagement"
  jobspy_sites: [indeed, linkedin, google]   # optional, this is the default
  jobspy_results_per_search: 20              # optional
  jobspy_hours_old: 336                      # optional, default 14 days
"""
from __future__ import annotations

import asyncio

from ..state import Job


def _scrape_one(keyword: str, sites: list[str], wanted: int, hours_old: int) -> list[Job]:
    """Blocking JobSpy call for one search term. Runs in a thread."""
    from jobspy import scrape_jobs  # import here: heavy pandas dependency

    try:
        df = scrape_jobs(
            site_name=sites,
            search_term=keyword,
            # google_search_term drives the Google Jobs tab query; without it
            # the google site silently returns nothing.
            google_search_term=f"{keyword} remote united states",
            location="United States",
            is_remote=True,
            results_wanted=wanted,
            hours_old=hours_old,
            country_indeed="USA",
            linkedin_fetch_description=True,
            verbose=0,
        )
    except Exception as e:
        print(f"  [jobspy] '{keyword}': scrape failed: {e}")
        return []

    if df is None or df.empty:
        return []

    out: list[Job] = []
    for _, row in df.iterrows():
        title = str(row.get("title") or "").strip()
        company = str(row.get("company") or "").strip()
        url = str(row.get("job_url") or "").strip()
        if not title or not company or not url:
            continue
        site = str(row.get("site") or "jobspy")
        desc = str(row.get("description") or "")
        if desc in ("nan", "None"):
            desc = ""
        location = str(row.get("location") or "")
        if location in ("nan", "None"):
            location = ""
        posted = str(row.get("date_posted") or "")
        if posted in ("nan", "None", "NaT"):
            posted = ""
        out.append(Job(
            source=f"jobspy:{site}",
            source_id=str(row.get("id") or url),
            company=company,
            title=title,
            location=location,
            url=url,
            description=desc[:8000],
            posted_at=posted[:10] or None,
        ))
    return out


async def fetch_all(
    searches: list[str],
    sites: list[str] | None = None,
    wanted: int = 20,
    hours_old: int = 336,
) -> list[Job]:
    if not searches:
        return []
    sites = sites or ["indeed", "linkedin", "google"]

    # JobSpy is synchronous (requests + pandas), so push each search term
    # onto a thread. Searches run concurrently; each one internally hits its
    # sites sequentially, which keeps per-site request rates polite.
    results = await asyncio.gather(
        *[asyncio.to_thread(_scrape_one, kw, sites, wanted, hours_old) for kw in searches],
        return_exceptions=True,
    )
    out: list[Job] = []
    for kw, r in zip(searches, results):
        if isinstance(r, Exception):
            print(f"  [jobspy] '{kw}' crashed: {r}")
            continue
        print(f"  [jobspy] '{kw}': {len(r)} jobs")
        out.extend(r)
    return out
