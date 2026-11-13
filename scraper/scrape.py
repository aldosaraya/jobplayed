#!/usr/bin/env python3
"""
RadarJobs scraper — remote roles from public ATS APIs, aggregators and RSS feeds.

Sources (all public, no API key):
  ATS          Greenhouse     boards-api.greenhouse.io/v1/boards/<token>/jobs
               Ashby          api.ashbyhq.com/posting-api/job-board/<token>
               Lever          api.lever.co/v0/postings/<company>?mode=json
  Aggregators  remotejobs.org  remotejobs.org/api/v1/jobs          (~21k listings)
               The Muse        themuse.com/api/public/jobs
               Himalayas       himalayas.app/jobs/api
               Jobicy          jobicy.com/api/v2/remote-jobs
               RemoteOK        remoteok.com/api
               Arbeitnow       arbeitnow.com/api/job-board-api
               Remotive        remotive.com/api/remote-jobs
               4dayweek.io     4dayweek.io/api/jobs
               Working Nomads  workingnomads.com/api/exposed_jobs
  RSS          We Work Remotely  8 category feeds + global feed
               NoDesk             nodesk.co/remote-jobs/index.xml

Ghost detection: a posting older than GHOST_DAYS (30) is flagged `ghost: true`;
anything older than DROP_DAYS (90) is discarded as dead.

Output:
  scraper/out/jobs.json   full records (gitignored)
  jobs.json               slim feed served by Netlify

Usage:
    python scraper/scrape.py
    python scraper/scrape.py --push     # also upsert into Supabase (service role key)
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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; RadarJobs/1.0; +https://jobscrapping.netlify.app)",
    "Accept": "application/json, application/xml, text/xml, */*",
}
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GHOST_DAYS = 30
DROP_DAYS = 90

# ------------------------------------------------------------------ config
GREENHOUSE_BOARDS = [
    "gitlab", "stripe", "coinbase", "affirm", "twilio", "instacart",
    "reddit", "datadog", "vercel", "discord", "dropbox", "airtable",
    "wolt", "canonical", "payoneer", "intercom", "monzo", "wise",
    "upwork", "turing", "typeform", "elastic", "n26",
    # US-heavy (2026-11, verificados vivos)
    "figma", "databricks", "cloudflare", "okta", "robinhood", "lyft",
    "airbnb", "pinterest", "flexport", "handshake", "duolingo",
    "block", "twitch", "zscaler",
]
ASHBY_BOARDS = ["openai", "vanta", "posthog", "ramp", "linear", "zapier",
                "deel", "cursor", "sardine", "mercury"]
LEVER_COMPANIES = ["spotify", "toptal", "palantir"]
SMARTRECRUITERS_COMPANIES = ["wise", "deliveryhero"]
RECRUITEE_COMPANIES = ["bunq"]
NODESK_FEEDS = ["https://nodesk.co/remote-jobs/index.xml"]

# Public RSS job feeds — no key, no HTML scraping. Probed live 2026-11.
RSS_JOB_FEEDS = [
    ("jobspresso", "https://jobspresso.co/feed/?post_type=job_listing"),
    ("djangojobs", "https://djangojobs.net/jobs/feed/"),
    ("pythonjobs", "https://www.python.org/jobs/feed/rss/"),
]


REMOTEJOBS_PAGES = 30          # 50 per page
THEMUSE_PAGES = 15             # 20 per page, location=Remote
HIMALAYAS_PAGES = 15           # 20 per page via offset
FOURDAYWEEK_PAGES = 4          # 25 per page
ARBEITNOW_PAGES = 4

REMOTIVE_CATEGORIES = [
    "customer-support", "data", "design", "marketing", "sales",
    "product", "software-dev", "all-others", "finance-legal", "hr",
]

WWR_FEEDS = [
    "https://weworkremotely.com/remote-jobs.rss",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "https://weworkremotely.com/categories/all-other-remote-jobs.rss",
]

# API category -> our category
CATEGORY_HINTS = {
    "customer support": "support", "customer service": "support", "support": "support",
    "programming": "engineering", "software development": "engineering", "engineering": "engineering",
    "data science": "data", "data": "data", "devops": "devops", "cyber security": "devops",
    "design": "design", "product management": "product", "product": "product",
    "sales": "sales", "marketing": "marketing", "finance": "finance", "legal": "finance",
    "human resources": "admin", "admin": "admin", "writing": "writing",
    "data entry": "data-entry", "dental": "other", "general": None,
}


# ------------------------------------------------------------------ http
def _request(url, retries=2, timeout=30):
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


def fetch_json(url):
    blob = _request(url)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:  # noqa: BLE001
        return None


def fetch_xml(url):
    blob = _request(url)
    if not blob:
        return []
    txt = blob.decode("utf-8", "ignore")
    # tolerate undefined HTML entities (&nbsp; etc.) that break ElementTree
    txt = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", txt)
    try:
        return ET.fromstring(txt).findall(".//item")
    except Exception as e:  # noqa: BLE001
        print(f"    ! xml parse {url}: {e}", file=sys.stderr)
        return []


# ------------------------------------------------------------------ helpers
def fetch_feed_items(url):
    """RSS/Atom items as plain dicts.

    Regex over <item> blocks on purpose: these feeds ship CDATA bodies and
    namespaced tags (job:company, job:location) that ElementTree either drops
    or returns as '{ns}tag'. We only need a handful of fields, so a tolerant
    parse beats a namespace-aware one.
    """
    blob = _request(url)
    if not blob:
        return []
    txt = blob.decode("utf-8", "ignore")

    def pick(block, *names):
        for name in names:
            m = re.search(r"<%s>(.*?)</%s>" % (name, name), block, re.S)
            if not m:
                continue
            value = m.group(1).strip()
            value = re.sub(r"^<!\[CDATA\[|\]\]>$", "", value).strip()
            if value:
                return value
        return ""

    out = []
    for block in re.findall(r"<item>(.*?)</item>", txt, re.S):
        out.append({
            "title": pick(block, "title"),
            "link": pick(block, "link"),
            "category": pick(block, "category"),
            "pub": pick(block, "pubDate", "published", "updated"),
            "description": clean_text(pick(block, "description", "content:encoded"), 1500),
            "company": pick(block, "job:company"),
            "location": pick(block, "job:location"),
            "work_mode": pick(block, "job:work_mode"),
            "apply_url": pick(block, "job:apply_url"),
        })
    return out


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
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        pass
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ classification
LATAM = r"latam|latin america|brazil|brasil|argentina|mexic|colombia|chile|peru|uruguay|paraguay|ecuador|venezuela|centroamerica|caribbean"
EU = r"emea|europe|europa|\beu\b|germany|deutschland|france|united kingdom|\buk\b|spain|portugal|netherlands|poland|ireland|italy|sweden|denmark|norway|finland|austria|belgium|switzerland|czech|romania|ukraine"
AMERICA = r"united states|\bu\.?s\.?a?\b|america|canada|north america|new york|san francisco|seattle|austin|boston|chicago|toronto"
AFRICA = r"africa|nigeria|kenya|south africa|egypt|morocco|ghana|uganda|tanzania|rwanda|senegal"
ASIA = r"india|pakistan|bangladesh|philippines|indonesia|vietnam|thailand|malaysia|singapore|japan|korea|china|sri lanka|nepal"


def infer_region(*parts):
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
    if isinstance(hint, (list, tuple)):
        hint = " ".join(str(h) for h in hint if h)
    h = str(hint or "").lower()
    if "entry" in h or "intern" in h or "graduate" in h:
        return "entry"
    if "junior" in h:
        return "junior"
    if "senior" in h:
        return "senior"
    if any(k in h for k in ("lead", "principal", "director", "executive", "manager")):
        return "lead" if "manager" not in h else "mid"

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
        return n * 1000 if n < 1000 else n

    lo, hi = norm(m.group(1)), norm(m.group(2))
    if lo < 12000 or hi < lo or hi > 2_000_000:
        return None, None
    return f"${lo:,}", f"${hi:,}"


def money(v):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    if n <= 0 or n > 2_000_000:
        return None
    return f"${n * 1000 if n < 1000 else n:,}"


def build(title, company, url, source, region, tags=None, posted=None, body="",
          salary_hint=None, level_hint=None, category_hint=None):
    smin = smax = None
    if isinstance(salary_hint, tuple):
        smin, smax = salary_hint
    if not smin:
        smin, smax = extract_salary(body)

    # An "annual" range under $24k is almost always an hourly, daily or monthly
    # figure the source published (a $1,500–$2,500 range for a physician is a
    # day rate). Shipping it as annual poisons the salary filter and the digest
    # floor, so drop the pair instead of guessing a multiplier.
    hi = re.sub(r"[^0-9]", "", str(smax or smin or ""))
    if hi and int(hi) < 24000:
        smin = smax = None

    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    tags = [t for t in (tags or []) if t]

    cat = None
    if category_hint:
        if isinstance(category_hint, (list, tuple)):
            category_hint = next((c for c in category_hint if c), None)
        if category_hint:
            cat = CATEGORY_HINTS.get(str(category_hint).strip().lower(), "auto")
            if cat == "auto":
                cat = None
    if not cat:
        cat = categorize(title)

    dt = parse_date(posted)
    ghost = bool(dt and (datetime.now(timezone.utc) - dt) > timedelta(days=GHOST_DAYS))

    # Only ever ship http(s) links: a feed that carries a javascript: or data:
    # URL turns every card into an XSS vector on our own domain.
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = ""

    return {
        "title": (title or "").strip()[:180],
        "company": (company or "").strip()[:80],
        "salary_min": smin,
        "salary_max": smax,
        "url": url,
        "source": source,
        "tags": [str(t)[:32] for t in tags][:4],
        "posted_at": dt.isoformat() if dt else None,
        "region": region,
        "category": cat,
        "level": level_of(title, level_hint),
        "ghost": ghost,
        "_body": clean_text(body, 600),
    }


# ------------------------------------------------------------------ normalizers
def from_greenhouse(job, board):
    loc = (job.get("location") or {}).get("name") or ""
    return build(
        job.get("title"), job.get("company_name") or board, job.get("absolute_url"),
        "greenhouse", infer_region(loc),
        [d.get("name") for d in (job.get("departments") or []) if d.get("name")],
        job.get("first_published") or job.get("updated_at"), job.get("content"),
    )


def from_ashby(job, board):
    loc = job.get("location") or ""
    return build(
        job.get("title"), board, job.get("jobUrl") or job.get("applyUrl"),
        "ashby", infer_region(loc, job.get("workplaceType")),
        [job.get(k) for k in ("department", "team", "employmentType") if job.get(k)],
        job.get("publishedAt"),
        job.get("descriptionPlain") or job.get("descriptionHtml"),
    )


def from_lever(job, company):
    cats = job.get("categories") or {}
    loc = cats.get("location") or ""
    return build(
        job.get("text"), company, job.get("hostedUrl"),
        "lever", infer_region(loc, cats.get("commitment")),
        [cats.get("team"), cats.get("department"), cats.get("commitment")],
        job.get("createdAt"),
        job.get("descriptionPlain") or job.get("description"),
    )


def from_remotejobs(item):
    comp = item.get("company") or {}
    cat = item.get("category") or {}
    loc = item.get("location") or ""
    norm = item.get("location_normalization") or {}
    scope = norm.get("scope") if isinstance(norm, dict) else None
    return build(
        item.get("title"),
        comp.get("name") if isinstance(comp, dict) else comp,
        item.get("apply_url") or item.get("url"),
        "remotejobs",
        infer_region(loc, scope),
        [cat.get("name") if isinstance(cat, dict) else cat] + (item.get("employment_types") or []),
        item.get("posted_at"),
        item.get("description"),
        salary_hint=(money(item.get("salary_min")), money(item.get("salary_max"))),
        category_hint=cat.get("name") if isinstance(cat, dict) else None,
    )


def from_themuse(item):
    locs = [l.get("name") for l in (item.get("locations") or []) if l.get("name")]
    levels = [l.get("name") for l in (item.get("levels") or []) if l.get("name")]
    cats = [c.get("name") for c in (item.get("categories") or []) if c.get("name")]
    comp = item.get("company") or {}
    return build(
        item.get("name"),
        comp.get("name") if isinstance(comp, dict) else comp,
        (item.get("refs") or {}).get("landing_page"),
        "themuse",
        infer_region(", ".join(locs)),
        cats,
        item.get("publication_date"),
        item.get("contents"),
        level_hint=levels[0] if levels else None,
        category_hint=cats[0] if cats else None,
    )


def from_himalayas(item):
    return build(
        item.get("title"), item.get("companyName"),
        item.get("applicationLink") or item.get("guid"),
        "himalayas", infer_region(item.get("locationRestrictions")),
        (item.get("categories") or [])[:4],
        item.get("pubDate") or item.get("expiryDate"),
        item.get("description") or item.get("excerpt"),
        salary_hint=(money(item.get("minSalary")), money(item.get("maxSalary"))),
        level_hint=item.get("seniority"),
    )


def from_jobicy(item):
    return build(
        item.get("jobTitle"), item.get("companyName"), item.get("url"),
        "jobicy", infer_region(item.get("jobGeo")),
        [item.get("jobIndustry"), item.get("jobType")],
        item.get("pubDate"),
        item.get("jobDescription") or item.get("jobExcerpt"),
        level_hint=item.get("jobLevel"),
        category_hint=item.get("jobIndustry"),
    )


def from_remoteok(item):
    return build(
        item.get("position"), item.get("company"),
        item.get("apply_url") or item.get("url"),
        "remoteok", infer_region(item.get("location")),
        item.get("tags"), item.get("date") or item.get("epoch"),
        item.get("description"),
        salary_hint=(money(item.get("salary_min")), money(item.get("salary_max"))),
    )


def from_arbeitnow(item):
    if not item.get("remote"):
        return None
    return build(
        item.get("title"), item.get("company_name"), item.get("url"),
        "arbeitnow", infer_region(item.get("location")),
        item.get("tags") or item.get("job_types"),
        item.get("created_at"), item.get("description"),
    )


def from_remotive(item):
    return build(
        item.get("title"), item.get("company_name"), item.get("url"),
        "remotive", infer_region(item.get("candidate_required_location")),
        [item.get("category"), item.get("job_type")],
        item.get("publication_date"), item.get("description"),
        category_hint=item.get("category"),
    )


def from_4dayweek(item):
    locs = item.get("locations") or []
    if isinstance(locs, list):
        loc_txt = ", ".join(str(l.get("country") or l.get("continent") or "") for l in locs if isinstance(l, dict))
    else:
        loc_txt = str(locs)
    slug = item.get("slug") or ""
    return build(
        item.get("title"), item.get("company_name"),
        f"https://4dayweek.io/job/{slug}" if slug else None,
        "4dayweek", infer_region(loc_txt, item.get("work_arrangement")),
        [item.get("category"), item.get("schedule_type")],
        item.get("inserted") or item.get("posted"),
        "",
        salary_hint=(money(item.get("salary_lower")), money(item.get("salary_upper"))),
        level_hint=item.get("level"),
        category_hint=item.get("category"),
    )


def from_workingnomads(item):
    return build(
        item.get("title"), item.get("company_name"), item.get("url"),
        "workingnomads", infer_region(item.get("location")),
        [item.get("category_name")] + list(
            [t.strip() for t in (item.get("tags") or "").split(",")] if isinstance(item.get("tags"), str) else (item.get("tags") or [])
        )[:3],
        item.get("pub_date"), item.get("description"),
        category_hint=item.get("category_name"),
    )


def from_wwr(item):
    title = item.findtext("title") or ""
    company, _, role = title.partition(": ")
    if not role:
        company, role = "", title
    skills = item.findtext("skills") or ""
    tags = [item.findtext("category")] + [s.strip() for s in skills.split(",") if s.strip()][:3]
    return build(
        role, company, item.findtext("link"),
        "weworkremotely", infer_region(item.findtext("region"), item.findtext("country")),
        tags, item.findtext("pubDate"), item.findtext("description"),
    )


def from_nodesk(item):
    title = item.findtext("title") or ""
    company, _, role = title.rpartition(" at ")
    if not company:
        company, role = "", title
    return build(
        role, company, item.findtext("link"),
        "nodesk", infer_region(title),
        [], item.findtext("pubDate"), item.findtext("description"),
    )


def from_smartrecruiters(item, company):
    loc = item.get("location") or {}
    place = " ".join(str(loc.get(k)) for k in ("city", "region", "country") if loc.get(k))
    return build(
        item.get("name"), company,
        f"https://jobs.smartrecruiters.com/{company}/{item.get('id')}",
        "smartrecruiters", infer_region(place, "remote" if loc.get("remote") else None),
        [((item.get("department") or {}).get("label") or "")],
        item.get("releasedDate"), item.get("shortDescription"),
    )


def from_recruitee(item, company):
    depts = [d.get("name") for d in (item.get("departments") or []) if d.get("name")]
    return build(
        item.get("title"), company, item.get("careers_url") or item.get("url"),
        "recruitee", infer_region(item.get("location") or ""),
        depts, item.get("published_at"),
        item.get("description") or item.get("short_description"),
    )


def from_usajobs(item):
    d = item.get("MatchedObjectDescriptor") or {}
    pay = d.get("PositionRemuneration") or []
    hint = None
    if pay:
        lo = pay[0].get("RemunerationMinimumRate")
        hi = pay[0].get("RemunerationMaximumRate")
        try:
            hint = (int(float(lo)), int(float(hi or lo))) if lo else None
        except (TypeError, ValueError):
            hint = None
    disp = str(d.get("PositionLocationDisplay") or "")
    region = infer_region("United States") if disp.lower().startswith("remote") else infer_region(disp or "United States")
    return build(
        d.get("PositionTitle"), d.get("OrganizationName"), d.get("PositionURI"),
        "usajobs", region,
        [], d.get("PublicationStartDate"),
        (d.get("QualificationSummary") or ""), salary_hint=hint,
    )


LOC_WORDS = {"remote", "worldwide", "anywhere", "usa", "us", "united", "states", "canada",
             "europe", "eu", "uk", "latam", "germany", "global", "americas", "emea",
             "apac", "africa", "asia", "austria", "spain", "france"}

NAME_STOP = {"is", "was", "are", "has", "have", "provides", "helps", "builds", "born",
             "the", "a", "an", "we", "our", "at", "in", "for", "and", "with", "of",
             "to", "that", "this", "it", "on", "by", "as", "from", "its", "role",
             "position", "job", "team", "company"}


NAME_VERBS = r"(?:is|was|are|has|have|builds|provides|helps|enables|offers|makes|powers|born|now|lets)"
_AT_RE = re.compile(r"(?:^|[.!?]\s+|^)At\s+([A-Z][\w&.'\-]*(?:\s+[A-Z][\w&.'\-]*){0,2})")
_VERB_RE = re.compile(r"(?:^|[.!?]\s+)(?:The\s+)?([A-Z][\w&.'\-]*(?:\s+[A-Z][\w&.'\-]*){0,2}),?\s+" + NAME_VERBS + r"\b")


def name_from_description(desc):
    """Most boards open the body with the employer: 'Coinbase is…', 'At Mercury, we…'."""
    text = (desc or "").strip()[:400]
    for pattern in (_AT_RE, _VERB_RE):
        for m in pattern.finditer(text):
            words = []
            for word in m.group(1).rstrip(",").split():
                if word.lower() in NAME_STOP:
                    break
                words.append(word)
                if len(words) == 3:
                    break
            name = " ".join(words).strip()
            if name and len(name) > 2:
                return name
    return ""


def usable_company(name, title):
    """Reject a candidate that is the role title echoed back."""
    key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    title_key = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    if not key or not title_key:
        return ""
    if key in title_key:
        return ""
    title_words = {w for w in re.split(r"[^a-z0-9]+", (title or "").lower()) if len(w) > 2}
    name_words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower()) if len(w) > 2]
    if len(name_words) > 1 and name_words[0] not in title_words:
        if sum(1 for w in name_words if w in title_words) >= 1:
            return ""  # "Mercury Sales Development" for "Sales Development Representative"
    return name


def prettify_company(raw):
    s = html.unescape(raw or "").strip()
    if not s:
        return ""
    return s.replace("-", " ").title() if (s.islower() or "-" in s) else s


def company_from_slug(url):
    """Jobspresso puts the employer in the slug: /job/<company>-<location>-<role>/."""
    m = re.search(r"/job/([a-z0-9-]+)/", url or "")
    if not m:
        return ""
    keep = []
    for word in m.group(1).split("-"):
        if word in LOC_WORDS:
            break
        keep.append(word)
    name = " ".join(keep[:6]).strip()
    return name.title() if name else ""


def from_jobspresso(item):
    desc = item.get("description") or ""
    title = html.unescape(item.get("title") or "")
    company = (
        usable_company(company_from_slug(item.get("link")), title)
        or usable_company(name_from_description(desc), title)
        or "Jobspresso"
    )
    return build(
        title, company, item.get("link"), "jobspresso",
        infer_region(title + " " + desc + " remote"),
        posted=item.get("pub"), body=desc, category_hint=item.get("category"),
    )


def from_djangojobs(item):
    raw_title = html.unescape(item.get("title") or "")
    company = prettify_company(item.get("company"))
    title = raw_title
    if " at " in raw_title.lower()[-60:]:
        title, _, tail = raw_title.rpartition(" at ")
        company = company or prettify_company(tail)
    desc = item.get("description") or ""
    company = usable_company(company, title) or usable_company(name_from_description(desc), title) or "DjangoJobs"
    # a few items ship the two fields swapped: the role slug lands in job:company
    if categorize(title) == "other" and categorize(company) != "other" and company != "DjangoJobs":
        title, company = company, title
    where = " ".join(p for p in (item.get("location"), item.get("work_mode")) if p)
    return build(
        title.strip(), company, item.get("apply_url") or item.get("link"),
        "djangojobs", infer_region(where + " " + desc + " remote"),
        tags=[t for t in (item.get("work_mode"), item.get("location")) if t],
        posted=item.get("pub"), body=desc, category_hint=item.get("category"),
    )


def from_pythonjobs(item):
    raw_title = html.unescape(item.get("title") or "")
    role, company = raw_title, "Python.org Jobs"
    if "," in raw_title:
        role, _, tail = raw_title.rpartition(",")
        if tail.strip():
            company = tail.strip()
    desc = item.get("description") or ""
    return build(
        role.strip(), company, item.get("link"), "pythonjobs",
        infer_region(desc + " remote"), tags=["python"],
        posted=item.get("pub"), body=desc,
    )


def is_remote(item):
    blob = f"{item.get('_body','')} {' '.join(item.get('tags') or [])}".lower()
    return "remote" in blob or "anywhere" in blob or item.get("region") == "worldwide"


# ------------------------------------------------------------------ collect
def collect():
    jobs, seen, stats = [], set(), []
    cutoff = datetime.now(timezone.utc) - timedelta(days=DROP_DAYS)

    def add(item):
        if not item or not item["title"] or not item["url"]:
            return 0
        if item["url"] in seen:
            return 0
        # descartar listados muertos (> DROP_DAYS)
        if item["posted_at"]:
            try:
                if datetime.fromisoformat(item["posted_at"]) < cutoff:
                    return 0
            except Exception:  # noqa: BLE001
                pass
        seen.add(item["url"])
        jobs.append(item)
        return 1

    def report(name, fetched, kept):
        stats.append((name, fetched, kept))
        print(f"  {name:30} fetched={fetched:>5} kept={kept:>5}")

    # ---------- ATS
    for board in GREENHOUSE_BOARDS:
        raw = ((fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true") or {}).get("jobs")) or []
        kept = 0
        for j in raw:
            item = from_greenhouse(j, board)
            if is_remote(item):
                kept += add(item)
        report(f"greenhouse/{board}", len(raw), kept)

    for board in ASHBY_BOARDS:
        raw = ((fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}") or {}).get("jobs")) or []
        kept = 0
        for j in raw:
            item = from_ashby(j, board)
            if j.get("isRemote") or is_remote(item):
                kept += add(item)
        report(f"ashby/{board}", len(raw), kept)

    for comp in LEVER_COMPANIES:
        raw = fetch_json(f"https://api.lever.co/v0/postings/{comp}?mode=json") or []
        kept = 0
        for j in raw:
            if not isinstance(j, dict):
                continue
            item = from_lever(j, comp)
            if is_remote(item):
                kept += add(item)
        report(f"lever/{comp}", len(raw), kept)

    # ---------- remotejobs.org (biggest source)
    rj_total = rj_kept = 0
    for page in range(REMOTEJOBS_PAGES):
        data = fetch_json(f"https://remotejobs.org/api/v1/jobs?limit=50&offset={page * 50}")
        batch = (data or {}).get("data") or []
        rj_total += len(batch)
        rj_kept += sum(add(from_remotejobs(x)) for x in batch if isinstance(x, dict))
        if not (data or {}).get("pagination", {}).get("has_more"):
            break
        time.sleep(0.25)
    report("remotejobs.org", rj_total, rj_kept)

    # ---------- The Muse
    tm_total = tm_kept = 0
    for page in range(1, THEMUSE_PAGES + 1):
        data = fetch_json(f"https://www.themuse.com/api/public/jobs?page={page}&location=Remote")
        batch = (data or {}).get("results") or []
        tm_total += len(batch)
        tm_kept += sum(add(from_themuse(x)) for x in batch if isinstance(x, dict))
        if not batch:
            break
        time.sleep(0.25)
    report("themuse (Remote)", tm_total, tm_kept)

    # ---------- Himalayas
    hi_total = hi_kept = 0
    for page in range(HIMALAYAS_PAGES):
        data = fetch_json(f"https://himalayas.app/jobs/api?limit=20&offset={page * 20}")
        batch = (data or {}).get("jobs") or []
        hi_total += len(batch)
        hi_kept += sum(add(from_himalayas(x)) for x in batch if isinstance(x, dict))
        if not batch:
            break
        time.sleep(0.25)
    report("himalayas", hi_total, hi_kept)

    # ---------- 4dayweek
    fw_total = fw_kept = 0
    for page in range(1, FOURDAYWEEK_PAGES + 1):
        data = fetch_json(f"https://4dayweek.io/api/jobs?page={page}")
        batch = (data or {}).get("jobs") or []
        fw_total += len(batch)
        fw_kept += sum(add(from_4dayweek(x)) for x in batch if isinstance(x, dict) and not x.get("is_expired"))
        time.sleep(0.25)
    report("4dayweek.io", fw_total, fw_kept)

    # ---------- single-shot feeds
    for name, url, fn in [
        ("jobicy", "https://jobicy.com/api/v2/remote-jobs?count=100", from_jobicy),
        ("remoteok", "https://remoteok.com/api", from_remoteok),
        ("workingnomads", "https://www.workingnomads.com/api/exposed_jobs/", from_workingnomads),
    ]:
        data = fetch_json(url)
        if isinstance(data, dict):
            raw = data.get("jobs") or []
        elif isinstance(data, list):
            raw = [x for x in data if isinstance(x, dict) and "legal" not in str(x)[:40].lower()]
        else:
            raw = []
        kept = sum(add(fn(x)) for x in raw if isinstance(x, dict))
        report(name, len(raw), kept)
        time.sleep(0.3)

    for page in range(1, ARBEITNOW_PAGES + 1):
        raw = ((fetch_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}") or {}).get("data")) or []
        report(f"arbeitnow/p{page}", len(raw), sum(add(from_arbeitnow(x)) for x in raw if isinstance(x, dict)))
        time.sleep(0.3)

    for cat in REMOTIVE_CATEGORIES:
        raw = ((fetch_json(f"https://remotive.com/api/remote-jobs?category={cat}") or {}).get("jobs")) or []
        report(f"remotive/{cat}", len(raw), sum(add(from_remotive(x)) for x in raw if isinstance(x, dict)))
        time.sleep(1.0)  # remotive pide <= 2 req/min en el feed libre

    # ---------- RSS
    for url in WWR_FEEDS:
        items = fetch_xml(url)
        report("wwr/" + url.rsplit("/", 1)[-1].replace(".rss", "")[:24], len(items), sum(add(from_wwr(x)) for x in items))
        time.sleep(0.3)

    for url in NODESK_FEEDS:
        items = fetch_xml(url)
        report("nodesk", len(items), sum(add(from_nodesk(x)) for x in items))

    # ---------- public RSS job feeds (Jobspresso, DjangoJobs, Python.org)
    for name, url in RSS_JOB_FEEDS:
        items = fetch_feed_items(url)
        fn = {"jobspresso": from_jobspresso, "djangojobs": from_djangojobs,
              "pythonjobs": from_pythonjobs}[name]
        report(name, len(items), sum(add(fn(x)) for x in items))
        time.sleep(0.4)

    # ---------- SmartRecruiters / Recruitee
    for comp in SMARTRECRUITERS_COMPANIES:
        data = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{comp}/postings?limit=100") or {}
        raw = data.get("content") or []
        kept = 0
        for x in raw:
            if not isinstance(x, dict):
                continue
            item = from_smartrecruiters(x, comp)
            if (x.get("location") or {}).get("remote") or is_remote(item):
                kept += add(item)
        report(f"smartrecruiters/{comp}", len(raw), kept)
        time.sleep(0.3)

    for comp in RECRUITEE_COMPANIES:
        data = fetch_json(f"https://{comp}.recruitee.com/api/offers/") or {}
        raw = data.get("offers") or []
        kept = sum(add(from_recruitee(x, comp)) for x in raw if isinstance(x, dict))
        report(f"recruitee/{comp}", len(raw), kept)

    # ---------- USAJOBS (empleos de EE.UU., requiere clave gratuita)
    key = os.environ.get("USAJOBS_API_KEY")
    if key:
        data = None
        try:
            req = urllib.request.Request(
                "https://data.usajobs.gov/api/search?Keyword=remote&ResultsPerPage=100",
                headers={
                    "User-Agent": os.environ.get("USAJOBS_USER_EMAIL", "radarjobs@example.com"),
                    "Authorization-Key": key,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except Exception as e:  # noqa: BLE001
            data = None
            print(f"    ! usajobs -> {type(e).__name__}: {e}", file=sys.stderr)
        items = ((data or {}).get("SearchResult") or {}).get("SearchResultItems") or []
        report("usajobs", len(items), sum(add(from_usajobs(x)) for x in items if isinstance(x, dict)))
    else:
        report("usajobs (sin clave)", 0, 0)

    jobs.sort(key=lambda x: x.get("posted_at") or "", reverse=True)
    return jobs, stats


# ------------------------------------------------------------------ output
def write_rss(slim, path, limit=60):
    """RSS 2.0 feed of the freshest non-ghost openings."""
    from email.utils import format_datetime

    base = "https://radarjobs.pages.dev"
    items = [j for j in slim if not j.get("ghost")][:limit]
    now = datetime.now(timezone.utc)

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<?xml-stylesheet type="text/xsl" href="/rss.xsl"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        "  <title>RadarJobs — remote openings</title>",
        f"  <link>{base}/jobs</link>",
        "  <description>Fresh remote job openings, refreshed every hour.</description>",
        "  <language>en</language>",
        f"  <lastBuildDate>{format_datetime(now)}</lastBuildDate>",
        f'  <atom:link href="{base}/rss.xml" rel="self" type="application/rss+xml"/>',
    ]

    for j in items:
        title = (j.get("title") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        company = (j.get("company") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        url = (j.get("url") or "").replace("&", "&amp;")
        salary = j.get("salary_min")
        desc = f"{company} — {j.get('region', 'worldwide')}"
        if salary:
            desc += f" — {salary}" + (f"-{j['salary_max']}" if j.get("salary_max") else "") + " USD"
        pub = ""
        if j.get("posted_at"):
            try:
                pub = f"\n      <pubDate>{format_datetime(datetime.fromisoformat(j['posted_at']))}</pubDate>"
            except Exception:  # noqa: BLE001
                pass
        out.append(
            "  <item>\n"
            f"      <title>{title} @ {company}</title>\n"
            f"      <link>{url}</link>\n"
            f"      <guid isPermaLink=\"true\">{url}</guid>\n"
            f"      <description>{desc}</description>{pub}\n"
            "  </item>"
        )

    out.append("</channel>\n</rss>\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return len(items)


def write_outputs(jobs):
    os.makedirs(OUT_DIR, exist_ok=True)
    full = os.path.join(OUT_DIR, "jobs.json")
    with open(full, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=1)

    keep = ["title", "company", "salary_min", "salary_max", "url", "source",
            "tags", "posted_at", "region", "category", "level", "ghost"]
    slim = [{k: j.get(k) for k in keep} for j in jobs]
    slim.sort(key=lambda x: x.get("posted_at") or "", reverse=True)
    feed = os.path.join(ROOT, "jobs.json")
    with open(feed, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))

    n = write_rss(slim, os.path.join(ROOT, "rss.xml"))
    print(f"  rss: {n} items")
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
                "apikey": key, "Authorization": f"Bearer {key}",
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
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    print(f"RadarJobs scraper — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n")
    jobs, stats = collect()
    full, feed, slim = write_outputs(jobs)

    by_cat, by_level, by_region, by_src = {}, {}, {}, {}
    for j in jobs:
        by_cat[j["category"]] = by_cat.get(j["category"], 0) + 1
        by_level[j["level"]] = by_level.get(j["level"], 0) + 1
        by_region[j["region"]] = by_region.get(j["region"], 0) + 1
        by_src[j["source"]] = by_src.get(j["source"], 0) + 1

    ghosts = sum(1 for j in jobs if j["ghost"])
    print(f"\nTOTAL unique remote jobs: {len(jobs)}  (ghost >30d: {ghosts})")
    print(f"  con salario: {sum(1 for j in jobs if j['salary_min'])}")
    print(f"  por categoria: {dict(sorted(by_cat.items(), key=lambda x: -x[1]))}")
    print(f"  por nivel:     {by_level}")
    print(f"  por region:    {by_region}")
    print(f"  por fuente:    {dict(sorted(by_src.items(), key=lambda x: -x[1]))}")
    print(f"  feed: {feed} ({os.path.getsize(feed)/1024:.0f} KB)")

    if args.push:
        print("\nSubiendo a Supabase…")
        print("  enviados:", push_to_supabase(jobs))


if __name__ == "__main__":
    main()
