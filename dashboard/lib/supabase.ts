import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

// Read-only client used in pages. RLS allows public SELECT on jobs.
export const supabase = createClient(url, anon, {
  auth: { persistSession: false },
});

// Server-side client with service role, only used in /api routes. Never expose.
export function serverClient() {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
  return createClient(url, key, { auth: { persistSession: false } });
}

export type Job = {
  canonical_url: string;
  source: string;
  company: string;
  title: string;
  location: string | null;
  score: number;
  fit_reasoning: string | null;
  strengths: string[] | null;
  gaps: string[] | null;
  hard_rejected: boolean;
  user_decision: "applied" | "snoozed" | "rejected" | null;
  first_seen_at: string;
};
