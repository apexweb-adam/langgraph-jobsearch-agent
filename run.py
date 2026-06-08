"""CLI entrypoint: load profile YAML, run the graph, exit.

  python run.py                  # one-shot
  python run.py --dry-run        # discovery + scoring only, no email send
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.graph import build
from src.profile.schema import Profile


def load_profile(path: str) -> Profile:
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"profile not found at {p}. Generate one with:\n"
            f"  python -m src.profile.parser <cv.pdf> [cover_letter.pdf]"
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return Profile.model_validate(data)


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="skip email send; still upserts the DB so digest sends next run")
    args = ap.parse_args()

    if args.dry_run:
        os.environ["SMTP_HOST"] = ""  # force the approval node into stdout mode

    profile = load_profile(os.environ.get("PROFILE_PATH", "./data/profile.yaml"))
    print(f"profile: {profile.name} <{profile.email}>")
    print(f"  target titles: {profile.target_titles}")
    print(f"  companies: greenhouse={len(profile.target_companies.greenhouse)} "
          f"lever={len(profile.target_companies.lever)}")

    graph = build()
    final = graph.invoke({"profile": profile})
    print(f"\ndone. digest_sent={final.get('digest_sent', False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
