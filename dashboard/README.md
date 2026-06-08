# Dashboard

Minimal Next.js 14 (App Router) dashboard. Reads scored jobs from Supabase via
the anon key, and lets the user mark each one applied / snoozed / rejected via
a service-role-backed API route.

## Local dev

```bash
cd dashboard
cp .env.local.example .env.local      # fill in NEXT_PUBLIC_SUPABASE_URL,
                                      # NEXT_PUBLIC_SUPABASE_ANON_KEY,
                                      # SUPABASE_SERVICE_ROLE_KEY
npm install
npm run dev
# open http://localhost:3000
```

## Deploy (Vercel free tier)

```bash
# in dashboard/
vercel
# set the 3 env vars in the Vercel project settings
# pick a private deployment URL or guard with vercel auth if you prefer
```

## Architecture

```
LangGraph runner (Python)
   v upserts via service-role key
[ Supabase: jobs table ]
   v reads via anon key (RLS allows SELECT only)
[ Next.js dashboard ]
   ^ user clicks "Mark applied" -> POST /api/decide
   ^ /api/decide uses service-role key (server-only) to UPDATE user_decision
```

The browser never holds the service-role key. The only write happens through
`/api/decide`, which validates the requested decision against a whitelist
before touching the DB.
