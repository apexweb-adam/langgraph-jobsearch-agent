"""LangGraph state shape shared across all nodes.

The state is a TypedDict that LangGraph mutates as it walks the graph.
Each node is a pure function: state -> partial state update.
"""
from __future__ import annotations

from typing import TypedDict
from pydantic import BaseModel, Field

from .profile.schema import Profile


class Job(BaseModel):
    """Normalized job posting across all source ATSes."""
    source: str                 # "greenhouse" | "lever"
    source_id: str              # unique within source
    company: str
    title: str
    location: str = ""
    url: str
    description: str = ""
    posted_at: str | None = None
    # Extracted in the scoring node from the description text. We persist
    # both a human-readable original ("$120,000 to $150,000 USD") and the
    # parsed min/max in whole dollars so the dashboard can render a chip.
    salary_text: str = ""
    salary_min: int | None = None
    salary_max: int | None = None


class ScoredJob(BaseModel):
    """A Job with the scorer's verdict attached."""
    job: Job
    score: int                              # 0..100
    fit_reasoning: str                      # 1-3 sentences why this score
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    hard_rejected: bool = False             # True if dropped by deterministic gate
    hard_rejected_reason: str = ""


class GraphState(TypedDict, total=False):
    profile: Profile
    discovered: list[Job]
    scored: list[ScoredJob]
    digest_sent: bool
    error: str
