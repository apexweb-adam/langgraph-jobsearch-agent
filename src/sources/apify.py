"""Apify-backed scraper source for ATSes that block native HTTP clients.

Any JS-rendered ATS (Paylocity, ADP Workforce Now, iCIMS, sometimes BambooHR)
won't return data to plain httpx. Same problem for Indeed (Cloudflare + bot
detection).

Strategy: outsource the rendering to an Apify Actor and normalize the result
into our Job model. The user picks an Actor from the Apify Store and provides
its run-input via profile.yaml. We just submit the run and read the dataset.

Required env: APIFY_TOKEN
Profile shape (per source):
  apify_sources:
    - actor_id: "misceres/indeed-scraper"
      label:    "indeed-program-director"
      input:
        position: "Program Director"
        country:  "US"
        maxItems: 50
      mapping:
        title:        "positionName"
        company:      "company"
        location:     "location"
        url:          "url"
        description:  "description"
"""
from __future__ import annotations

import os
import httpx

from ..state import Job


APIFY_BASE = "https://api.apify.com/v2"


def _get(obj: dict, dotted: str, default: str = "") -> str:
    """Pull a value out of a nested dict by dotted path."""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    if cur is None:
        return default
    return str(cur)


async def run_actor(
    client: httpx.AsyncClient,
    token: str,
    actor_id: str,
    actor_input: dict,
    poll_timeout_s: int = 900,    # hard cap: don't wait more than 15 min per actor
    poll_interval_s: int = 5,
) -> list[dict]:
    """Start the Actor asynchronously, poll until terminal state, then read
    the run's default dataset.

    Why not run-sync-get-dataset-items: that endpoint has a 5-minute hard
    ceiling. Indeed scraping with detail-page fetching for ~30-50 items
    routinely exceeds 5 minutes (residential proxy + headless Chromium is
    slow). Async + poll has no time limit beyond what we set here.
    """
    actor_path = actor_id.replace("/", "~")
    headers = {"Authorization": f"Bearer {token}"}

    # 1) start the run
    try:
        r = await client.post(
            f"{APIFY_BASE}/acts/{actor_path}/runs",
            headers=headers, json=actor_input, timeout=30,
        )
        r.raise_for_status()
        run = r.json().get("data", {})
        run_id = run.get("id")
        if not run_id:
            print(f"  [apify] {actor_id}: start succeeded but no runId in response")
            return []
    except Exception as e:
        print(f"  [apify] {actor_id}: start failed: {e}")
        return []

    # 2) poll until terminal state
    import asyncio
    elapsed = 0
    final_status = None
    while elapsed < poll_timeout_s:
        try:
            r = await client.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                headers=headers, timeout=15,
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            status = data.get("status")
            if status in ("SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"):
                final_status = status
                break
        except Exception as e:
            print(f"  [apify] {actor_id}: poll error: {e} (continuing)")
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s

    if final_status != "SUCCEEDED":
        print(f"  [apify] {actor_id}: run {run_id} ended with {final_status or 'TIMEOUT_WAITING'}")
        # Even on partial-success runs the dataset may have rows; try to fetch.

    # 3) fetch the run's default dataset
    try:
        r = await client.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            headers=headers, params={"format": "json", "clean": "true"}, timeout=60,
        )
        r.raise_for_status()
        items = r.json()
        if isinstance(items, list):
            return items
    except Exception as e:
        print(f"  [apify] {actor_id}: dataset fetch failed: {e}")
    return []


def _normalize(item: dict, mapping: dict, source_label: str) -> Job | None:
    """Map a raw Apify item to our Job model via the user's mapping config."""
    title = _get(item, mapping.get("title", "title"))
    if not title:
        return None
    url = _get(item, mapping.get("url", "url"))
    if not url:
        return None
    # Build a stable source_id even if the actor doesn't surface one
    sid = _get(item, mapping.get("id", "id")) or url
    return Job(
        source=f"apify:{source_label}",
        source_id=sid,
        company=_get(item, mapping.get("company", "company")) or source_label,
        title=title.strip(),
        location=_get(item, mapping.get("location", "location")),
        url=url,
        description=_get(item, mapping.get("description", "description"))[:8000],
        posted_at=_get(item, mapping.get("posted_at", "postedAt")) or None,
    )


async def fetch_all(apify_sources: list[dict]) -> list[Job]:
    if not apify_sources:
        return []
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("  [apify] APIFY_TOKEN not set, skipping all Apify sources")
        return []

    # Run actors in parallel instead of sequentially. With 4 actors and a
    # 15-minute per-run poll ceiling, sequential execution can hit 30+ minutes
    # wall time and fall over on the slower runs. asyncio.gather keeps total
    # wall time at the SLOWEST single actor instead of the sum.
    import asyncio

    async with httpx.AsyncClient(
        headers={"User-Agent": "langgraph-jobsearch-agent/0.1"}
    ) as c:

        async def _one(src: dict) -> list[Job]:
            actor_id = src.get("actor_id")
            label = src.get("label", actor_id or "unknown")
            actor_input = src.get("input") or {}
            mapping = src.get("mapping") or {}
            if not actor_id:
                return []
            print(f"  [apify] starting {actor_id} as '{label}'")
            items = await run_actor(c, token, actor_id, actor_input)
            print(f"  [apify] {label}: {len(items)} raw items")
            out: list[Job] = []
            for it in items:
                j = _normalize(it, mapping, label)
                if j:
                    out.append(j)
            return out

        groups = await asyncio.gather(
            *[_one(src) for src in apify_sources],
            return_exceptions=True,
        )
        results: list[Job] = []
        for g in groups:
            if isinstance(g, Exception):
                print(f"  [apify] actor crashed: {g}")
                continue
            results.extend(g)
        return results
