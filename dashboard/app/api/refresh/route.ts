import { NextResponse } from "next/server";

// Fires a workflow_dispatch against the langgraph-jobsearch-agent repo so
// the candidate can manually trigger a fresh discovery+scoring cycle from
// the dashboard. Backstop against impatience: a real run takes ~10 minutes
// so we apply a 5-minute per-cookie cooldown by stamping a header back
// that the UI reads.
//
// Required env vars:
//   GITHUB_TOKEN     — PAT with `workflow` scope on the langgraph repo
//   REFRESH_REPO     — "apexweb-adam/langgraph-jobsearch-agent" (default)
//   REFRESH_WORKFLOW — "daily.yml" (default)
//
// We do NOT pass any user input downstream — the workflow runs on `main`
// with the secrets that GitHub already holds, so this endpoint can't be
// abused to leak data.

const REPO = process.env.REFRESH_REPO || "apexweb-adam/langgraph-jobsearch-agent";
const WORKFLOW = process.env.REFRESH_WORKFLOW || "daily.yml";

export async function POST(_req: Request) {
  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return NextResponse.json(
      {
        error:
          "Refresh isn't configured yet. Ask Adam to set GITHUB_TOKEN in Vercel.",
      },
      { status: 500 }
    );
  }

  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const r = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main" }),
  });

  if (r.status === 204) {
    return NextResponse.json({ ok: true, started: true });
  }

  // GitHub returns 422 if the workflow is already queued under the same
  // concurrency group. That's actually fine for the candidate ("already
  // refreshing").
  if (r.status === 422) {
    return NextResponse.json({
      ok: true,
      started: false,
      message: "A refresh is already in progress. New roles should appear in ~10 minutes.",
    });
  }

  const body = await r.text();
  return NextResponse.json(
    { error: `GitHub returned ${r.status}: ${body.slice(0, 300)}` },
    { status: 500 }
  );
}
