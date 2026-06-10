import { NextRequest, NextResponse } from "next/server";

// Single-secret cookie gate. Cheaper to wire than full magic-link auth
// (no email provider, no Supabase Auth UI), and good enough for a one-
// candidate dashboard: send Diamond ONE link like
//   https://.../?key=<DASHBOARD_ACCESS_KEY>
// First visit sets a year-long cookie; every visit after that just works.
//
// If no key is set in env, the gate is disabled (dev mode).

const COOKIE = "dash_access";

export function middleware(req: NextRequest) {
  const required = process.env.DASHBOARD_ACCESS_KEY;
  if (!required) return NextResponse.next();

  const url = new URL(req.url);

  // Always let the static /unauth page through so we have something to render.
  if (url.pathname === "/unauth") return NextResponse.next();

  // First-visit handshake: ?key=... in the URL sets the cookie and strips
  // the query param so Diamond never sees the secret again.
  const keyParam = url.searchParams.get("key");
  if (keyParam && keyParam === required) {
    url.searchParams.delete("key");
    const res = NextResponse.redirect(url);
    res.cookies.set(COOKIE, required, {
      httpOnly: true,
      secure: true,
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
    });
    return res;
  }

  // Returning visit: cookie must match.
  const cookie = req.cookies.get(COOKIE)?.value;
  if (cookie === required) return NextResponse.next();

  // No valid auth: send to /unauth.
  url.pathname = "/unauth";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/|favicon|.*\\.(?:png|jpg|svg|ico|css|js)).*)"],
};
