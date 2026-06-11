"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase, TABLE, Job } from "@/lib/supabase";

type Filter = "new" | "all" | "applied" | "snoozed";
type Band = "all" | "great" | "good" | "maybe";

function scoreClass(score: number): string {
  if (score >= 80) return "score high";
  if (score >= 70) return "score mid";
  if (score >= 50) return "score low";
  return "score dim";
}

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.round(hr / 24);
  return `${d}d ago`;
}

function formatSalary(j: Job): string | null {
  if (j.salary_min && j.salary_max) {
    if (j.salary_min === j.salary_max) {
      return `$${Math.round(j.salary_min / 1000)}K`;
    }
    return `$${Math.round(j.salary_min / 1000)}K to $${Math.round(j.salary_max / 1000)}K`;
  }
  if (j.salary_text) return j.salary_text;
  return null;
}

const CANDIDATE = process.env.NEXT_PUBLIC_CANDIDATE_NAME || "your";

export default function Page() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filter, setFilter] = useState<Filter>("new");
  const [band, setBand] = useState<Band>("all");
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [tailoring, setTailoring] = useState<string | null>(null);
  const [tailorOut, setTailorOut] = useState<string>("");
  const [refreshState, setRefreshState] = useState<
    "idle" | "starting" | "running" | "error"
  >("idle");
  const [refreshMessage, setRefreshMessage] = useState<string>("");

  async function load() {
    setLoading(true);
    let q = supabase
      .from(TABLE)
      .select("*")
      .eq("hard_rejected", false)
      .order("score", { ascending: false })
      .limit(500);
    if (filter === "new") {
      // Show undecided roles scoring 40+. Below 40 is consistently noise
      // (wrong function, wrong seniority) and burying the queue in it makes
      // the dashboard feel broken. The band sub-filter handles focus above
      // that line.
      q = q.is("user_decision", null).gte("score", 40);
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

  async function tailor(j: Job) {
    setTailoring(j.canonical_url);
    setTailorOut("Generating a tailored cover letter — about 10 seconds…");
    try {
      const res = await fetch("/api/tailor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: j.title,
          company: j.company,
          description: j.description ?? "",
        }),
      });
      const j2 = await res.json();
      setTailorOut(j2.text || j2.error || "Something went wrong.");
    } catch (e) {
      setTailorOut(String(e));
    }
  }

  async function copyTailored() {
    try {
      await navigator.clipboard.writeText(tailorOut);
    } catch {}
  }

  async function refresh() {
    setRefreshState("starting");
    setRefreshMessage("Kicking off a fresh discovery cycle…");
    try {
      const res = await fetch("/api/refresh", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setRefreshState("error");
        setRefreshMessage(data.error || "Refresh failed.");
        return;
      }
      setRefreshState("running");
      setRefreshMessage(
        data.message ||
          "Refresh started. New roles usually appear in about 10 minutes. You can keep using the dashboard while it runs."
      );
    } catch (e) {
      setRefreshState("error");
      setRefreshMessage(String(e));
    }
  }

  const filtered = useMemo(() => {
    if (band === "all") return jobs;
    if (band === "great") return jobs.filter((j) => j.score >= 80);
    if (band === "good") return jobs.filter((j) => j.score >= 70 && j.score < 80);
    return jobs.filter((j) => j.score >= 40 && j.score < 70);
  }, [jobs, band]);

  const counts = useMemo(() => {
    return {
      great: jobs.filter((j) => j.score >= 80).length,
      good: jobs.filter((j) => j.score >= 70 && j.score < 80).length,
      maybe: jobs.filter((j) => j.score >= 40 && j.score < 70).length,
    };
  }, [jobs]);

  const lastRefresh = useMemo(() => {
    const ts = jobs
      .map((j) => j.last_scored_at)
      .filter(Boolean)
      .sort()
      .pop();
    return timeAgo(ts ?? null);
  }, [jobs]);

  return (
    <div className="wrap">
      <header className="header">
        <div>
          <div className="brand">Job matches</div>
          <h1>Curated for {CANDIDATE}</h1>
          <div className="sub">
            Scored against your background. Click a role to expand, draft a
            tailored cover letter, then mark applied or snooze.
          </div>
        </div>
        <div className="last">
          <button
            className={`refresh-btn ${refreshState !== "idle" ? "busy" : ""}`}
            onClick={refresh}
            disabled={refreshState !== "idle" && refreshState !== "error"}
          >
            {refreshState === "starting"
              ? "Starting…"
              : refreshState === "running"
              ? "Refresh running…"
              : "↻ Refresh now"}
          </button>
          <div className="chip">Last refresh: {lastRefresh}</div>
          <div className="chip subtle">Auto-refresh every 6 hours</div>
        </div>
      </header>

      {refreshMessage && (
        <div className={`refresh-banner ${refreshState}`}>{refreshMessage}</div>
      )}

      <div className="controls">
        {(["new", "applied", "snoozed", "all"] as Filter[]).map((f) => (
          <button
            key={f}
            className={`btn ${filter === f ? "active" : ""}`}
            onClick={() => {
              setFilter(f);
              setBand("all");
            }}
          >
            {f}
          </button>
        ))}
        {filter === "new" && (
          <div className="bands">
            <button
              className={`band ${band === "all" ? "active" : ""}`}
              onClick={() => setBand("all")}
            >
              All ({counts.great + counts.good + counts.maybe})
            </button>
            <button
              className={`band great ${band === "great" ? "active" : ""}`}
              onClick={() => setBand("great")}
            >
              Great ≥80 ({counts.great})
            </button>
            <button
              className={`band good ${band === "good" ? "active" : ""}`}
              onClick={() => setBand("good")}
            >
              Good 70 to 79 ({counts.good})
            </button>
            <button
              className={`band maybe ${band === "maybe" ? "active" : ""}`}
              onClick={() => setBand("maybe")}
            >
              Maybe 40 to 69 ({counts.maybe})
            </button>
          </div>
        )}
      </div>

      {loading ? (
        <div className="empty">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="empty">
          <strong>No roles in this view yet.</strong>
          <div style={{ marginTop: 6, fontSize: 13 }}>
            The pipeline auto-refreshes every six hours. You can also kick off
            a fresh cycle now with the Refresh button above.
          </div>
        </div>
      ) : (
        filtered.map((j) => {
          const isOpen = !!expanded[j.canonical_url];
          return (
            <div className="card" key={j.canonical_url}>
              <div className="row">
                <a
                  className="title"
                  href={j.canonical_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {j.title}
                </a>
                <span className={scoreClass(j.score)}>{j.score}</span>
              </div>
              <div className="meta">
                <strong>{j.company}</strong>
                {j.location ? ` · ${j.location}` : ""}
                <span className="src"> · {j.source}</span>
                {formatSalary(j) && (
                  <span className="salary"> · 💵 {formatSalary(j)}</span>
                )}
              </div>
              {j.fit_reasoning && <div className="reasoning">{j.fit_reasoning}</div>}
              <div className="tags">
                {j.strengths?.map((s, i) => (
                  <span key={`s${i}`} className="tag fit">
                    {s}
                  </span>
                ))}
                {j.gaps?.map((g, i) => (
                  <span key={`g${i}`} className="tag gap">
                    {g}
                  </span>
                ))}
              </div>

              {j.description && (
                <button
                  className="expander"
                  onClick={() =>
                    setExpanded((e) => ({
                      ...e,
                      [j.canonical_url]: !isOpen,
                    }))
                  }
                >
                  {isOpen ? "Hide description ▴" : "Show full description ▾"}
                </button>
              )}
              {isOpen && j.description && (
                <pre className="description">{j.description}</pre>
              )}

              {filter === "new" && (
                <div className="actions">
                  <button
                    className="action primary"
                    onClick={() => tailor(j)}
                  >
                    Tailor cover letter
                  </button>
                  <a
                    className="action ghost"
                    href={j.canonical_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open job ↗
                  </a>
                  <button
                    className="action applied"
                    onClick={() => decide(j.canonical_url, "applied")}
                  >
                    ✓ Applied
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
          );
        })
      )}

      {tailoring && (
        <div className="modalbg" onClick={() => setTailoring(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modalhead">
              <div>Tailored cover letter</div>
              <button className="close" onClick={() => setTailoring(null)}>
                ✕
              </button>
            </div>
            <textarea className="tailorbox" value={tailorOut} readOnly />
            <div className="modalfoot">
              <button className="action primary" onClick={copyTailored}>
                Copy to clipboard
              </button>
              <button className="action ghost" onClick={() => setTailoring(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
