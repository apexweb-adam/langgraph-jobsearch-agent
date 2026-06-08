"""Greenhouse public job board API connector.

Endpoint: https://boards-api.greenhouse.io/v1/boards/<board_token>/jobs
No auth required for public boards. Returns full job content when content=true.
"""
from __future__ import annotations

import re
import httpx

from ..state import Job


BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = TAG_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]


async def fetch_company(client: httpx.AsyncClient, board_token: str) -> list[Job]:
    """Fetch every active job for one company board."""
    url = BASE.format(token=board_token) + "?content=true"
    try:
        r = await client.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [greenhouse] {board_token}: fetch failed: {e}")
        return []

    out: list[Job] = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(
            Job(
                source="greenhouse",
                source_id=str(j.get("id")),
                company=board_token,
                title=j.get("title", "").strip(),
                location=loc,
                url=j.get("absolute_url", ""),
                description=_strip_html(j.get("content", "")),
                posted_at=j.get("updated_at"),
            )
        )
    return out


async def fetch_all(board_tokens: list[str]) -> list[Job]:
    if not board_tokens:
        return []
    async with httpx.AsyncClient(headers={"User-Agent": "berniesbaby-jobsearch/0.1"}) as c:
        results: list[Job] = []
        for token in board_tokens:
            results.extend(await fetch_company(c, token))
        return results
