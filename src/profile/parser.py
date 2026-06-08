"""Profile builder: parse a PDF/DOCX CV + cover letter into the structured Profile.

Used at kickoff (one-shot CLI: `python -m src.profile.parser <cv> <cl>`) and
again any time the client edits their resume. The output YAML is hand-editable;
the parser is a draft, the human is the final say.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml
from docx import Document
from pypdf import PdfReader
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

from .schema import Profile


SYSTEM = """You are a resume parser. Read the user's CV plus optional cover letter
and return a structured JSON profile that downstream agents will use to score jobs.

Return strictly this JSON shape (no markdown, no prose outside the JSON):
{
  "name": "...",
  "email": "...",
  "target_titles": ["..."],          # 3-5 titles the candidate is targeting
  "reject_titles": ["..."],          # titles to hard-reject (infer from CV gaps)
  "skills": ["..."],                 # 10-15 strongest skills
  "preferred_industries": ["..."],
  "rejected_industries": [],
  "remote_only": true,
  "geos": [],
  "timezones": [],
  "salary_floor": null,              # or {"amount": 100000, "currency": "USD", "period": "yearly"}
  "seniority": ["mid"],
  "company_size": [],
  "target_companies": {"greenhouse": [], "lever": []},
  "resume_text": "<verbatim resume text, max 6000 chars>",
  "cover_letter_text": "<verbatim cover letter text, max 3000 chars>"
}

If a field is uncertain, use a conservative empty default. The human will edit."""


JSON_RE = re.compile(r"\{.*\}", re.S)


def _read_pdf(path: Path) -> str:
    r = PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def _read_docx(path: Path) -> str:
    d = Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


def _read_any(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return _read_pdf(path)
    if suf in (".docx", ".doc"):
        return _read_docx(path)
    if suf in (".txt", ".md"):
        return path.read_text(encoding="utf-8")
    raise ValueError(f"unsupported file type: {suf}")


def parse(cv_path: str, cover_letter_path: str | None = None) -> Profile:
    if not os.environ.get("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY is not set")

    cv_text = _read_any(Path(cv_path))
    cl_text = _read_any(Path(cover_letter_path)) if cover_letter_path else ""

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.1,
        max_output_tokens=2000,
    )
    user = (
        f"CV:\n{cv_text[:12000]}\n\n"
        f"COVER LETTER:\n{cl_text[:6000] if cl_text else '(none provided)'}"
    )
    resp = llm.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=user)])
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    m = JSON_RE.search(raw)
    if not m:
        raise RuntimeError(f"parser returned no JSON: {raw[:300]}")
    data = json.loads(m.group(0))
    return Profile.model_validate(data)


def to_yaml(profile: Profile) -> str:
    return yaml.safe_dump(profile.model_dump(), sort_keys=False, allow_unicode=True)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m src.profile.parser <cv.pdf|cv.docx> [cover_letter]")
        return 1
    cv = sys.argv[1]
    cl = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"parsing {cv}{' + ' + cl if cl else ''}")
    p = parse(cv, cl)
    out_path = Path(os.environ.get("PROFILE_PATH", "./data/profile.yaml"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_yaml(p), encoding="utf-8")
    print(f"wrote {out_path} - review and edit, especially target_companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
