"""Profile schema + Pydantic validation."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class ApifySource(BaseModel):
    """One Apify Actor configuration. Used for any ATS we can't hit natively
    (Paylocity, ADP, Indeed, custom React careers pages).

    actor_id: the Actor's "<author>/<name>" path on Apify Store.
    label:    short identifier surfaced in transcripts and the dashboard.
    input:    the Actor's run-input dict, passed straight to the API.
    mapping:  how to map raw item fields to our Job model. Each value can be a
              dotted path into a nested dict.
    """
    actor_id: str
    label: str
    input: dict = Field(default_factory=dict)
    mapping: dict[str, str] = Field(default_factory=dict)


class TargetCompanies(BaseModel):
    """Companies to watch per ATS. The token is what appears in the board URL.

    Greenhouse: https://boards.greenhouse.io/<token>      -> use <token>
    Lever:      https://jobs.lever.co/<company_slug>      -> use <company_slug>
    JazzHR:     https://<subdomain>.applytojob.com        -> use <subdomain>

    Apify sources cover anything JS-rendered (Paylocity, ADP, iCIMS, Indeed).
    """
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    jazzhr: list[str] = Field(default_factory=list)
    apify: list[ApifySource] = Field(default_factory=list)


class SalaryFloor(BaseModel):
    amount: int
    currency: str = "USD"
    period: Literal["yearly", "monthly", "hourly"] = "yearly"


class Profile(BaseModel):
    """Structured candidate profile.

    Source of truth for the scoring node. Edit data/profile.yaml then re-run
    discovery; the graph picks up the new values on the next pass.
    """
    name: str
    email: str

    # Roles
    target_titles: list[str]
    reject_titles: list[str] = Field(default_factory=list)

    # Skills + industry weighting
    skills: list[str]
    preferred_industries: list[str] = Field(default_factory=list)
    rejected_industries: list[str] = Field(default_factory=list)

    # Location
    remote_only: bool = True
    geos: list[str] = Field(default_factory=list)
    timezones: list[str] = Field(default_factory=list)

    # Compensation
    salary_floor: SalaryFloor | None = None

    # Seniority + company shape
    seniority: list[str] = Field(default_factory=list)
    company_size: list[str] = Field(default_factory=list)

    # Companies to monitor
    target_companies: TargetCompanies

    # Hard-blocked companies. Case-insensitive substring match against the
    # job's company name in the pre-filter. Used for "I will never work
    # there again" rules (e.g. Diamond explicitly excluded American Red
    # Cross, federal jobs, local government).
    reject_companies: list[str] = Field(default_factory=list)

    # Free-text resume + cover letter (filled by parser or by hand)
    resume_text: str = ""
    cover_letter_text: str = ""

    def reject_titles_lower(self) -> list[str]:
        return [t.lower() for t in self.reject_titles]
