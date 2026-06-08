# langgraph-jobsearch-agent

A LangGraph-orchestrated job discovery, AI scoring, and human-in-the-loop approval system. Built to be deployed for any candidate in any field, against any combination of Greenhouse and Lever public job boards.

> **What this is.** A clean, opinionated MVP of an "agent team" that finds jobs for one human and hands them a daily ranked digest. Three nodes, deterministic guardrails, structured outputs, no spam.

## What this does

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐
│  discovery   │ -> │   scoring    │ -> │ approval (HITL gate) │
│              │    │              │    │                      │
│  Greenhouse  │    │  Gemini 2.5  │    │  daily email digest  │
│  Lever       │    │  + hard      │    │  of top-N scored     │
│  (concurrent)│    │  filters     │    │  jobs above floor    │
└──────────────┘    └──────────────┘    └──────────────────────┘
```

Every run:
1. Discovers all open roles at your target companies on Greenhouse and Lever (public APIs, no auth needed for public boards).
2. Scores each posting against your structured profile using Gemini 2.5 Flash, with deterministic gates layered on top so the model can never override hard requirements.
3. Sends you a daily HTML email digest of the top-N scored jobs above your score floor. Each match includes the score, fit reasoning, top strengths, and gaps.

No application is ever sent without your explicit action. You click through to apply on your schedule.

## Quickstart

```bash
# 1. Install
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. Configure
cp .env.example .env
# Fill in GOOGLE_API_KEY and SMTP_* credentials

# 3. Build your profile from your CV
python -m src.profile.parser /path/to/cv.pdf /path/to/cover_letter.pdf
# This writes data/profile.yaml. Open it and edit, especially:
#   - target_companies.greenhouse  (board tokens from boards.greenhouse.io/<token>)
#   - target_companies.lever       (slugs from jobs.lever.co/<slug>)
#   - salary_floor and reject_titles

# 4. Run the graph
python run.py                  # full run with digest email
python run.py --dry-run        # discovery + scoring only, no email
```

## Project layout

```
src/
  state.py              # LangGraph TypedDict + Pydantic models
  graph.py              # LangGraph wiring (3 nodes, linear)
  nodes/
    discovery.py        # async fan-out to all sources, dedup
    scoring.py          # Gemini structured output + hard gates
    approval.py         # email digest + DB upsert
  sources/
    greenhouse.py       # public board API client
    lever.py            # public postings API client
  profile/
    schema.py           # Profile Pydantic model
    parser.py           # CV/CL PDF/DOCX -> structured Profile via Gemini
  storage/
    db.py               # SQLite ledger
run.py                  # CLI entrypoint
```

## How the scoring stays honest

LLM scoring drifts. Without guardrails, Gemini will over-score "Director of Marketing" when you target "Senior Backend Engineer." Three layers prevent that:

1. **Pre-filter** (no API call): if the job title contains any `reject_titles` string, score = 0. If you're remote-only and the JD requires on-site, score = 0.
2. **Structured output** (during the LLM call): Gemini is bound to a Pydantic schema via `with_structured_output()`. No markdown fences, no truncated JSON, no parsing errors.
3. **Post-filter** (after the LLM): if the model returns a high score for something that hits a `rejected_industries` term in the JD, we override the score down.

That pattern eliminates the "Director of Sales gets a 70" class of failures that pure-LLM scoring is famous for. The digest is signal, not noise.

## Sample scoring run

Against the example "Senior Backend Engineer, remote-only, Python/AWS/distributed systems" profile, scored against the live Anthropic board:

```
[score  20]  Analytics Data Engineer
             reasoning: "On-site role does not align with remote-only candidate..."

[score  10]  Biological Safety Research Scientist
             reasoning: "Not software engineering, requires biological expertise, on-site."

[score  15]  Design Engineer, AI Capability Development
             reasoning: "Not remote-compatible, requires front-end design focus."

[HARD-REJ]   Enterprise Account Executive, Federal Civilian Sales
             reasoning: "title contains rejected term 'sales'"  (no API call made)
```

The hard-reject case is the killer feature: deterministic rules catch what LLMs miss, and they do it for free.

## Deployment

For the MVP, the recommended run is a daily cron on GitHub Actions:

```yaml
# .github/workflows/daily.yml
on: { schedule: [{ cron: "0 13 * * 1-5" }] }   # Mon-Fri 13:00 UTC
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e .
      - env:
          GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
          SMTP_HOST:      ${{ secrets.SMTP_HOST }}
          SMTP_USER:      ${{ secrets.SMTP_USER }}
          SMTP_PASS:      ${{ secrets.SMTP_PASS }}
          SMTP_FROM:      ${{ secrets.SMTP_FROM }}
          DIGEST_TO:      ${{ secrets.DIGEST_TO }}
        run: python run.py
```

The SQLite DB persists run-to-run by uploading the `data/` directory as an artifact. Swap to Postgres or Supabase for cross-machine persistence.

## Tests

```bash
uv run pytest tests/ -v
```

Three live tests hit real Greenhouse and Lever boards to validate connector schema and error handling. They run in under 6 seconds.

## What's next

Phase 2 extends this with:
- Per-job CV and cover letter tailoring (Gemini)
- One-click apply via ATS APIs where available
- Cross-source dedup by company plus title fuzzy match
- Score calibration loop: your approve/reject signals tune the scoring weights over time

## Stack

LangGraph · langchain-google-genai · httpx · Pydantic · pypdf · python-docx · SQLite · smtplib · Jinja2
