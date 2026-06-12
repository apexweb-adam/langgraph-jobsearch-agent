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
- 40-49: same professional function, multiple real gaps, still worth a look
- <40 : wrong function or wrong level, do not surface

Calibration anchor: if the role is in the candidate's core FUNCTION
(program leadership, volunteer engagement, partnerships, community impact
at a mission-driven organization) and at roughly the right seniority, the
score belongs at 45 or higher even when several specifics are missing.
Reserve sub-40 for roles in a different function (sales, engineering,
fundraising-only, data, admin) or clearly wrong level. When torn between
two bands, pick the higher one; the human reviews everything above 40.

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


# Remote markers trusted in the LOCATION field, where they are reliable
# (Indeed and LinkedIn put "Remote", "Remote, US" etc. straight in location).
_REMOTE_LOC_MARKERS = (
    "remote", "work from home", "wfh", "anywhere", "remote-first",
    "distributed", "telecommute", "telework", "home-based", "home based",
)

# Strong remote phrases required in the DESCRIPTION to override a bound city
# location. A bare "remote" is too weak: it matches "not a remote role",
# "remote offices", "remotely related", and similar false positives.
_REMOTE_DESC_PHRASES = (
    "fully remote", "100% remote", "100 percent remote", "work from anywhere",
    "work-from-anywhere", "this role is remote", "this position is remote",
    "remote (us", "remote - us", "remote, us", "remote within the us",
    "remote within the united states", "us-remote", "remote anywhere in the us",
)

# Location strings that are nationwide or ambiguous, not a specific city the
# candidate would have to relocate to. Kept (let the scorer judge) rather than
# rejected, because Indeed and LinkedIn frequently label genuinely remote US
# roles this way.
_NATIONWIDE_LOCS = (
    "", "united states", "usa", "us", "u.s.", "u.s.a.", "nationwide",
    "national", "various", "multiple locations", "multiple", "anywhere",
)

# Foreign location markers. The candidate's timezones are US only, so a role
# anchored to one of these is out of scope even if it calls itself remote
# (for example GiveDirectly and One Acre Fund roles based in East Africa).
_FOREIGN_MARKERS = (
    "kenya", "nigeria", "rwanda", "uganda", "tanzania", "ghana", "ethiopia",
    "nairobi", "lagos", "kigali", "kampala", "india", "united kingdom",
    "london", "england", "canada", "toronto", "australia", "germany",
    "france", "netherlands", "amsterdam", "singapore", "philippines",
    "mexico", "brazil", "south africa", "ireland", "dublin", "remote - global",
    "remote, global", "global remote", "emea", "apac", "latam",
)


def _location_ok(job: Job, profile: Profile) -> tuple[bool, str]:
    """For a remote-only, US-timezone candidate, decide if a posting is reachable.

    Keep the job when: the LOCATION field shows a remote marker, OR it sits in
    one of the candidate's geos (Atlanta GA), OR the location is nationwide or
    ambiguous, OR a bound city is overridden by a strong remote phrase in the
    description. Reject a foreign-anchored role outright, and reject a specific
    US city that is neither in the geos nor backed by a strong remote phrase,
    since that would require relocation.
    """
    loc = (job.location or "").strip().lower()
    desc_head = (job.description or "")[:1500].lower()

    # 0) Foreign-anchored: out of scope (US timezones only), even if "remote".
    if any(m in loc for m in _FOREIGN_MARKERS):
        return False, f"out of scope, foreign location: {job.location}"

    # 1) Remote stated in the LOCATION field: keep.
    if any(m in loc for m in _REMOTE_LOC_MARKERS):
        return True, ""

    # 2) In one of the candidate's preferred geos (Atlanta, Georgia, GA, etc).
    geo_tokens: list[str] = []
    for g in profile.geos:
        gl = g.lower()
        if "remote" in gl:
            continue  # handled by the remote markers above
        for tok in re.split(r"[ ,]+", gl):
            if len(tok) >= 2:
                geo_tokens.append(tok)
    if loc and any(_term_in(loc, tok) for tok in geo_tokens):
        return True, ""

    # 3) Nationwide or ambiguous location: keep, let the scorer weigh it.
    if loc in _NATIONWIDE_LOCS:
        return True, ""

    # 4) A specific bound city, kept ONLY if the JD strongly states remote.
    if any(p in desc_head for p in _REMOTE_DESC_PHRASES):
        return True, ""

    # 5) Otherwise the role is location-bound: reject, relocation is off the table.
    where = job.location or "unspecified"
    return False, f"remote-only profile, posting is location-bound to {where}"


def _pre_filter(job: Job, profile: Profile) -> tuple[bool, str]:
    """Return (rejected, reason). Runs BEFORE any LLM call. Saves API spend."""
    for term in profile.reject_titles:
        if _term_in(job.title, term):
            return True, f"title contains rejected term '{term.lower()}'"

    # Hard-blocked companies. Substring match on the company name handles
    # variations like "American Red Cross", "American Red Cross of Greater
    # Atlanta", etc. with a single rule.
    company_low = (job.company or "").lower()
    for blocked in (profile.reject_companies or []):
        if blocked.lower() in company_low:
            return True, f"company '{job.company}' is on the candidate's hard-block list ({blocked})"

    if profile.remote_only:
        ok, reason = _location_ok(job, profile)
        if not ok:
            return True, reason

    # Rejected industries, checked BEFORE the LLM call. This used to live in
    # the post-filter, which meant we paid a Gemini call for jobs we were
    # going to throw away anyway. With 700+ jobs per run, that is the
    # difference between fitting the workflow timeout and not.
    desc_first2k = (job.description or "")[:2000]
    for term in profile.rejected_industries:
        if _term_in(desc_first2k, term):
            return True, f"description hits rejected industry '{term.lower()}'"

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

    # Cheap deterministic salary extraction. Runs on every job (even hard-
    # rejected ones, since salary is still useful on the dashboard for the
    # "applied" view). The LLM scorer never sees this; it judges salary fit
    # from the JD text directly via the prompt's salary_floor anchor.
    from ..salary import extract_salary
    for j in jobs:
        text, lo, hi = extract_salary(j.description or "")
        j.salary_text = text
        j.salary_min = lo
        j.salary_max = hi

    # Split into hard-rejected (no LLM cost) and to-score buckets.
    out: list[ScoredJob] = []
    to_score: list[Job] = []
    for j in jobs:
        rejected, reason = _pre_filter(j, profile)
        if rejected:
            out.append(ScoredJob(
                job=j, score=0, fit_reasoning=reason,
                hard_rejected=True, hard_rejected_reason=reason,
            ))
        else:
            to_score.append(j)

    # Score concurrently. Gemini Flash happily takes 8 parallel requests and
    # each call is network-bound, so threads (not processes) are the right
    # tool. 700 sequential calls blew through the workflow's 60-minute
    # timeout; 8-wide this finishes in under 10 minutes.
    from concurrent.futures import ThreadPoolExecutor
    print(f"  [scoring] {len(to_score)} jobs to LLM ({len(out)} pre-rejected without LLM cost)")
    with ThreadPoolExecutor(max_workers=8) as ex:
        scored_list = list(ex.map(lambda j: _score_one(llm, j, profile), to_score))
    for scored in scored_list:
        out.append(_post_filter(scored, profile))

    out.sort(key=lambda s: s.score, reverse=True)
    pre_blocked = sum(1 for s in out if s.hard_rejected)
    high = sum(1 for s in out if s.score >= 70)
    with_sal = sum(1 for s in out if s.job.salary_min)
    print(f"  [scoring] {high} high-fit (>=70), {pre_blocked} hard-rejected, "
          f"{with_sal} with salary parsed")
    return {"scored": out}
