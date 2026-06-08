"""Profile schema + Pydantic validation."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class TargetCompanies(BaseModel):
    """Companies to watch per ATS. The token is what appears in the board URL.

    Greenhouse: https://boards.greenhouse.io/<token>      -> use <token>
    Lever:      https://jobs.lever.co/<company_slug>      -> use <company_slug>
    """
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)


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

    # Free-text resume + cover letter (filled by parser or by hand)
    resume_text: str = ""
    cover_letter_text: str = ""

    def reject_titles_lower(self) -> list[str]:
        return [t.lower() for t in self.reject_titles]
