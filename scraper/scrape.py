#!/usr/bin/env python3
"""
RadarJobs scraper — pulls remote roles from public ATS APIs.

Sources (no auth needed):
  - Greenhouse  https://boards-api.greenhouse.io/v1/boards/<token>/jobs
  - Ashby       https://api.ashbyhq.com/posting-api/job-board/<token>

Output: scraper/out/jobs.json
Optional: upserts into Supabase when SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are set.

Usage:
    python scraper/scrape.py              # scrape + write JSON
    python scraper/scrape.py --push       # scrape + write JSON + upsert to Supabase
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------- boards
GREENHOUSE_BOARDS = [
    "gitlab", "stripe", "coinbase", "affirm", "twilio", "instacart",
    "reddit", "datadog", "vercel", "discord", "dropbox", "airtable",
]
ASHBY_BOARDS = ["openai", "vanta", "posthog", "ramp"]

UA = {"User-Agent": "RadarJobs/1.0 (+https://jobscrapping.netlify.app)"}
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


# ---------------------------------------------------------------- helpers
def fetch_json(url, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    print(f"    ! {url} -> {type(last).__name__}: {last}", file=sys.stderr)
    return None


def clean_text(raw, limit=400):
    if not raw:
        return ""
    txt = html.unescape(html.unescape(raw))
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&nbsp;", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()[:limit]


def infer_region(location, is_remote=None, workplace=None):
    loc = (location or "").lower()
    wp = (workplace or "").lower()
    remote = bool(is_remote) or "remote" in loc or wp in ("remote", "hybrid")

    latam = r"latam|latin america|brazil|brasil|argentina|mexic|colombia|chile|peru|uruguay|paraguay|ecuador|venezuela|centroamerica"
    eu = r"emea|europe|europa|\beu\b|germany|deutschland|france|united kingdom|\buk\b|spain|portugal|netherlands|poland|ireland|italy|sweden|denmark|norway|finland|austria|belgium|switzerland|czech"
    america = r"united states|\bu\.?s\.?a?\b|america|canada|north america|new york|san francisco|seattle|austin|boston|chicago|toronto"

    if re.search(latam, loc):
        return "latam"
    if re.search(eu, loc):
        return "eu"
    if re.search(america, loc):
        return "america"
    if remote:
        return "worldwide"
    return "worldwide"


SALARY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3})\s?(?:k)?\s*(?:-|–|—|to)\s*\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3})\s?(?:k)?",
    re.I,
)


def extract_salary(text):
    """Best-effort salary range from the job body. Returns (min, max) as strings or (None, None)."""
    if not text:
        return None, None
    m = SALARY_RE.search(text)
    if not m:
        return None, None

    def norm(v):
        v = v.replace(",", "")
        n = int(v)
        if n < 1000:  # "120" means 120k
            n *= 1000
        return n

    lo, hi = norm(m.group(1)), norm(m.group(2))
    if lo < 20000 or hi < lo or hi > 2_000_000:
        return None, None
    return f"${lo:,}", f"${hi:,}"


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- normalizers
def from_greenhouse(job, board):
    loc = (job.get("location") or {}).get("name") or ""
    body = clean_text(job.get("content"), 4000)
    smin, smax = extract_salary(body)

    tags = [d.get("name") for d in (job.get("departments") or []) if d.get("name")][:4]
    for meta in (job.get("metadata") or [])[:3]:
        if meta.get("value") and isinstance(meta["value"], str):
            tags.append(f"{meta['name']}: {meta['value']}"[:40])

    return {
        "title": (job.get("title") or "").strip(),
        "company": (job.get("company_name") or board).strip(),
        "salary_min": smin,
        "salary_max": smax,
        "url": job.get("absolute_url") or "",
        "source": "greenhouse",
        "tags": tags,
        "posted_at": parse_date(job.get("first_published") or job.get("updated_at")),
        "region": infer_region(loc),
        "_location": loc,
        "_body": body[:600],
    }


def from_ashby(job, board):
    loc = job.get("location") or ""
    body = clean_text(job.get("descriptionPlain") or job.get("descriptionHtml"), 4000)
    smin, smax = extract_salary(body)

    tags = []
    for key in ("department", "team", "employmentType"):
        if job.get(key):
            tags.append(str(job[key]))

    return {
        "title": (job.get("title") or "").strip(),
        "company": board,
        "salary_min": smin,
        "salary_max": smax,
        "url": job.get("jobUrl") or job.get("applyUrl") or "",
        "source": "ashby",
        "tags": tags[:4],
        "posted_at": parse_date(job.get("publishedAt")),
        "region": infer_region(loc, job.get("isRemote"), job.get("workplaceType")),
        "_location": loc,
        "_body": body[:600],
    }


def is_remote(job):
    blob = f"{job.get('_location','')} {job.get('_body','')}".lower()
    return "remote" in blob or "anywhere" in blob


# ---------------------------------------------------------------- scrape
def scrape():
    jobs, seen = [], set()
    stats = []

    for board in GREENHOUSE_BOARDS:
        data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
        raw = (data or {}).get("jobs") or []
        kept = 0
        for j in raw:
            item = from_greenhouse(j, board)
            if not item["title"] or not item["url"] or item["url"] in seen:
                continue
            if not is_remote(item):
                continue
            seen.add(item["url"])
            jobs.append(item)
            kept += 1
        stats.append(("greenhouse", board, len(raw), kept))
        print(f"  greenhouse/{board:12} fetched={len(raw):>4} kept={kept:>4}")

    for board in ASHBY_BOARDS:
        data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        raw = (data or {}).get("jobs") or []
        kept = 0
        for j in raw:
            item = from_ashby(j, board)
            if not item["title"] or not item["url"] or item["url"] in seen:
                continue
            if not (j.get("isRemote") or is_remote(item)):
                continue
            seen.add(item["url"])
            jobs.append(item)
            kept += 1
        stats.append(("ashby", board, len(raw), kept))
        print(f"  ashby/{board:17} fetched={len(raw):>4} kept={kept:>4}")

    jobs.sort(key=lambda x: x.get("posted_at") or "", reverse=True)
    return jobs, stats


# ---------------------------------------------------------------- supabase
def push_to_supabase(jobs):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("\n[push] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — skipping upload.")
        return 0

    endpoint = f"{url}/rest/v1/jobs?on_conflict=url"
    sent = 0
    batch = 100
    for i in range(0, len(jobs), batch):
        chunk = [
            {k: v for k, v in j.items() if not k.startswith("_")}
            for j in jobs[i : i + batch]
        ]
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(chunk).encode(),
            method="POST",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                sent += len(chunk)
                print(f"  [push] batch {i//batch + 1}: {len(chunk)} rows -> HTTP {r.status}")
        except urllib.error.HTTPError as e:
            print(f"  [push] batch {i//batch + 1} FAILED {e.code}: {e.read().decode()[:200]}")
        except Exception as e:  # noqa: BLE001
            print(f"  [push] batch {i//batch + 1} FAILED: {e}")
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="upsert into Supabase after scraping")
    args = ap.parse_args()

    print(f"RadarJobs scraper — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n")
    jobs, stats = scrape()

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "jobs.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=1)

    # Slim feed consumed by the site (served statically by Netlify).
    keep = ["title", "company", "salary_min", "salary_max", "url", "source", "tags", "posted_at", "region"]
    slim = [{k: j.get(k) for k in keep} for j in jobs]
    slim.sort(key=lambda x: x.get("posted_at") or "", reverse=True)
    feed_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jobs.json")
    with open(feed_path, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  feed: {feed_path} ({len(slim)} jobs)")

    with_salary = sum(1 for j in jobs if j.get("salary_min"))
    print(f"\nTOTAL remote jobs: {len(jobs)}")
    print(f"  con salario detectado: {with_salary}")
    print(f"  por region: ", {r: sum(1 for j in jobs if j['region'] == r) for r in ("worldwide", "latam", "america", "eu")})
    print(f"  escrito: {path}")

    if args.push:
        print("\nSubiendo a Supabase…")
        n = push_to_supabase(jobs)
        print(f"  enviados: {n}")


if __name__ == "__main__":
    main()
