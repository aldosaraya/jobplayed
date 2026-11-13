/* RadarJobs — job feed renderer, filters, sort and pagination.
 * Data source: Supabase REST (live), with static /jobs.json as fallback.
 */
import { initZoom, safeUrl } from "./app.js";
import { SUPABASE_URL, SUPABASE_ANON_KEY, supabase } from "./auth.js";

(function () {
  "use strict";

  const container = document.getElementById("jobs-container");
  if (!container) return;
  container.setAttribute("data-zoom-group", "");

  const PAGE_SIZE = 10;
  const LOAD_BATCH = 500;      // rows per REST call
  const BATCH_PAUSE = 120;     // ms between calls — 6 pages, not 6k requests
  const REGION_LABEL = {
    worldwide: "Worldwide",
    latam: "Latam Only",
    america: "America Only",
    eu: "EU Only",
  };

  // Which public endpoint each row came from — shown in the inspector.
  const SOURCE_LABEL = {
    greenhouse: "Greenhouse (public API)",
    ashby: "Ashby (public API)",
    lever: "Lever (public API)",
    smartrecruiters: "SmartRecruiters",
    recruitee: "Recruitee",
    remotejobs: "remotejobs.org",
    themuse: "The Muse",
    himalayas: "Himalayas",
    jobicy: "Jobicy",
    remoteok: "RemoteOK",
    arbeitnow: "Arbeitnow",
    remotive: "Remotive",
    workingnomads: "Working Nomads",
    "4dayweek": "4dayweek.io",
    weworkremotely: "We Work Remotely",
    nodesk: "NoDesk",
    jobspresso: "Jobspresso",
    djangojobs: "DjangoJobs",
    pythonjobs: "Python.org Jobs",
    usajobs: "USAJOBS",
  };

  let JOBS = [];
  const state = { q: "", region: "all", category: "", salary: 0, worktype: "", seniority: "", page: 1, sort: "", hideGhost: true };

  const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------------- derivaciones ---------------- */
  function categorize(title) {
    const t = (title || "").toLowerCase();
    if (/\b(data scientist|data engineer|analytics|machine learning|\bml\b|\bai\b|research)\b/.test(t)) return "data";
    if (/\b(design|ux|ui|creative|brand)\b/.test(t)) return "design";
    if (/\b(product manager|product lead|product owner|product)\b/.test(t)) return "product";
    if (/\b(sales|account executive|revenue|partnership|business development)\b/.test(t)) return "sales";
    if (/\b(support|customer success|advocate)\b/.test(t)) return "support";
    if (/\b(devops|\bsre\b|reliability|infrastructure|platform|security|cloud)\b/.test(t)) return "devops";
    if (/\b(finance|accounting|tax|payroll|legal|counsel|compliance)\b/.test(t)) return "finance";
    if (/\b(engineer|developer|software|backend|frontend|full.?stack|architect)\b/.test(t)) return "engineering";
    return "engineering";
  }

  function seniorityOf(title) {
    const t = (title || "").toLowerCase();
    if (/\b(junior|intern|entry|graduate)\b/.test(t)) return "junior";
    if (/\b(staff|principal|lead|head|director|vp|chief)\b/.test(t)) return "lead";
    if (/\bsenior\b|\bsr\.?\b/.test(t)) return "senior";
    return "mid";
  }

  function worktypeOf(job) {
    const tags = (job.tags || []).join(" ").toLowerCase();
    if (/contract|contractor|b2b/.test(tags)) return "contract";
    if (/part.?time/.test(tags)) return "part-time";
    return "full-time";
  }

  // El feed ya trae category/level calculados por el scraper; si faltan, se derivan.
  function categoryOf(job) {
    return job.category || categorize(job.title);
  }

  function levelOf(job) {
    return job.level || seniorityOf(job.title);
  }

  const LEVEL_WEIGHT = { entry: 0, junior: 1, mid: 2, senior: 3, lead: 4 };
  const ACCESSIBLE = new Set(["support", "admin", "data-entry", "writing", "marketing", "sales"]);

  function ago(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const mins = Math.floor((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + " min ago";
    const h = Math.floor(mins / 60);
    if (h < 24) return h + " h ago";
    const days = Math.floor(h / 24);
    return days + " d ago";
  }

  function salaryOf(job) {
    if (!job.salary_min) return null;
    if (!job.salary_max || job.salary_max === job.salary_min) return `${job.salary_min} USD`;
    return `${job.salary_min} - ${job.salary_max} USD`;
  }

  function salaryNum(job) {
    if (!job.salary_min) return 0;
    return parseInt(String(job.salary_min).replace(/[^0-9]/g, ""), 10) || 0;
  }

  const COMPANY_NAMES = {
    openai: "OpenAI", gitlab: "GitLab", posthog: "PostHog", github: "GitHub",
    mongodb: "MongoDB", datadog: "Datadog", instacart: "Instacart", airtable: "Airtable",
    vercel: "Vercel", coinbase: "Coinbase", twilio: "Twilio", reddit: "Reddit",
    discord: "Discord", dropbox: "Dropbox", stripe: "Stripe", affirm: "Affirm",
    vanta: "Vanta", ramp: "Ramp",
  };

  function companyLabel(job) {
    const raw = String(job.company || "Company").trim();
    const key = raw.toLowerCase().replace(/[^a-z0-9]/g, "");
    return COMPANY_NAMES[key] || raw.charAt(0).toUpperCase() + raw.slice(1);
  }

  /* ---------------- tarjeta ---------------- */
  function cardHTML(job, idx) {
    const sal = salaryOf(job);
    const tags = (job.tags || []).slice(0, 3)
      .map((t) => `<span class="px-2 py-0.5 rounded-DEFAULT bg-surface-container text-on-surface-variant font-label-sm text-label-sm font-mono">${esc(t)}</span>`)
      .join("");

    const ghost = job.ghost
      ? `<span title="Posted more than 30 days ago — the posting may be stale" class="px-2 py-0.5 rounded-DEFAULT bg-surface-container-highest text-outline font-label-sm text-label-sm">👻 ghost</span>`
      : "";

    return `<article data-idx="${idx}" data-zoom class="group relative bg-surface-container-low hover:bg-surface-container transition-all p-5 rounded-DEFAULT shadow-sm cursor-pointer border-l-2 ${job.ghost ? "border-outline-variant" : "border-primary"}">
  <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
    <div class="flex items-start gap-3.5 min-w-0">
      <div class="w-12 h-12 rounded-DEFAULT bg-surface-container flex items-center justify-center font-headline-sm text-headline-sm text-primary font-bold shadow-inner shrink-0">
        <span class="material-symbols-outlined text-[24px]">${esc((companyLabel(job)[0] || "?").toUpperCase())}</span>
      </div>
      <div class="space-y-1 min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-headline-sm text-body-md text-on-surface font-semibold tracking-wide">${esc(companyLabel(job))}</span>
          <span class="px-2 py-0.5 rounded-DEFAULT bg-surface-container-high font-label-sm text-label-sm text-outline flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>${esc(job.source)}
          </span>
          <span class="font-mono-data text-body-sm text-outline">${esc(ago(job.posted_at))}</span>
          ${ghost}
        </div>
        <h3 class="font-headline-md text-headline-sm text-on-surface group-hover:text-primary transition-colors font-bold truncate">${esc(job.title)}</h3>
        <p class="font-body-sm text-body-sm text-on-surface-variant">${esc(REGION_LABEL[job.region] || "Worldwide")} • Remote</p>
      </div>
    </div>
    <div class="flex items-center gap-2 self-start sm:self-auto shrink-0">
      <a class="p-2 rounded-DEFAULT bg-surface-container text-on-surface-variant hover:text-primary hover:bg-surface-container-high transition-colors" href="${esc(safeUrl(job.url))}" target="_blank" rel="noopener noreferrer" title="View posting at original source">
        <span class="material-symbols-outlined text-[20px]">open_in_new</span>
      </a>
    </div>
  </div>
  <div class="mt-4 pt-3 flex flex-wrap items-center justify-between gap-2.5">
    <div class="flex flex-wrap items-center gap-1.5">
      ${tags}
    </div>
    <span class="font-mono-data text-body-sm ${sal ? "text-primary font-semibold" : "text-outline"}">${sal ? esc(sal) : "Salary not disclosed"}</span>
  </div>
</article>`;
  }

  /* ---------------- sponsored (pinned) listings ---------------- */
  // Paid rows live in their own table so the hourly feed refresh can never
  // delete them. They are pinned above organic results only while they match
  // the visitor's current search and filters — an irrelevant ad is just spam.
  let SPONSORED = [];
  const TRACKED = new Set();

  async function loadSponsored() {
    try {
      const { data, error } = await supabase
        .from("sponsored_jobs")
        .select("id,title,company,url,logo_url,blurb,salary_min,salary_max,region,category,level,tags,weight")
        .order("weight", { ascending: false })
        .limit(20);
      if (error) throw error;
      SPONSORED = data || [];
    } catch (err) {
      console.warn("[radarjobs] sponsored rows unavailable:", err.message);
      SPONSORED = [];
    }
  }

  function trackSponsored(id, kind) {
    if (kind === "view") {
      if (TRACKED.has(id)) return;
      TRACKED.add(id);
    }
    supabase.rpc("rj_sponsor_track", { p_id: id, p_kind: kind }).then(() => {}, () => {});
  }

  function sponsoredCard(job) {
    const sal = salaryOf(job);
    const initial = esc((companyLabel(job)[0] || "?").toUpperCase());
    const logo = job.logo_url
      ? `<img src="${esc(safeUrl(job.logo_url))}" alt="" width="48" height="48" loading="lazy" class="w-12 h-12 rounded-DEFAULT object-contain bg-surface-container p-1" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${initial}',className:'font-headline-sm text-headline-sm text-primary font-bold'}))">`
      : `<span class="font-headline-sm text-headline-sm text-primary font-bold">${initial}</span>`;
    const tags = (job.tags || []).slice(0, 3)
      .map((t) => `<span class="px-2 py-0.5 rounded-DEFAULT bg-surface-container text-on-surface-variant font-label-sm text-label-sm font-mono">${esc(t)}</span>`)
      .join("");
    return `<article class="relative bg-surface-container-low p-5 rounded-DEFAULT shadow-sm border border-primary/40">
    <span class="absolute -top-2.5 left-4 px-2 py-0.5 rounded-full bg-primary text-on-primary font-label-sm text-label-sm font-semibold tracking-wider uppercase">Sponsored</span>
    <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
      <div class="flex items-start gap-3.5 min-w-0">
        <div class="w-12 h-12 rounded-DEFAULT bg-surface-container flex items-center justify-center shrink-0">${logo}</div>
        <div class="space-y-1 min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-headline-sm text-body-md text-on-surface font-semibold">${esc(companyLabel(job))}</span>
            ${job.blurb ? `<span class="font-body-sm text-body-sm text-on-surface-variant">${esc(job.blurb)}</span>` : ""}
          </div>
          <h3 class="font-headline-md text-headline-sm text-on-surface font-bold">${esc(job.title)}</h3>
          <p class="font-body-sm text-body-sm text-on-surface-variant">${esc(REGION_LABEL[job.region] || "Worldwide")} · Remote</p>
        </div>
      </div>
      <a class="px-4 py-2 rounded-DEFAULT bg-primary text-on-primary font-label-md text-label-md hover:bg-primary-fixed transition-colors flex items-center gap-1 shrink-0"
         href="${esc(safeUrl(job.url))}" target="_blank" rel="noopener noreferrer sponsored"
         data-sponsor-click="${esc(job.id)}">
        <span>Apply</span>
        <span class="material-symbols-outlined text-[16px]">open_in_new</span>
      </a>
    </div>
    <div class="mt-4 pt-3 flex flex-wrap items-center justify-between gap-2.5">
      <div class="flex flex-wrap items-center gap-1.5">${tags}</div>
      <span class="font-mono-data text-body-sm ${sal ? "text-primary font-semibold" : "text-outline"}">${sal ? esc(sal) : "Salary not disclosed"}</span>
    </div>
  </article>`;
  }

  /* ---------------- preview ---------------- */
  function renderPreview(job) {
    const body = document.getElementById("preview-body");
    const empty = document.getElementById("preview-empty");
    if (!body || !empty) return;

    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };

    set("pv-company", companyLabel(job));
    set("pv-title", job.title);
    set("pv-source", job.source + " feed");
    set("pv-salary", salaryOf(job) || "Not disclosed");
    set("pv-region", REGION_LABEL[job.region] || "Worldwide");
    set("pv-ats", SOURCE_LABEL[job.source] || job.source || "unknown");
    set("pv-posted", ago(job.posted_at) || "unknown");

    const apply = document.getElementById("pv-apply");
    if (apply) {
      apply.href = safeUrl(job.url);
      apply.innerHTML = `Apply on ${esc(companyLabel(job))} <span class="material-symbols-outlined text-[18px]">launch</span>`;
    }

    const tagWrap = document.getElementById("pv-tags");
    if (tagWrap) {
      tagWrap.innerHTML = (job.tags || [])
        .map((t) => `<span class="px-2 py-0.5 rounded-DEFAULT bg-surface-container text-on-surface-variant font-label-sm text-label-sm font-mono">${esc(t)}</span>`)
        .join("") || '<span class="font-body-sm text-body-sm text-outline">No tags</span>';
    }

    empty.classList.add("hidden");
    body.classList.remove("hidden");
  }

  /* ---------------- filtros ---------------- */
  const REG_ACTIVE = ["bg-primary", "text-on-primary", "font-semibold", "shadow-sm"];
  const REG_IDLE = ["bg-surface-container", "text-on-surface", "hover:bg-surface-container-high", "transition-colors"];
  const CAT_ACTIVE = ["bg-secondary-container/40", "text-secondary"];
  const CAT_IDLE = ["bg-surface-container", "text-on-surface-variant", "hover:text-on-surface", "hover:bg-surface-container-high", "transition-colors"];

  function paint(btn, active) {
    const A = btn.dataset.filter === "region" ? REG_ACTIVE : CAT_ACTIVE;
    const I = btn.dataset.filter === "region" ? REG_IDLE : CAT_IDLE;
    [...A, ...I].forEach((c) => btn.classList.remove(c));
    (active ? A : I).forEach((c) => btn.classList.add(c));
  }

  const regionBtns = () => Array.from(document.querySelectorAll('[data-filter="region"]'));
  const catBtns = () => Array.from(document.querySelectorAll('[data-filter="category"]'));

  function paintAll() {
    regionBtns().forEach((b) => paint(b, b.dataset.value === state.region));
    catBtns().forEach((b) => paint(b, norm(b.dataset.value) === norm(state.category)));
  }

  function matches(job, ignoreRegion) {
    if (state.hideGhost && job.ghost) return false;
    if (state.q) {
      // Every word must appear somewhere on the row: "python ai engineer"
      // should find a Python AI engineer, not require that exact phrase.
      const hay = `${job.title} ${job.company} ${(job.tags || []).join(" ")} ${job.region} ${job.category} ${job.level}`.toLowerCase();
      const tokens = state.q.split(/\s+/).filter(Boolean);
      if (!tokens.every((token) => hay.includes(token))) return false;
    }
    if (!ignoreRegion && state.region !== "all" && job.region !== state.region) return false;
    if (state.category && norm(categoryOf(job)) !== norm(state.category)) return false;
    if (state.salary && salaryNum(job) < state.salary) return false;
    if (state.seniority && levelOf(job) !== state.seniority) return false;
    if (state.worktype && worktypeOf(job) !== state.worktype) return false;
    return true;
  }

  function ordered() {
    const arr = JOBS.slice();
    if (state.sort === "Highest salary") {
      arr.sort((a, b) => salaryNum(b) - salaryNum(a));
    } else if (state.sort === "Entry-friendly first") {
      arr.sort((a, b) => {
        const lw = (LEVEL_WEIGHT[levelOf(a)] ?? 2) - (LEVEL_WEIGHT[levelOf(b)] ?? 2);
        if (lw !== 0) return lw;
        const aw = (ACCESSIBLE.has(categoryOf(a)) ? 0 : 1) - (ACCESSIBLE.has(categoryOf(b)) ? 0 : 1);
        if (aw !== 0) return aw;
        return new Date(b.posted_at || 0) - new Date(a.posted_at || 0);
      });
    } else {
      arr.sort((a, b) => new Date(b.posted_at || 0) - new Date(a.posted_at || 0));
    }
    return arr;
  }

  /* ---------------- paginacion ---------------- */
  function buildPager(total) {
    const wrap = document.getElementById("pagination-controls");
    if (!wrap) return;
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (state.page > pages) state.page = pages;
    wrap.innerHTML = "";

    const mk = (label, page, active, disabled) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.className = active
        ? "px-3 py-1.5 rounded-DEFAULT bg-primary text-on-primary font-bold text-body-sm"
        : "px-3 py-1.5 rounded-DEFAULT bg-surface-container text-on-surface-variant hover:text-on-surface text-body-sm transition-colors";
      if (disabled) {
        b.disabled = true;
        b.classList.add("opacity-40", "cursor-not-allowed");
      } else {
        b.addEventListener("click", () => {
          state.page = page;
          render();
          container.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
      wrap.appendChild(b);
    };

    const dot = () => {
      const s = document.createElement("span");
      s.className = "text-outline px-1";
      s.textContent = "…";
      wrap.appendChild(s);
    };

    mk("Previous", Math.max(1, state.page - 1), false, state.page === 1);
    for (let i = 1; i <= pages; i++) {
      if (pages > 7 && i !== 1 && i !== pages && Math.abs(i - state.page) > 2) {
        if (i === 2 || i === pages - 1) dot();
        continue;
      }
      mk(String(i), i, i === state.page, false);
    }
    mk("Next", Math.min(pages, state.page + 1), false, state.page === pages);
  }

  /* ---------------- render ---------------- */
  function render() {
    const matched = ordered().filter((j) => matches(j, false));
    const pages = Math.max(1, Math.ceil(matched.length / PAGE_SIZE));
    if (state.page > pages) state.page = pages;

    const from = (state.page - 1) * PAGE_SIZE;
    const slice = matched.slice(from, from + PAGE_SIZE);

    const pinned = SPONSORED.filter((j) => matches(j, false)).slice(0, 2);
    pinned.forEach((j) => trackSponsored(j.id, "view"));
    container.innerHTML = pinned.map(sponsoredCard).join("") + slice.map((j) => cardHTML(j, JOBS.indexOf(j))).join("");

    const empty = document.getElementById("jobs-empty-state");
    if (empty) empty.classList.toggle("hidden", matched.length > 0);

    const counter = document.getElementById("results-count");
    if (counter) counter.textContent = matched.length.toLocaleString("en-US");

    const range = document.getElementById("results-range");
    if (range) {
      const a = matched.length ? from + 1 : 0;
      range.textContent = `Showing ${a.toLocaleString("en-US")} - ${(from + slice.length).toLocaleString("en-US")} of ${matched.length.toLocaleString("en-US")} indexed openings`;
    }

    // badges de region con conteos reales
    regionBtns().forEach((btn) => {
      const spans = btn.querySelectorAll("span");
      if (spans.length < 2) return;
      const v = btn.dataset.value;
      const n = v === "all" ? JOBS.filter((j) => matches(j, true)).length : JOBS.filter((j) => matches(j, true) && j.region === v).length;
      spans[spans.length - 1].textContent = n.toLocaleString("en-US");
    });

    buildPager(matched.length);
    initZoom();
  }

  /* ---------------- eventos ---------------- */
  function wire() {
    const input = document.getElementById("job-query-input");
    let t;
    input?.addEventListener("input", (e) => {
      clearTimeout(t);
      t = setTimeout(() => {
        state.q = e.target.value.trim().toLowerCase();
        state.page = 1;
        render();
      }, 200);
    });
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        input?.focus();
        input?.select();
      }
    });
    input?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        state.q = input.value.trim().toLowerCase();
        state.page = 1;
        render();
      }
    });

    regionBtns().forEach((b) =>
      b.addEventListener("click", () => {
        state.region = b.dataset.value;
        state.page = 1;
        paintAll();
        render();
      })
    );

    catBtns().forEach((b) =>
      b.addEventListener("click", () => {
        const v = b.dataset.value;
        state.category = norm(state.category) === norm(v) ? "" : v;
        state.page = 1;
        paintAll();
        render();
      })
    );

    document.getElementById("salary-select")?.addEventListener("change", (e) => {
      state.salary = parseInt(e.target.value || "0", 10) * 1000;
      state.page = 1;
      render();
    });
    document.getElementById("worktype-select")?.addEventListener("change", (e) => {
      state.worktype = e.target.value;
      state.page = 1;
      render();
    });
    document.getElementById("seniority-select")?.addEventListener("change", (e) => {
      state.seniority = e.target.value;
      state.page = 1;
      render();
    });
    document.getElementById("sort-select")?.addEventListener("change", (e) => {
      state.sort = e.target.value;
      state.page = 1;
      render();
    });
    document.getElementById("apply-filters-btn")?.addEventListener("click", () => {
      state.q = (input?.value || "").trim().toLowerCase();
      state.page = 1;
      render();
    });

    document.getElementById("ghost-toggle")?.addEventListener("change", (e) => {
      state.hideGhost = e.target.checked;
      state.page = 1;
      render();
    });

    container.addEventListener("click", (e) => {
      const paid = e.target.closest("[data-sponsor-click]");
      if (paid) {
        trackSponsored(paid.getAttribute("data-sponsor-click"), "click");
        return; // let the link open
      }
      const card = e.target.closest("article[data-idx]");
      if (!card) return;
      if (e.target.closest("a")) return; // dejar que el link externo funcione
      const job = JOBS[parseInt(card.dataset.idx, 10)];
      if (job) renderPreview(job);
    });
  }

  /* ---------------- estado vacio ---------------- */
  function ensureEmptyState() {
    if (document.getElementById("jobs-empty-state")) return;
    const div = document.createElement("div");
    div.id = "jobs-empty-state";
    div.className = "hidden text-center py-16 px-6 rounded-DEFAULT bg-surface-container-low border border-outline-variant/30";
    div.innerHTML =
      '<span class="material-symbols-outlined text-[36px] text-outline block mb-3">search_off</span>' +
      '<p class="font-headline-sm text-headline-sm text-on-surface mb-1">No openings match your filters</p>' +
      '<p class="font-body-sm text-body-sm text-on-surface-variant">Try clearing the search, lowering the salary floor or widening the region.</p>';
    container.parentNode.insertBefore(div, container.nextSibling);
  }

  /* ---------------- arranque ---------------- */
  // Live feed: Supabase first (paged, anon key — public by design), then the
  // static jobs.json baked at deploy time as a fallback. The first batch paints
  // immediately and the rest arrives in the background, so a 6k-row feed never
  // shows a blank screen.
  const SUPA_URL = SUPABASE_URL;
  const SUPA_ANON = SUPABASE_ANON_KEY;   // anon key: public by design, RLS guards the data
  const COLS = "title,company,salary_min,salary_max,url,source,tags,posted_at,region,category,level,ghost";

  function restUrl(offset) {
    return SUPA_URL + "/rest/v1/jobs?select=" + COLS +
      "&order=posted_at.desc.nullslast&limit=" + LOAD_BATCH + (offset ? "&offset=" + offset : "");
  }

  async function loadJobs(onBatch) {
    const headers = { "apikey": SUPA_ANON, "Authorization": "Bearer " + SUPA_ANON };
    const rows = [];
    for (let off = 0; off <= 20000; off += LOAD_BATCH) {
      if (off) await new Promise((r) => setTimeout(r, BATCH_PAUSE));
      let res;
      try {
        res = await fetch(restUrl(off), { headers: headers, cache: "no-cache" });
        if (!res.ok) throw new Error("HTTP " + res.status);
      } catch (err) {
        if (!rows.length) throw err;          // nothing on screen yet -> static fallback
        console.warn("[radarjobs] stopped paging:", err.message);
        break;
      }
      const batch = await res.json();
      rows.push.apply(rows, batch);
      if (rows.length) onBatch(rows.slice());
      if (batch.length < LOAD_BATCH) break;
    }
    return rows;
  }

  function applyDeepLinks() {
    const params = new URLSearchParams(location.search);
    const q = params.get("q");
    if (q) {
      state.q = q.trim().toLowerCase();
      const input = document.getElementById("job-query-input");
      if (input) input.value = q.trim();
    }
    const region = params.get("region");
    if (region && ["worldwide", "latam", "america", "eu"].includes(region)) state.region = region;
    const category = params.get("category");
    if (category) state.category = category.trim();
    paintAll();
  }

  function loadingRows(n) {
    container.innerHTML = Array.from({ length: n }, () => '<div class="skeleton" style="height:96px"></div>').join("");
  }

  async function boot() {
    ensureEmptyState();
    wire();
    paintAll();
    applyDeepLinks();
    loadingRows(5);
    loadSponsored();

    try {
      const all = await loadJobs(function (rows) {
        JOBS = rows;
        render();
      });
      JOBS = all;
      paintAll();
      render();
      console.log("[radarjobs] loaded", JOBS.length, "jobs");
    } catch (err) {
      console.error("[radarjobs] live feed failed, using jobs.json:", err.message);
      try {
        const res = await fetch("jobs.json", { cache: "no-cache" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        JOBS = await res.json();
        applyDeepLinks();
        render();
        console.log("[radarjobs] fallback feed:", JOBS.length, "jobs");
      } catch (err2) {
        container.innerHTML =
          '<div class="p-10 text-center"><p class="font-headline-sm text-on-surface mb-1">Could not load the job feed</p>' +
          '<p class="font-body-sm text-on-surface-variant">' + esc(err2.message) + "</p></div>";
      }
    }
  }

  boot();
})();
