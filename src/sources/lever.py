"""Lever public job board API connector.

Endpoint: https://api.lever.co/v0/postings/<company_slug>?mode=json
No auth required for public boards.
"""
from __future__ import annotations

import re
import httpx

from ..state import Job


BASE = "https://api.lever.co/v0/postings/{company}"
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = TAG_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]


async def fetch_company(client: httpx.AsyncClient, slug: str) -> list[Job]:
    url = BASE.format(company=slug) + "?mode=json"
    try:
        r = await client.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [lever] {slug}: fetch failed: {e}")
        return []

    if not isinstance(data, list):
        return []

    out: list[Job] = []
    for p in data:
        cats = p.get("categories") or {}
        loc = cats.get("location") or ""
        # Build description from descriptionPlain or lists + description
        desc_parts = [p.get("descriptionPlain") or _strip_html(p.get("description") or "")]
        for section in p.get("lists") or []:
            text = section.get("text") or ""
            content = _strip_html(section.get("content") or "")
            desc_parts.append(f"{text}: {content}")
        desc = " | ".join(d for d in desc_parts if d)[:8000]

        out.append(
            Job(
                source="lever",
                source_id=p.get("id", ""),
                company=slug,
                title=(p.get("text") or "").strip(),
                location=loc,
                url=p.get("hostedUrl", ""),
                description=desc,
                posted_at=str(p.get("createdAt", "")) or None,
            )
        )
    return out


async def fetch_all(slugs: list[str]) -> list[Job]:
    if not slugs:
        return []
    async with httpx.AsyncClient(headers={"User-Agent": "langgraph-jobsearch-agent/0.1"}) as c:
        results: list[Job] = []
        for slug in slugs:
            results.extend(await fetch_company(c, slug))
        return results
