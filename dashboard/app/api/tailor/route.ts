import { NextResponse } from "next/server";

// Per-job cover letter tailoring. The candidate's resume and a generic cover-
// letter template are stored in Vercel env vars (CANDIDATE_RESUME and
// CANDIDATE_COVER_BASE) so the source-of-truth profile YAML stays in the
// langgraph repo and never has to ship to this Next.js project.
//
// Uses Gemini 2.5 Flash directly via REST so we don't have to bundle the
// google generative AI SDK into a serverless function.

const MODEL = "gemini-2.5-flash";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const title = String(body?.title ?? "").trim();
  const company = String(body?.company ?? "").trim();
  const description = String(body?.description ?? "").slice(0, 6000);

  if (!title || !company) {
    return NextResponse.json({ error: "missing title or company" }, { status: 400 });
  }

  const apiKey = process.env.GOOGLE_API_KEY;
  const resume = process.env.CANDIDATE_RESUME || "";
  const base = process.env.CANDIDATE_COVER_BASE || "";
  if (!apiKey || !resume) {
    return NextResponse.json(
      { error: "Tailoring is not configured yet. Ask Adam to set the GOOGLE_API_KEY and CANDIDATE_RESUME env vars in Vercel." },
      { status: 500 }
    );
  }

  const prompt = `
You are a senior nonprofit career writer. Adapt the candidate's existing cover
letter and resume into a tailored cover letter for the role below.

Hard rules:
- Do NOT invent any experience that isn't in the resume.
- Do NOT use em dashes (—) or en dashes (–). Use commas, periods, colons, or
  the word "to" for ranges.
- Keep it to 3 to 4 tight paragraphs, max 350 words.
- Open with a hook that names the company and the role.
- Cite 2 to 3 concrete numbers or programs from the resume that match the
  job description.
- Close with a confident, warm sign-off.
- No filler like "I am writing to apply for..." Start with substance.

Target role:
Title: ${title}
Company: ${company}
Description (excerpt):
${description || "(no description available)"}

Candidate resume:
${resume}

Candidate's existing base cover letter (use as voice anchor):
${base || "(none provided)"}

Output: just the cover letter body, ready to paste. No preamble, no markdown
headings.
`.trim();

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${apiKey}`;
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { temperature: 0.4, maxOutputTokens: 1200 },
    }),
  });

  if (!r.ok) {
    const txt = await r.text();
    return NextResponse.json(
      { error: `Gemini error ${r.status}: ${txt.slice(0, 400)}` },
      { status: 500 }
    );
  }
  const j = await r.json();
  const text =
    j?.candidates?.[0]?.content?.parts?.[0]?.text?.trim() ||
    "Gemini returned an empty response.";

  // Strip any em/en dashes that slipped through, per the candidate's hard rule.
  const cleaned = text.replace(/—/g, ", ").replace(/–/g, " to ");

  return NextResponse.json({ text: cleaned });
}
