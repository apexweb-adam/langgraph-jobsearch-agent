"""Discovery node: fan out to enabled sources, dedupe, return all jobs.

Pure function: state["profile"] -> {"discovered": [Job, ...]}.
The downstream scoring node handles relevance; discovery is purely fetch + dedup.
"""
from __future__ import annotations

import asyncio

from ..state import GraphState, Job
from ..sources import greenhouse, lever, jazzhr, apify, jobspy_source


def _norm(s: str) -> str:
    """Normalize for fuzzy company+title matching: lowercase, collapse
    whitespace, strip common posting suffixes like "- Remote"."""
    import re
    s = (s or "").lower().strip()
    s = re.sub(r"\s*[-–|]\s*(remote|hybrid|us|usa|united states).*$", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _dedupe(jobs: list[Job]) -> list[Job]:
    """Two-level dedup. URL catches exact reposts; normalized company+title
    catches the same posting syndicated across boards (Indeed vs LinkedIn vs
    the company ATS all have different URLs for one job)."""
    seen_urls: set[str] = set()
    seen_ct: set[str] = set()
    out: list[Job] = []
    for j in jobs:
        url_key = (j.url or f"{j.source}:{j.source_id}").lower()
        ct_key = f"{_norm(j.company)}::{_norm(j.title)}"
        if url_key in seen_urls or (ct_key != "::" and ct_key in seen_ct):
            continue
        seen_urls.add(url_key)
        seen_ct.add(ct_key)
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
