"""Scoring node: deterministic pre-filter, then Gemini relevance score,
then deterministic post-filter.

The deterministic layers are NOT redundant with the LLM. They exist because
LLM scoring drifts on edge cases (over-scoring "Director of Marketing" when
the profile targets "AI Engineer"). The hard gates make the model's mistakes
non-actionable.

Structured output: we use LangChain's with_structured_output() which forces
Gemini to return a Pydantic-validated object. No markdown fences, no JSON parse
errors, no half-truncated responses.

Pure function: state["profile"], state["discovered"] -> {"scored": [...]}.
"""
from __future__ import annotations

import os

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from ..state import GraphState, Job, ScoredJob
from ..profile.schema import Profile


class ScoreOutput(BaseModel):
    """What the LLM must return. with_structured_output() enforces this shape."""
    score: int = Field(ge=0, le=100, description="0-100 fit score for the candidate")
    fit_reasoning: str = Field(max_length=300, description="One short sentence, max 30 words")
    strengths: list[str] = Field(default_factory=list, description="Up to 3 bullets, each max 5 words")
    gaps: list[str] = Field(default_factory=list, description="Up to 3 bullets, each max 5 words")


SYSTEM_PROMPT = """You are a senior recruiter scoring jobs for one candidate.

Return strictly this JSON (no markdown, no prose outside the JSON):
{
  "score": <0-100 integer>,
  "fit_reasoning": "<one short sentence, max 30 words, no fluff>",
  "strengths": ["<=5 word bullet", "<=5 word bullet"],
  "gaps": ["<=5 word bullet"]
}

Hard limits:
- fit_reasoning: 30 words MAXIMUM. Be terse. The user reads this in a scrolling email.
- strengths and gaps: each item 5 words max.
- Never repeat the job title or company name back in your response.

Scoring rubric:
- 90+ : near-perfect fit, candidate should apply today
- 70-89: strong fit with minor gaps
- 50-69: plausible fit but stretch
- 30-49: weak fit, only if desperate
- <30 : do not apply

Weight: target_titles match (heavy), skills overlap (heavy), seniority (medium),
location/remote compatibility (medium), industry (light)."""


USER_PROMPT_TPL = """CANDIDATE PROFILE:
- Target titles: {target_titles}
- Reject titles (hard no): {reject_titles}
- Skills: {skills}
- Seniority: {seniority}
- Remote only: {remote_only}
- Geos: {geos}
- Salary floor: {salary_floor}
- Preferred industries: {preferred_industries}
- Rejected industries: {rejected_industries}

JOB POSTING:
- Company: {company}
- Title: {title}
- Location: {location}
- Description (first 4000 chars):
{description}

Return ONLY the JSON object."""


# ── Deterministic gates ─────────────────────────────────────────────────────

import re


def _term_in(haystack_orig: str, term: str) -> bool:
    """Word-boundary match. Used instead of plain substring matching so that
    short reject terms don't blow up:

    - "intern" should match "Internship" and "Marketing Intern" but NOT
      "Internal Audit Manager".
    - "IT" should match "IT Operations" but NOT the pronoun "it" in "it is".
    - "finance" should match "Finance Director" but NOT "refinance".

    For terms of 1-2 characters we require an exact-case match against the
    original string (so "IT" only matches uppercase "IT", not "it"). For
    longer terms we use case-insensitive word-boundary regex.
    """
    if not term or not haystack_orig:
        return False
    if len(term) <= 2:
        # Case-sensitive on the original to require uppercase short abbreviations
        return bool(re.search(rf"\b{re.escape(term)}\b", haystack_orig))
    return bool(re.search(rf"\b{re.escape(term)}\b", haystack_orig, re.IGNORECASE))


def _pre_filter(job: Job, profile: Profile) -> tuple[bool, str]:
    """Return (rejected, reason). Runs BEFORE any LLM call. Saves API spend."""
    for term in profile.reject_titles:
        if _term_in(job.title, term):
            return True, f"title contains rejected term '{term.lower()}'"

    if profile.remote_only:
        loc_low = (job.location or "").lower()
        desc_low = (job.description or "")[:1000].lower()
        # Hard on-site signals in the first KB of the JD
        onsite_signals = ("on-site", "onsite", "in-office", "in office", "must be located in")
        if any(s in desc_low or s in loc_low for s in onsite_signals):
            if "remote" not in desc_low and "remote" not in loc_low:
                return True, "remote-only profile, posting requires on-site"

    return False, ""


def _post_filter(scored: ScoredJob, profile: Profile) -> ScoredJob:
    """Override the LLM if it scored something we hard-reject. Runs AFTER scoring."""
    for term in profile.reject_titles:
        if _term_in(scored.job.title, term):
            scored.hard_rejected = True
            scored.hard_rejected_reason = f"title contains rejected term '{term.lower()}'"
            scored.score = 0
            return scored

    desc_first2k = scored.job.description[:2000] if scored.job.description else ""
    for term in profile.rejected_industries:
        if _term_in(desc_first2k, term):
            scored.hard_rejected = True
            scored.hard_rejected_reason = f"description hits rejected industry '{term.lower()}'"
            scored.score = min(scored.score, 20)
            return scored

    return scored


# ── LLM call ───────────────────────────────────────────────────────────────

def _llm():
    if not os.environ.get("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY is not set")
    base = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.2,
        max_output_tokens=1200,
    )
    # with_structured_output() wires Gemini's native JSON mode behind the scenes
    # and validates the response against our Pydantic schema. If it fails to
    # produce valid JSON, LangChain raises rather than returning garbage.
    return base.with_structured_output(ScoreOutput)


def _score_one(llm, job: Job, profile: Profile) -> ScoredJob:
    user = USER_PROMPT_TPL.format(
        target_titles=", ".join(profile.target_titles),
        reject_titles=", ".join(profile.reject_titles) or "(none)",
        skills=", ".join(profile.skills),
        seniority=", ".join(profile.seniority) or "(any)",
        remote_only=profile.remote_only,
        geos=", ".join(profile.geos) or "(any)",
        salary_floor=(
            f"{profile.salary_floor.amount} {profile.salary_floor.currency} "
            f"per {profile.salary_floor.period}"
            if profile.salary_floor else "(none specified)"
        ),
        preferred_industries=", ".join(profile.preferred_industries) or "(any)",
        rejected_industries=", ".join(profile.rejected_industries) or "(none)",
        company=job.company,
        title=job.title,
        location=job.location or "(unspecified)",
        description=(job.description or "(no description)")[:4000],
    )
    try:
        out: ScoreOutput = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user),
        ])
        return ScoredJob(
            job=job,
            score=out.score,
            fit_reasoning=out.fit_reasoning,
            strengths=out.strengths,
            gaps=out.gaps,
        )
    except Exception as e:
        return ScoredJob(job=job, score=0, fit_reasoning=f"scorer error: {e}")


def scoring_node(state: GraphState) -> GraphState:
    profile: Profile = state["profile"]
    jobs: list[Job] = state.get("discovered", [])
    if not jobs:
        return {"scored": []}

    print(f"  [scoring] {len(jobs)} jobs (pre-filter -> Gemini -> post-filter)")
    llm = _llm()

    out: list[ScoredJob] = []
    for j in jobs:
        rejected, reason = _pre_filter(j, profile)
        if rejected:
            out.append(ScoredJob(
                job=j, score=0, fit_reasoning=reason,
                hard_rejected=True, hard_rejected_reason=reason,
            ))
            continue
        scored = _score_one(llm, j, profile)
        scored = _post_filter(scored, profile)
        out.append(scored)

    out.sort(key=lambda s: s.score, reverse=True)
    pre_blocked = sum(1 for s in out if s.hard_rejected)
    high = sum(1 for s in out if s.score >= 70)
    print(f"  [scoring] {high} high-fit (>=70), {pre_blocked} hard-rejected")
    return {"scored": out}
