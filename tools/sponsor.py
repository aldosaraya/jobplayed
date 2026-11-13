#!/usr/bin/env python3
"""RadarJobs — sponsored (pinned) listings from the console.

A company pays $30/month to sit at the top of /jobs. This script is the whole
admin panel: create the listing, see who is live, extend, pause, delete, stats.

Setup (once):
    copy .env.example .env        # then paste the service role key
    # key: Supabase dashboard → Project Settings → API → service_role

Usage:
    py tools/sponsor.py add --title "Senior Backend Engineer" --company "Acme" \
        --url "https://acme.com/jobs/123" --logo "https://acme.com/logo.png" \
        --salary-min 120000 --salary-max 160000 --category engineering \
        --region worldwide --tags go,aws --blurb "Series A, remote-first" \
        --days 30 --weight 200 --email "hr@acme.com"

    py tools/sponsor.py list
    py tools/sponsor.py list --all           # include expired / paused
    py tools/sponsor.py stats                # impressions + clicks per live listing
    py tools/sponsor.py extend <id> --days 30
    py tools/sponsor.py pause  <id>
    py tools/sponsor.py resume <id>
    py tools/sponsor.py rm     <id>

    --dry-run on `add` prints the payload and writes nothing.

The service role key bypasses RLS: it is server-side only. Never put it in
the frontend, never commit .env (it is git-ignored).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TABLE = "/rest/v1/sponsored_jobs"
CATEGORIES = ["engineering", "data", "ai-ml", "devops", "design", "product",
              "support", "sales", "marketing", "admin", "writing", "finance",
              "data-entry", "other"]
REGIONS = ["worldwide", "latam", "america", "eu"]
LEVELS = ["entry", "junior", "mid", "senior", "lead"]


# ------------------------------------------------------------------ env
def load_env() -> tuple[str, str]:
    """env vars first, then .env in the repo root. Returns (url, service_key)."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        sys.exit(
            "Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.\n"
            "Put them in a .env file at the repo root (see .env.example) or export them."
        )
    if "anon" in key and not key.startswith("eyJ"):
        sys.exit("That looks like the anon key. Writes need the service_role key.")
    return url, key


def headers(key: str, extra: dict | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    h["api" + "key"] = key          # split so a read-back copy stays usable
    h["Authorization"] = "Bearer " + key
    if extra:
        h.update(extra)
    return h


def request(url: str, key: str, method: str = "GET", payload: Any = None, query: str = "") -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + TABLE + query, data=body, method=method,
                                 headers=headers(key))
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8", "ignore")
            return json.loads(raw) if raw.strip() else []
    except urllib.error.HTTPError as e:
        sys.exit(f"Supabase {e.code}: {e.read().decode('utf-8', 'ignore')[:400]}")
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach Supabase: {e.reason}")


# ------------------------------------------------------------------ helpers
def money(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return f"${int(digits):,}" if digits else None


def parse_when(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt(dt: str | None) -> str:
    if not dt:
        return "—"
    return parse_when(dt).astimezone().strftime("%Y-%m-%d %H:%M")


def days_left(ends_at: str) -> int:
    return max(0, (parse_when(ends_at) - datetime.now(timezone.utc)).days)


# ------------------------------------------------------------------ commands
def cmd_add(args) -> None:
    if not re.match(r"^https?://", args.url, re.I):
        sys.exit("--url must start with http:// or https://")
    if args.logo and not re.match(r"^https?://", args.logo, re.I):
        sys.exit("--logo must start with http:// or https://")
    if args.category not in CATEGORIES:
        sys.exit(f"--category must be one of: {', '.join(CATEGORIES)}")
    if args.region not in REGIONS:
        sys.exit(f"--region must be one of: {', '.join(REGIONS)}")
    if args.level not in LEVELS:
        sys.exit(f"--level must be one of: {', '.join(LEVELS)}")
    if not 1 <= args.days <= 365:
        sys.exit("--days must be between 1 and 365")

    starts = parse_when(args.starts) if args.starts else datetime.now(timezone.utc)
    payload = {
        "title": args.title.strip()[:180],
        "company": args.company.strip()[:80],
        "url": args.url.strip(),
        "logo_url": (args.logo or None),
        "blurb": (args.blurb or None),
        "salary_min": money(args.salary_min),
        "salary_max": money(args.salary_max),
        "region": args.region,
        "category": args.category,
        "level": args.level,
        "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()][:6],
        "weight": args.weight,
        "monthly_price": args.price,
        "contact_email": (args.email or None),
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(days=args.days)).isoformat(),
        "active": True,
    }

    if args.dry_run:
        print("DRY RUN — nothing written. Payload:\n")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    url, key = load_env()
    created = request(url, key, "POST", payload)
    row: dict = created[0] if isinstance(created, list) and created else (created or {})
    print("Sponsored listing created:\n")
    print(f"  id       {row.get('id')}")
    print(f"  pinned   {row['title']} @ {row['company']}")
    print(f"  runs     {fmt(row['starts_at'])} → {fmt(row['ends_at'])}  ({args.days} days)")
    print(f"  price    ${row.get('monthly_price')}/mo")
    print(f"\nIt appears at the top of /jobs when the visitor's filters match it.")
    print(f"Remove it early:  py tools/sponsor.py rm {row.get('id')}")


def cmd_list(args) -> None:
    url, key = load_env()
    query = "?select=id,title,company,url,region,category,weight,active,starts_at,ends_at,impressions,clicks,monthly_price&order=created_at.desc&limit=100"
    if not args.all:
        query += "&active=eq.true&ends_at=gt." + datetime.now(timezone.utc).isoformat()
    rows = request(url, key, "GET", query=query)
    if not rows:
        print("No sponsored listings" + ("" if args.all else " live right now") + ".")
        return

    print(f"{len(rows)} listing(s):\n")
    for r in rows:
        state = "LIVE" if r.get("active") and days_left(r["ends_at"]) > 0 else "off "
        print(f"  [{state}] {r['company']} — {r['title']}")
        print(f"          {r['id']}")
        print(f"          {r['region']}/{r['category']} · weight {r['weight']} · ${r['monthly_price']}/mo"
              f" · {days_left(r['ends_at'])}d left · 👁 {r.get('impressions', 0)} · 🖱 {r.get('clicks', 0)}")
        print(f"          until {fmt(r['ends_at'])} · {r['url']}")
        print()


def cmd_extend(args) -> None:
    url, key = load_env()
    rows = request(url, key, "GET", query=f"?select=id,ends_at&id=eq.{args.id}")
    if not rows:
        sys.exit(f"No listing with id {args.id}")
    base = parse_when(rows[0]["ends_at"])
    if base < datetime.now(timezone.utc):
        base = datetime.now(timezone.utc)
    new_end = base + timedelta(days=args.days)
    request(url, key, "PATCH", {"ends_at": new_end.isoformat(), "active": True},
            query=f"?id=eq.{args.id}")
    print(f"Extended {args.id} → {fmt(new_end.isoformat())} ({days_left(new_end.isoformat())} days left)")


def cmd_pause(args) -> None:
    url, key = load_env()
    request(url, key, "PATCH", {"active": False}, query=f"?id=eq.{args.id}")
    print(f"Paused {args.id} — it stops appearing immediately, the row is kept.")


def cmd_resume(args) -> None:
    url, key = load_env()
    request(url, key, "PATCH", {"active": True}, query=f"?id=eq.{args.id}")
    print(f"Resumed {args.id}.")


def cmd_rm(args) -> None:
    url, key = load_env()
    request(url, key, "DELETE", query=f"?id=eq.{args.id}")
    print(f"Deleted {args.id}.")


def cmd_stats(args) -> None:
    url, key = load_env()
    query = "?select=title,company,impressions,clicks,starts_at,ends_at,monthly_price&order=impressions.desc"
    rows = request(url, key, "GET", query=query)
    if not rows:
        print("Nothing to report yet.")
        return
    total_views = sum(r.get("impressions", 0) for r in rows)
    total_clicks = sum(r.get("clicks", 0) for r in rows)
    print(f"{len(rows)} listing(s) · {total_views} impressions · {total_clicks} clicks"
          f" · CTR {(total_clicks / total_views * 100) if total_views else 0:.1f}%\n")
    for r in rows:
        views = r.get("impressions", 0)
        clicks = r.get("clicks", 0)
        ctr = (clicks / views * 100) if views else 0
        print(f"  {r['company']:<22} {views:>6} views  {clicks:>5} clicks  CTR {ctr:>5.1f}%  "
              f"{'expired' if days_left(r['ends_at']) == 0 else str(days_left(r['ends_at'])) + 'd left'}")


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser(description="Manage RadarJobs sponsored (pinned) listings.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="create a pinned listing")
    add.add_argument("--title", required=True)
    add.add_argument("--company", required=True)
    add.add_argument("--url", required=True, help="link to the original posting")
    add.add_argument("--logo", help="https://…square logo (optional)")
    add.add_argument("--blurb", help="one line shown under the title")
    add.add_argument("--salary-min")
    add.add_argument("--salary-max")
    add.add_argument("--region", default="worldwide", choices=REGIONS)
    add.add_argument("--category", default="other", choices=CATEGORIES)
    add.add_argument("--level", default="mid", choices=LEVELS)
    add.add_argument("--tags", help="comma separated, max 6")
    add.add_argument("--weight", type=int, default=100, help="higher pins above other sponsors")
    add.add_argument("--price", type=float, default=30.0, help="what they pay per month")
    add.add_argument("--email", help="billing contact")
    add.add_argument("--days", type=int, default=30)
    add.add_argument("--starts", help="ISO date, defaults to now")
    add.add_argument("--dry-run", action="store_true")
    add.set_defaults(func=cmd_add)

    lst = sub.add_parser("list", help="show listings")
    lst.add_argument("--all", action="store_true", help="include paused and expired")
    lst.set_defaults(func=cmd_list)

    ext = sub.add_parser("extend", help="add days to a listing")
    ext.add_argument("id")
    ext.add_argument("--days", type=int, default=30)
    ext.set_defaults(func=cmd_extend)

    for name, fn, label in (("pause", cmd_pause, "hide a listing without deleting it"),
                            ("resume", cmd_resume, "show it again"),
                            ("rm", cmd_rm, "delete it")):
        p = sub.add_parser(name, help=label)
        p.add_argument("id")
        p.set_defaults(func=fn)

    st = sub.add_parser("stats", help="impressions and clicks")
    st.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
