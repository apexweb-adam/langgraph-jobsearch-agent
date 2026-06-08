"""JazzHR public job feed connector.

JazzHR exposes every customer's public board at:
  https://<subdomain>.applytojob.com/api/jobs?type=public&page=1

Returns JSON with up to 100 jobs per page. No auth required for public boards.
Some customers also expose:
  https://<subdomain>.applytojob.com/api/feed/?type=rss

We use the JSON endpoint because it parses cleaner.
"""
from __future__ import annotations

import re
import httpx

from ..state import Job


BASE = "https://{sub}.applytojob.com/api/jobs?type=public&page={page}"
TAG_RE = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    if not html:
        return ""
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", html)).strip()[:8000]


async def fetch_company(client: httpx.AsyncClient, subdomain: str) -> list[Job]:
    out: list[Job] = []
    for page in range(1, 6):  # cap at 500 jobs per company per run
        url = BASE.format(sub=subdomain, page=page)
        try:
            r = await client.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  [jazzhr] {subdomain} page {page}: {e}")
            break

        jobs = data.get("data") if isinstance(data, dict) else data
        if not isinstance(jobs, list) or not jobs:
            break

        for j in jobs:
            if not isinstance(j, dict):
                continue
            jid = str(j.get("id") or j.get("board_code") or "")
            if not jid:
                continue
            apply_url = (
                j.get("board_url")
                or f"https://{subdomain}.applytojob.com/apply/{j.get('board_code', jid)}"
            )
            out.append(
                Job(
                    source="jazzhr",
                    source_id=jid,
                    company=subdomain,
                    title=(j.get("title") or "").strip(),
                    location=", ".join(
                        x for x in [j.get("city"), j.get("state"), j.get("country")] if x
                    ),
                    url=apply_url,
                    description=_strip(j.get("description", "") or j.get("notes", "")),
                    posted_at=j.get("original_open_date") or j.get("modified_at"),
                )
            )

        if len(jobs) < 50:  # last page
            break
    return out


async def fetch_all(subdomains: list[str]) -> list[Job]:
    if not subdomains:
        return []
    async with httpx.AsyncClient(
        headers={"User-Agent": "langgraph-jobsearch-agent/0.1"}
    ) as c:
        results: list[Job] = []
        for s in subdomains:
            results.extend(await fetch_company(c, s))
        return results
