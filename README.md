# langgraph-jobsearch-agent

A LangGraph-orchestrated job discovery, AI scoring, and human-in-the-loop approval system. Built to be deployed for any candidate in any field, against any combination of supported ATSes plus arbitrary Apify-backed sources.

> **What this is.** A clean, opinionated MVP of an "agent team" that finds jobs for one human and surfaces them on a private dashboard. Three nodes, deterministic guardrails, structured outputs, no spam.

## What this does

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐
│  discovery   │ -> │   scoring    │ -> │   approval (HITL)    │
│              │    │              │    │                      │
│  Greenhouse  │    │  Gemini 2.5  │    │  dashboard mode:     │
│  Lever       │    │  + hard      │    │    upsert -> Supabase│
│  JazzHR      │    │  filters     │    │    -> Next.js read   │
│  Apify       │    │  (structured │    │  digest mode:        │
│  (concurrent)│    │  output)     │    │    daily email       │
└──────────────┘    └──────────────┘    └──────────────────────┘
```

Every run:
1. Discovers all open roles at your target companies on Greenhouse, Lever, JazzHR (native API), plus any Apify-backed sources you configure for ATSes that block direct HTTP (Paylocity, ADP Workforce Now, iCIMS, Indeed, custom React careers pages).
2. Scores each posting against your structured profile using Gemini 2.5 Flash, with deterministic gates layered on top so the model can never override hard requirements.
3. **Dashboard mode (default):** upserts scored jobs into Supabase. The Next.js dashboard reads them via the anon key. You mark each one applied, snoozed, or rejected.
4. **Digest mode (legacy):** sends a daily HTML email with the top-N scored jobs above your score floor.

No application is ever sent without your explicit action. You click through to apply on your schedule.

## Quickstart

```bash
# 1. Install Python deps
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. Configure
cp .env.example .env
# Fill in GOOGLE_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, APIFY_TOKEN.
# Apply the SQL in src/storage/supabase.py (SETUP_SQL) once to create the table.

# 3. Build your profile from your CV
python -m src.profile.parser /path/to/cv.pdf /path/to/cover_letter.pdf
# Writes data/profile.yaml. Open it and edit target_companies + reject lists.

# 4. Run the graph
python run.py                  # full run, writes scored jobs to Supabase
python run.py --dry-run        # discovery + scoring, no DB write

# 5. (optional) Run the dashboard
cd dashboard
cp .env.local.example .env.local
npm install
npm run dev
```

## Supported sources

| Source | Native or Apify | What you configure |
|---|---|---|
| Greenhouse | Native | `target_companies.greenhouse: [board_token]` |
| Lever | Native | `target_companies.lever: [company_slug]` |
| JazzHR | Native | `target_companies.jazzhr: [subdomain]` |
| Indeed | Apify | actor `misceres/indeed-scraper` with your search params |
| Paylocity | Apify | actor `apify/web-scraper` with company-specific JS |
| ADP Workforce Now | Apify | actor `apify/web-scraper` with company-specific JS |
| Any custom careers page | Apify | same pattern |

The Apify integration is generic: pick any Actor from the Apify Store, provide its `actor_id`, `input`, and a `mapping` from raw fields to our Job model. See `examples/profile.example.yaml` for the wiring.

## Project layout

```
src/
  state.py                # LangGraph TypedDict + Pydantic models
  graph.py                # 3-node linear graph
  nodes/
    discovery.py          # async fan-out to all sources, dedup
    scoring.py            # Gemini structured output + hard gates
    approval.py           # dashboard (Supabase) or digest (SMTP) mode
  sources/
    greenhouse.py         # public board API client
    lever.py              # public postings API client
    jazzhr.py             # public board API client
    apify.py              # generic Apify Actor runner + field mapper
  profile/
    schema.py             # Profile + ApifySource Pydantic models
    parser.py             # CV/CL PDF/DOCX -> structured Profile via Gemini
  storage/
    db.py                 # SQLite ledger (digest mode + tests)
    supabase.py           # Postgres adapter (dashboard mode)
dashboard/
  app/                    # Next.js App Router
  lib/supabase.ts         # typed client + Job model
  app/api/decide/route.ts # write user_decision via service role
run.py                    # CLI entrypoint
```

## How the scoring stays honest

LLM scoring drifts. Without guardrails, Gemini will over-score "Director of Marketing" when you target "Senior Backend Engineer." Three layers prevent that:

1. **Pre-filter** (no API call): if the job title contains any `reject_titles` string, score = 0. If you are remote-only and the JD requires on-site, score = 0.
2. **Structured output** (during the LLM call): Gemini is bound to a Pydantic schema via `with_structured_output()`. No markdown fences, no truncated JSON, no parse errors.
3. **Post-filter** (after the LLM): if the model returns a high score for something that hits a `rejected_industries` term in the JD, we override the score down.

That pattern eliminates the "Director of Sales gets a 70" class of failures pure-LLM scoring is famous for. The dashboard shows signal, not noise.

## Deployment

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
          GOOGLE_API_KEY:            ${{ secrets.GOOGLE_API_KEY }}
          SUPABASE_URL:              ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          APIFY_TOKEN:               ${{ secrets.APIFY_TOKEN }}
        run: python run.py
```

The dashboard deploys to Vercel free tier with the 3 Supabase env vars set in the Vercel project. Private URL by default; no auth needed for personal use because the URL is unguessable.

## Tests

```bash
uv run pytest tests/ -v
```

Live tests hit real Greenhouse, Lever, and JazzHR boards to validate connector schema and error handling. They run in under 8 seconds.

## What's next

Phase 2 extends this with:
- Per-job CV and cover letter tailoring (Gemini)
- One-click apply via ATS APIs where available
- Cross-source dedup by company + title fuzzy match
- Score calibration loop: your approve/reject signals tune scoring weights over time
- Email/Slack notification when a 90+ scored role appears

## Stack

LangGraph · langchain-google-genai · httpx · Pydantic · pypdf · python-docx · SQLite · Supabase (Postgres) · Next.js 14 · Apify · smtplib · Jinja2
