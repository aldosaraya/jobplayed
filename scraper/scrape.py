#!/usr/bin/env python3
"""
RadarJobs scraper — pulls remote roles from public ATS APIs and job aggregators.

Sources (all public, no auth):
  ATS         Greenhouse    boards-api.greenhouse.io/v1/boards/<token>/jobs
              Ashby         api.ashbyhq.com/posting-api/job-board/<token>
  Aggregators Jobicy        jobicy.com/api/v2/remote-jobs
              Arbeitnow     arbeitnow.com/api/job-board-api
              RemoteOK      remoteok.com/api
              WorkingNomads workingnomads.com/api/exposed_jobs
              Remotive      remotive.com/api/remote-jobs?category=<cat>
              Himalayas     himalayas.app/jobs/api

Output:
  scraper/out/jobs.json   full records (gitignored)
  jobs.json               slim feed served by Netlify

Usage:
    python scraper/scrape.py
    python scraper/scrape.py --push     # also upsert into Supabase (needs service role key)
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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "RadarJobs/1.0 (+https://jobscrapping.netlify.app)", "Accept": "application/json"}
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------ boards
GREENHOUSE_BOARDS = [
    "gitlab", "stripe", "coinbase", "affirm", "twilio", "instacart",
    "reddit", "datadog", "vercel", "discord", "dropbox", "airtable",
    # support / ops heavy, hire globally
    "wolt", "canonical", "payoneer", "intercom", "monzo", "wise",
    "upwork", "turing", "typeform", "elastic", "n26",
]
ASHBY_BOARDS = ["openai", "vanta", "posthog", "ramp", "linear", "zapier"]

REMOTIVE_CATEGORIES = [
    "customer-support", "data", "design", "marketing", "sales",
    "product", "software-dev", "all-others", "finance-legal", "hr",
]
ARBEITNOW_PAGES = 4

# We Work Remotely publishes per-category RSS feeds.
WWR_CATEGORIES = [
    "remote-customer-support-jobs",
    "remote-sales-and-marketing-jobs",
    "remote-programming-jobs",
    "remote-design-jobs",
    "remote-devops-sysadmin-jobs",
    "remote-management-and-finance-jobs",
    "remote-product-jobs",
]


# ------------------------------------------------------------------ helpers
def fetch_json(url, retries=2, timeout=30):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
    print(f"    ! {url} -> {type(last).__name__}: {last}", file=sys.stderr)
    return None


def fetch_bytes(url, retries=2, timeout=30):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
    print(f"    ! {url} -> {type(last).__name__}: {last}", file=sys.stderr)
    return None


def clean_text(raw, limit=4000):
    if not raw:
        return ""
    txt = html.unescape(html.unescape(str(raw)))
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&nbsp;", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()[:limit]


def parse_date(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ classification
LATAM = r"latam|latin america|brazil|brasil|argentina|mexic|colombia|chile|peru|uruguay|paraguay|ecuador|venezuela|centroamerica|caribbean"
EU = r"emea|europe|europa|\beu\b|germany|deutschland|france|united kingdom|\buk\b|spain|portugal|netherlands|poland|ireland|italy|sweden|denmark|norway|finland|austria|belgium|switzerland|czech|romania|ukraine"
AMERICA = r"united states|\bu\.?s\.?a?\b|america|canada|north america|new york|san francisco|seattle|austin|boston|chicago|toronto"
AFRICA = r"africa|nigeria|kenya|south africa|egypt|morocco|ghana|uganda|tanzania|rwanda|senegal"
ASIA = r"india|pakistan|bangladesh|philippines|indonesia|vietnam|thailand|malaysia|singapore|japan|korea|china|sri lanka|nepal"


def infer_region(*parts):
    """Maps free-form location text to the feed's region buckets."""
    blob = " ".join(str(p) for p in parts if p).lower()

    if re.search(r"worldwide|anywhere|global|100% remote|fully remote", blob):
        if not re.search(LATAM + "|" + EU + "|" + AMERICA, blob):
            return "worldwide"

    if re.search(LATAM, blob):
        return "latam"
    if re.search(AFRICA, blob) or re.search(ASIA, blob):
        return "worldwide"
    if re.search(EU, blob):
        return "eu"
    if re.search(AMERICA, blob):
        return "america"
    return "worldwide"


def categorize(title):
    t = (title or "").lower()

    if re.search(r"data entry|data-entry|transcri|annotat|labell?ing|data quality", t):
        return "data-entry"
    if re.search(r"virtual assistant|executive assistant|admin(istrative)?|back ?office|moderat|trust (and|&) safety|content review|operations associate", t):
        return "admin"
    if re.search(r"customer (support|service|care|experience)|support (agent|specialist|representative|rep|associate|engineer|advocate)|help ?desk|call cent|bpo|customer success|technical support", t):
        return "support"
    if re.search(r"writer|copywrit|content (writer|editor|specialist)|editor|translator|locali[sz]|proofread|documentation", t):
        return "writing"
    if re.search(r"marketing|seo|growth|social media|community|brand|demand gen|paid media", t):
        return "marketing"
    if re.search(r"machine learning|\bml\b|\bai\b|llm|deep learning|nlp|computer vision", t):
        return "ai-ml"
    if re.search(r"data scien|data engineer|analytics|business intelligence|\bbi\b|statistic", t):
        return "data"
    if re.search(r"design|ux|\bui\b|creative|figma", t):
        return "design"
    if re.search(r"product (manager|owner|lead)|product management", t):
        return "product"
    if re.search(r"sales|account executive|account manager|revenue|partnership|business development|\bbdr\b|\bsdr\b", t):
        return "sales"
    # operations / people / project — roles an international candidate can realistically land
    if re.search(r"\bops\b|operations|logistics|supply chain|payment ops|gtm systems|process specialist|coordinator", t):
        return "admin"
    if re.search(r"people (ops|operations|partner)|recruit|talent|human resources|\bhr\b|sourcer", t):
        return "admin"
    if re.search(r"project manager|program manager|scrum|delivery manager|technical program", t):
        return "product"
    if re.search(r"investigator|fraud|\brisk\b|\baml\b|\bkyc\b|underwrit", t):
        return "finance"
    if re.search(r"finance|accounting|tax|payroll|legal|counsel|compliance|audit|bookkeep|controller", t):
        return "finance"
    if re.search(r"devops|\bsre\b|reliability|infrastructure|platform|security|cloud|kubernetes", t):
        return "devops"
    if re.search(r"engineer|developer|software|backend|frontend|full.?stack|architect|programmer", t):
        return "engineering"
    return "other"


def level_of(title, hint=None):
    h = (hint or "").lower()
    if "entry" in h or "intern" in h:
        return "entry"
    if "junior" in h:
        return "junior"
    if "senior" in h:
        return "senior"
    if any(k in h for k in ("lead", "principal", "director", "executive")):
        return "lead"

    t = (title or "").lower()
    if re.search(r"\b(intern|internship|trainee|graduate|entry.level|no experience|apprentice)\b", t):
        return "entry"
    if re.search(r"\bjunior\b|\bjr\.?\b", t):
        return "junior"
    if re.search(r"\b(staff|principal|lead|head|director|vp|chief)\b", t):
        return "lead"
    if re.search(r"\bsenior\b|\bsr\.?\b", t):
        return "senior"
    return "mid"


SALARY_RE = re.compile(
    r"(?:\$|USD\s?)\s?(\d{2,3}(?:[,\s]\d{3})+|\d{2,3})\s?k?\s*(?:-|–|—|to)\s*(?:\$|USD\s?)?\s?(\d{2,3}(?:[,\s]\d{3})+|\d{2,3})\s?k?",
    re.I,
)


def extract_salary(text):
    if not text:
        return None, None
    m = SALARY_RE.search(text)
    if not m:
        return None, None

    def norm(v):
        v = re.sub(r"[,\s]", "", v)
        n = int(v)
        if n < 1000:
            n *= 1000
        return n

    lo, hi = norm(m.group(1)), norm(m.group(2))
    if lo < 12000 or hi < lo or hi > 2_000_000:
        return None, None
    return f"${lo:,}", f"${hi:,}"


def money(v):
    """Normaliza salarios numéricos de las APIs (remoteok / himalayas)."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    if n <= 0 or n > 2_000_000:
        return None
    if n < 1000:
        n *= 1000
    return f"${n:,}"


def build(title, company, url, source, region, tags=None, posted=None, body="", salary_hint=None, level_hint=None):
    smin = smax = None
    if isinstance(salary_hint, tuple):
        smin, smax = salary_hint
    if not smin:
        smin, smax = extract_salary(body)

    # Algunas fuentes entregan tags como string ("a, b, c") y otras como lista.
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    tags = [t for t in (tags or []) if t]

    return {
        "title": (title or "").strip()[:180],
        "company": (company or "").strip()[:80],
        "salary_min": smin,
        "salary_max": smax,
        "url": (url or "").strip(),
        "source": source,
        "tags": [str(t)[:32] for t in tags][:4],
        "posted_at": posted,
        "region": region,
        "category": categorize(title),
        "level": level_of(title, level_hint),
        "_body": clean_text(body, 600),
    }


# ------------------------------------------------------------------ normalizers
def from_greenhouse(job, board):
    loc = (job.get("location") or {}).get("name") or ""
    body = clean_text(job.get("content"))
    tags = [d.get("name") for d in (job.get("departments") or []) if d.get("name")]
    return build(
        job.get("title"),
        job.get("company_name") or board,
        job.get("absolute_url"),
        "greenhouse",
        infer_region(loc),
        tags,
        parse_date(job.get("first_published") or job.get("updated_at")),
        body,
    )


def from_ashby(job, board):
    loc = job.get("location") or ""
    body = clean_text(job.get("descriptionPlain") or job.get("descriptionHtml"))
    tags = [job.get(k) for k in ("department", "team", "employmentType") if job.get(k)]
    return build(
        job.get("title"),
        board,
        job.get("jobUrl") or job.get("applyUrl"),
        "ashby",
        infer_region(loc, job.get("workplaceType")),
        tags,
        parse_date(job.get("publishedAt")),
        body,
    )


def from_jobicy(item):
    return build(
        item.get("jobTitle"),
        item.get("companyName"),
        item.get("url"),
        "jobicy",
        infer_region(item.get("jobGeo")),
        [item.get("jobIndustry"), item.get("jobType")],
        parse_date(item.get("pubDate")),
        item.get("jobDescription") or item.get("jobExcerpt"),
        level_hint=item.get("jobLevel"),
    )


def from_arbeitnow(item):
    if not item.get("remote"):
        return None
    return build(
        item.get("title"),
        item.get("company_name"),
        item.get("url"),
        "arbeitnow",
        infer_region(item.get("location")),
        item.get("tags") or item.get("job_types"),
        parse_date(item.get("created_at")),
        item.get("description"),
    )


def from_remoteok(item):
    return build(
        item.get("position"),
        item.get("company"),
        item.get("apply_url") or item.get("url"),
        "remoteok",
        infer_region(item.get("location")),
        item.get("tags"),
        parse_date(item.get("date") or item.get("epoch")),
        item.get("description"),
        salary_hint=(money(item.get("salary_min")), money(item.get("salary_max"))),
    )


def from_workingnomads(item):
    tags = item.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return build(
        item.get("title"),
        item.get("company_name"),
        item.get("url"),
        "workingnomads",
        infer_region(item.get("location")),
        [item.get("category_name")] + list(tags or [])[:3],
        parse_date(item.get("pub_date")),
        item.get("description"),
    )


def from_remotive(item):
    return build(
        item.get("title"),
        item.get("company_name"),
        item.get("url"),
        "remotive",
        infer_region(item.get("candidate_required_location")),
        [item.get("category"), item.get("job_type")],
        parse_date(item.get("publication_date")),
        item.get("description"),
    )


def from_himalayas(item):
    return build(
        item.get("title"),
        item.get("companyName"),
        item.get("applicationLink") or item.get("guid"),
        "himalayas",
        infer_region(item.get("locationRestrictions")),
        (item.get("categories") or [])[:4],
        parse_date(item.get("pubDate") or item.get("expiryDate")),
        item.get("description") or item.get("excerpt"),
        salary_hint=(money(item.get("minSalary")), money(item.get("maxSalary"))),
    )


def from_wwr(item):
    """We Work Remotely RSS item. Titles look like 'Company: Job Title'."""
    title = item.findtext("title") or ""
    company, _, role = title.partition(": ")
    if not role:
        company, role = "", title

    skills = item.findtext("skills") or ""
    tags = [item.findtext("category")] + [s.strip() for s in skills.split(",") if s.strip()][:3]

    posted = None
    pub = item.findtext("pubDate")
    if pub:
        try:
            posted = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            posted = None

    return build(
        role,
        company,
        item.findtext("link"),
        "weworkremotely",
        infer_region(item.findtext("region"), item.findtext("country")),
        tags,
        posted,
        item.findtext("description"),
    )


def is_remote(item):
    blob = f"{item.get('_body','')} {' '.join(item.get('tags') or [])}".lower()
    return "remote" in blob or "anywhere" in blob or item.get("region") == "worldwide"


# ------------------------------------------------------------------ collect
def collect():
    jobs, seen, stats = [], set(), []

    def add(item):
        if not item or not item["title"] or not item["url"]:
            return 0
        if item["url"] in seen:
            return 0
        seen.add(item["url"])
        jobs.append(item)
        return 1

    for board in GREENHOUSE_BOARDS:
        data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
        raw = (data or {}).get("jobs") or []
        kept = 0
        for j in raw:
            item = from_greenhouse(j, board)
            if is_remote(item):
                kept += add(item)
        stats.append(("greenhouse", board, len(raw), kept))
        print(f"  greenhouse/{board:12} fetched={len(raw):>4} kept={kept:>4}")

    for board in ASHBY_BOARDS:
        data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        raw = (data or {}).get("jobs") or []
        kept = 0
        for j in raw:
            item = from_ashby(j, board)
            if j.get("isRemote") or is_remote(item):
                kept += add(item)
        stats.append(("ashby", board, len(raw), kept))
        print(f"  ashby/{board:17} fetched={len(raw):>4} kept={kept:>4}")

    # ---- aggregators
    for name, url, key, fn in [
        ("jobicy", "https://jobicy.com/api/v2/remote-jobs?count=100", "jobs", from_jobicy),
        ("remoteok", "https://remoteok.com/api", None, from_remoteok),
        ("workingnomads", "https://www.workingnomads.com/api/exposed_jobs/", "jobs", from_workingnomads),
        ("himalayas", "https://himalayas.app/jobs/api?limit=100", "jobs", from_himalayas),
    ]:
        data = fetch_json(url)
        if isinstance(data, dict):
            raw = data.get(key) or []
        elif isinstance(data, list):
            raw = [x for x in data if isinstance(x, dict) and "legal" not in str(x)[:40].lower()]
        else:
            raw = []
        kept = sum(add(fn(x)) for x in raw if isinstance(x, dict))
        stats.append((name, "-", len(raw), kept))
        print(f"  {name:26} fetched={len(raw):>4} kept={kept:>4}")
        time.sleep(0.6)

    for page in range(1, ARBEITNOW_PAGES + 1):
        data = fetch_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        raw = (data or {}).get("data") or []
        kept = sum(add(from_arbeitnow(x)) for x in raw if isinstance(x, dict))
        stats.append(("arbeitnow", f"p{page}", len(raw), kept))
        print(f"  arbeitnow/page{page:<2}         fetched={len(raw):>4} kept={kept:>4}")
        time.sleep(0.6)

    for cat in REMOTIVE_CATEGORIES:
        data = fetch_json(f"https://remotive.com/api/remote-jobs?category={cat}")
        raw = (data or {}).get("jobs") or []
        kept = sum(add(from_remotive(x)) for x in raw if isinstance(x, dict))
        stats.append(("remotive", cat, len(raw), kept))
        print(f"  remotive/{cat:17} fetched={len(raw):>4} kept={kept:>4}")
        time.sleep(0.6)

    for cat in WWR_CATEGORIES:
        blob = fetch_bytes(f"https://weworkremotely.com/categories/{cat}.rss")
        raw = []
        if blob:
            try:
                raw = ET.fromstring(blob).findall(".//item")
            except Exception as e:  # noqa: BLE001
                print(f"    ! WWR parse {cat}: {e}", file=sys.stderr)
        kept = sum(add(from_wwr(x)) for x in raw)
        stats.append(("weworkremotely", cat, len(raw), kept))
        print(f"  wwr/{cat:32} fetched={len(raw):>4} kept={kept:>4}")
        time.sleep(0.6)

    jobs.sort(key=lambda x: x.get("posted_at") or "", reverse=True)
    return jobs, stats


# ------------------------------------------------------------------ output
def write_outputs(jobs):
    os.makedirs(OUT_DIR, exist_ok=True)
    full = os.path.join(OUT_DIR, "jobs.json")
    with open(full, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=1)

    keep = ["title", "company", "salary_min", "salary_max", "url", "source",
            "tags", "posted_at", "region", "category", "level"]
    slim = [{k: j.get(k) for k in keep} for j in jobs]
    slim.sort(key=lambda x: x.get("posted_at") or "", reverse=True)
    feed = os.path.join(ROOT, "jobs.json")
    with open(feed, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))
    return full, feed, slim


def push_to_supabase(jobs):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("\n[push] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — skipping.")
        return 0

    sent = 0
    for i in range(0, len(jobs), 100):
        chunk = [{k: v for k, v in j.items() if not k.startswith("_")} for j in jobs[i:i + 100]]
        req = urllib.request.Request(
            f"{url}/rest/v1/jobs?on_conflict=url",
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
                print(f"  [push] batch {i//100 + 1}: {len(chunk)} -> HTTP {r.status}")
        except urllib.error.HTTPError as e:
            print(f"  [push] batch {i//100 + 1} FAILED {e.code}: {e.read().decode()[:180]}")
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="upsert into Supabase after scraping")
    args = ap.parse_args()

    print(f"RadarJobs scraper — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n")
    jobs, stats = collect()
    full, feed, slim = write_outputs(jobs)

    by_cat, by_level, by_region = {}, {}, {}
    for j in jobs:
        by_cat[j["category"]] = by_cat.get(j["category"], 0) + 1
        by_level[j["level"]] = by_level.get(j["level"], 0) + 1
        by_region[j["region"]] = by_region.get(j["region"], 0) + 1

    print(f"\nTOTAL unique remote jobs: {len(jobs)}")
    print(f"  con salario: {sum(1 for j in jobs if j['salary_min'])}")
    print(f"  por categoria: {dict(sorted(by_cat.items(), key=lambda x: -x[1]))}")
    print(f"  por nivel:     {by_level}")
    print(f"  por region:    {by_region}")
    print(f"  full: {full}\n  feed: {feed}")

    if args.push:
        print("\nSubiendo a Supabase…")
        print("  enviados:", push_to_supabase(jobs))


if __name__ == "__main__":
    main()
