"use client";

import { useEffect, useState } from "react";
import { supabase, TABLE, Job } from "@/lib/supabase";

type Filter = "new" | "all" | "applied" | "snoozed";

function scoreClass(score: number): string {
  if (score >= 80) return "score high";
  if (score >= 70) return "score mid";
  if (score >= 50) return "score low";
  return "score dim";
}

export default function Page() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filter, setFilter] = useState<Filter>("new");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    let q = supabase
      .from(TABLE)
      .select("*")
      .eq("hard_rejected", false)
      .order("score", { ascending: false })
      .limit(200);
    if (filter === "new") {
      q = q.is("user_decision", null).gte("score", 50);
    } else if (filter === "applied") {
      q = q.eq("user_decision", "applied");
    } else if (filter === "snoozed") {
      q = q.eq("user_decision", "snoozed");
    }
    const { data, error } = await q;
    if (error) console.error(error);
    setJobs(data ?? []);
    setLoading(false);
  }

  useEffect(() => {
    load();
  }, [filter]);

  async function decide(url: string, decision: "applied" | "snoozed" | "rejected") {
    const res = await fetch("/api/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canonical_url: url, decision }),
    });
    if (res.ok) {
      setJobs((js) => js.filter((j) => j.canonical_url !== url));
    }
  }

  return (
    <div className="wrap">
      <h1>Job matches</h1>
      <div className="sub">
        Scored against your profile. Click through to apply, then mark applied or snooze.
      </div>

      <div className="controls">
        {(["new", "applied", "snoozed", "all"] as Filter[]).map((f) => (
          <button
            key={f}
            className={`btn ${filter === f ? "active" : ""}`}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="empty">Loading…</div>
      ) : jobs.length === 0 ? (
        <div className="empty">
          No jobs in this view yet. The runner will fill this on its next pass.
        </div>
      ) : (
        jobs.map((j) => (
          <div className="card" key={j.canonical_url}>
            <div className="row">
              <a className="title" href={j.canonical_url} target="_blank" rel="noreferrer">
                {j.title}
              </a>
              <span className={scoreClass(j.score)}>{j.score}/100</span>
            </div>
            <div className="meta">
              {j.company}
              {j.location ? ` · ${j.location}` : ""} · {j.source}
            </div>
            {j.fit_reasoning && <div className="reasoning">{j.fit_reasoning}</div>}
            {j.strengths && j.strengths.length > 0 && (
              <div className="strengths">
                <strong>Fit:</strong> {j.strengths.join(", ")}
              </div>
            )}
            {j.gaps && j.gaps.length > 0 && (
              <div className="gaps">
                <strong>Gaps:</strong> {j.gaps.join(", ")}
              </div>
            )}
            {filter === "new" && (
              <div className="actions">
                <button
                  className="action applied"
                  onClick={() => decide(j.canonical_url, "applied")}
                >
                  Mark applied
                </button>
                <button
                  className="action snoozed"
                  onClick={() => decide(j.canonical_url, "snoozed")}
                >
                  Snooze
                </button>
                <button
                  className="action rejected"
                  onClick={() => decide(j.canonical_url, "rejected")}
                >
                  Not for me
                </button>
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
