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
    timeout_s: int = 240,
) -> list[dict]:
    """Start the Actor in run-sync mode (blocks until it finishes or times out)
    and return the dataset items.

    We use the run-sync-get-dataset-items endpoint so we don't have to poll.
    Apify caps run-sync at 5 minutes which is fine for our use case.
    """
    actor_path = actor_id.replace("/", "~")
    url = f"{APIFY_BASE}/acts/{actor_path}/run-sync-get-dataset-items"
    params = {"token": token, "timeout": timeout_s, "format": "json"}
    try:
        r = await client.post(url, params=params, json=actor_input, timeout=timeout_s + 30)
        r.raise_for_status()
        items = r.json()
        if isinstance(items, list):
            return items
    except Exception as e:
        print(f"  [apify] actor {actor_id} failed: {e}")
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

    async with httpx.AsyncClient(
        headers={"User-Agent": "langgraph-jobsearch-agent/0.1"}
    ) as c:
        results: list[Job] = []
        for src in apify_sources:
            actor_id = src.get("actor_id")
            label = src.get("label", actor_id or "unknown")
            actor_input = src.get("input") or {}
            mapping = src.get("mapping") or {}
            if not actor_id:
                continue
            print(f"  [apify] running {actor_id} as '{label}'")
            items = await run_actor(c, token, actor_id, actor_input)
            print(f"  [apify] {label}: {len(items)} raw items")
            for it in items:
                j = _normalize(it, mapping, label)
                if j:
                    results.append(j)
        return results
