"""Discovery node: fan out to enabled sources, dedupe, return all jobs.

Pure function: state["profile"] -> {"discovered": [Job, ...]}.
The downstream scoring node handles relevance; discovery is purely fetch + dedup.
"""
from __future__ import annotations

import asyncio

from ..state import GraphState, Job
from ..sources import greenhouse, lever, jazzhr, apify, jobspy_source


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
    tc = profile.target_companies
    # Each source is independent: fan out concurrently, await all.
    apify_inputs = [s.model_dump() for s in tc.apify]
    results = await asyncio.gather(
        greenhouse.fetch_all(tc.greenhouse),
        lever.fetch_all(tc.lever),
        jazzhr.fetch_all(tc.jazzhr),
        apify.fetch_all(apify_inputs),
        jobspy_source.fetch_all(
            profile.jobspy_searches,
            sites=profile.jobspy_sites,
            wanted=profile.jobspy_results_per_search,
            hours_old=profile.jobspy_hours_old,
        ),
        return_exceptions=True,
    )
    out: list[Job] = []
    for name, r in zip(("greenhouse", "lever", "jazzhr", "apify", "jobspy"), results):
        if isinstance(r, Exception):
            print(f"  [discovery] {name} crashed: {r}")
            continue
        print(f"  [discovery] {name}: {len(r)} jobs")
        out.extend(r)
    return out


def discovery_node(state: GraphState) -> GraphState:
    profile = state["profile"]
    tc = profile.target_companies
    print(
        f"  [discovery] greenhouse={len(tc.greenhouse)} lever={len(tc.lever)} "
        f"jazzhr={len(tc.jazzhr)} apify={len(tc.apify)} "
        f"jobspy_searches={len(profile.jobspy_searches)}"
    )
    jobs = asyncio.run(_fetch_all(profile))
    deduped = _dedupe(jobs)
    print(f"  [discovery] {len(jobs)} raw -> {len(deduped)} deduped")
    return {"discovered": deduped}
