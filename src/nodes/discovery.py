"""Discovery node: fan out to enabled sources, dedupe, return all jobs.

Pure function: state["profile"] -> {"discovered": [Job, ...]}.
The downstream scoring node handles relevance; discovery is purely fetch + dedup.
"""
from __future__ import annotations

import asyncio

from ..state import GraphState, Job
from ..sources import greenhouse, lever


def _dedupe(jobs: list[Job]) -> list[Job]:
    seen: set[str] = set()
    out: list[Job] = []
    for j in jobs:
        key = (j.url or f"{j.source}:{j.source_id}").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


async def _fetch_all(profile) -> list[Job]:
    gh_task = greenhouse.fetch_all(profile.target_companies.greenhouse)
    lv_task = lever.fetch_all(profile.target_companies.lever)
    gh_jobs, lv_jobs = await asyncio.gather(gh_task, lv_task)
    return gh_jobs + lv_jobs


def discovery_node(state: GraphState) -> GraphState:
    profile = state["profile"]
    print(
        f"  [discovery] greenhouse={len(profile.target_companies.greenhouse)} "
        f"lever={len(profile.target_companies.lever)} boards"
    )
    jobs = asyncio.run(_fetch_all(profile))
    deduped = _dedupe(jobs)
    print(f"  [discovery] {len(jobs)} raw -> {len(deduped)} deduped")
    return {"discovered": deduped}
