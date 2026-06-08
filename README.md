# berniesbaby-jobsearch

A LangGraph-orchestrated job discovery, scoring, and human-in-the-loop approval system. Built for berniesbaby as Phase 1 of a two-phase engagement.

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
1. Discovers all open roles at your target companies on Greenhouse + Lever (public APIs, no auth needed for public boards).
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
    scoring.py          # Gemini relevance score + hard gates
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

LLM scoring drifts. Without guardrails, Gemini will over-score "Director of Marketing" when you target "Senior Backend Engineer." Two layers prevent that:

1. **Pre-filter** (no API call): if the job title contains any `reject_titles` string, score = 0. If you're remote-only and the JD requires on-site, score = 0.
2. **Post-filter** (after the LLM): if the model returns a high score for something that hits a `rejected_industries` term in the JD, we override the score down.

That pattern is the same one I used on Chris's job tracker to fix the leak after a multi-week investigation. It's why the digest is signal, not noise.

## What's in Phase 2

- Per-job CV and cover letter tailoring (Gemini)
- One-click apply via Greenhouse/Lever ATS APIs where available
- Cross-source dedup by company + title fuzzy match
- Score calibration loop: your approve/reject signals tune the scoring weights over time

## Deployment

For the MVP, the recommended run is a daily cron on a small VPS or GitHub Actions:

```yaml
# .github/workflows/daily.yml
on: { schedule: [{ cron: "0 13 * * 1-5" }] }   # Mon-Fri 13:00 UTC = 9am ET
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

The SQLite DB persists run-to-run by uploading the `data/` directory as an artifact (or replace with Postgres in Phase 2).

## Status

Phase 1 MVP. The pipeline runs end-to-end. Pending integration: kickoff call to populate your real profile + target companies.
