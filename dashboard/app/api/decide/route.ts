import { NextResponse } from "next/server";
import { serverClient, TABLE } from "@/lib/supabase";

const VALID = new Set(["applied", "snoozed", "rejected"]);

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const url = body?.canonical_url as string | undefined;
  const decision = body?.decision as string | undefined;
  if (!url || !decision || !VALID.has(decision)) {
    return NextResponse.json({ error: "bad input" }, { status: 400 });
  }
  const client = serverClient();
  const { error } = await client
    .from(TABLE)
    .update({ user_decision: decision })
    .eq("canonical_url", url);
  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
  return NextResponse.json({ ok: true });
}
