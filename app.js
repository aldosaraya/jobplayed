/* RadarJobs — shared UI module.
 * Owns: session-aware nav + account menu, single-active hover zoom,
 * live feed previews (home), and the digest simulator backed by the same
 * Postgres function the weekly email uses (rj_pick_jobs).
 */
import { supabase, getSession, signOut } from "./auth.js";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** A feed URL is untrusted input: never let javascript:/data: become an href. */
export const safeUrl = (url) => (/^https?:\/\//i.test(String(url ?? "")) ? String(url) : "#");

const REGION_LABEL = { worldwide: "Worldwide", latam: "LatAm only", america: "Americas", eu: "Europe" };

export function money(min, max) {
  const a = String(min ?? "").replace(/[^0-9]/g, "");
  if (!a) return null;
  const b = String(max ?? "").replace(/[^0-9]/g, "");
  const fmt = (v) => "$" + Number(v).toLocaleString("en-US");
  return b && b !== a ? `${fmt(a)} – ${fmt(b)}` : fmt(a);
}

export function ago(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.floor(mins / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  return `${d} d ago`;
}

/* The exact selection the Monday digest email makes — same RPC, same filters. */
export async function pickJobs({ role = "tech", region = "worldwide", salary = 0, limit = 3 } = {}) {
  const { data, error } = await supabase.rpc("rj_pick_jobs", {
    p_role: role, p_region: region, p_salary: salary, p_limit: limit,
  });
  if (error) throw error;
  return data || [];
}

/* ------------------------------------------------------------------ nav */

async function mountSession() {
  let session = null;
  try { session = await getSession(); } catch { session = null; }

  document.documentElement.classList.toggle("auth", Boolean(session));
  document.documentElement.classList.toggle("guest", !session);

  const slot = $("#nav-auth");
  if (!slot) return session;

  if (!session) {
    slot.innerHTML =
      '<a class="nav-link" href="login.html">Log in</a>' +
      '<a class="nav-cta" href="signup.html">Sign up free</a>';
    return null;
  }

  const email = session.user.email || "account";
  const initials = email.slice(0, 1).toUpperCase();
  slot.innerHTML = `
    <div class="relative">
      <button class="nav-user" id="acct-btn" type="button" aria-haspopup="menu" aria-expanded="false">
        <span class="avatar">${esc(initials)}</span>
        <span class="who hidden md:inline">${esc(email)}</span>
        <span class="material-symbols-outlined" style="font-size:18px;color:#988d9f">expand_more</span>
      </button>
      <div class="nav-menu" id="acct-menu" role="menu" hidden>
        <p class="menu-label">Signed in</p>
        <a role="menuitem" href="account.html"><span class="material-symbols-outlined">dashboard</span>Dashboard</a>
        <a role="menuitem" href="account.html#alerts"><span class="material-symbols-outlined">tune</span>My alerts</a>
        <a role="menuitem" href="account.html#subscription"><span class="material-symbols-outlined">credit_card</span>Subscription</a>
        <a role="menuitem" href="jobs.html"><span class="material-symbols-outlined">work</span>Explore jobs</a>
        <button role="menuitem" class="danger" type="button" id="acct-signout"><span class="material-symbols-outlined">logout</span>Sign out</button>
      </div>
    </div>`;

  const btn = $("#acct-btn", slot);
  const menu = $("#acct-menu", slot);
  const close = () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    btn.setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  $("#acct-signout", slot).addEventListener("click", () => signOut());

  $$("[data-user-email]").forEach((el) => { el.textContent = email; });
  return session;
}

/* ------------------------------------------------------ hover zoom (one at a time) */

function initZoom() {
  $$("[data-zoom-group]").forEach((group) => {
    const items = $$("[data-zoom]", group);
    let active = null;
    const activate = (item) => {
      if (active === item) return;
      if (active) active.classList.remove("is-zoom");
      active = item;
      if (item) item.classList.add("is-zoom");
    };
    items.forEach((item) => {
      item.addEventListener("mouseenter", () => activate(item));
      item.addEventListener("focusin", () => activate(item));
    });
    group.addEventListener("mouseleave", () => activate(null));
  });
}

/* ------------------------------------------------------ live previews */

const rowHTML = (job) => {
  const salary = money(job.salary_min, job.salary_max);
  const tags = (job.tags || []).slice(0, 3)
    .map((t) => `<span class="font-label-sm text-label-sm px-2 py-1 rounded-DEFAULT bg-surface-container text-on-surface-variant">${esc(t)}</span>`)
    .join("");
  return `<a class="block p-5 rounded-xl bg-surface-container-low hover:bg-surface-container transition-colors" href="${esc(safeUrl(job.url))}" target="_blank" rel="noopener noreferrer">
    <div class="flex flex-wrap items-center gap-2 mb-1">
      <span class="font-headline-sm text-headline-sm text-on-surface font-semibold">${esc(job.title)}</span>
      <span class="font-mono-data text-label-sm px-2 py-0.5 rounded-DEFAULT bg-surface-container-high text-secondary">${esc(job.source || "ats")}</span>
    </div>
    <div class="flex flex-wrap items-center gap-2 font-body-sm text-body-sm text-on-surface-variant">
      <span class="text-on-surface font-medium">${esc(job.company)}</span>
      <span aria-hidden="true">·</span>
      <span>${esc(REGION_LABEL[job.region] || "Worldwide")}</span>
      ${salary ? `<span aria-hidden="true">·</span><span class="font-mono-data text-primary">${esc(salary)}</span>` : ""}
      ${job.posted_at ? `<span aria-hidden="true">·</span><span class="font-mono-data">${esc(ago(job.posted_at))}</span>` : ""}
    </div>
    ${tags ? `<div class="flex flex-wrap gap-1.5 mt-3">${tags}</div>` : ""}
  </a>`;
};

const skeletonRows = (n) =>
  Array.from({ length: n }, () => '<div class="skeleton" style="height:88px"></div>').join("");

async function initHomeFeed() {
  const box = $("#home-feed");
  if (!box) return;
  box.innerHTML = skeletonRows(3);
  try {
    const { data, error } = await supabase.from("jobs")
      .select("title,company,salary_min,salary_max,url,source,tags,posted_at,region")
      .eq("ghost", false)
      .order("posted_at", { ascending: false, nullsFirst: false })
      .limit(3);
    if (error) throw error;
    if (!data || !data.length) throw new Error("empty");
    box.innerHTML = data.map(rowHTML).join("");
  } catch {
    box.innerHTML = '<p class="font-body-sm text-body-sm text-on-surface-variant p-4">Live feed unavailable right now — open <a class="text-primary hover:underline" href="jobs.html">all openings</a>.</p>';
  }
}

/* Digest simulator: real rows, real count, driven by the visible filters. */
function initDigest() {
  const root = $("[data-digest]");
  if (!root) return;

  const list = $("#digest-list", root);
  const countEl = $("#digest-count", root);
  const titleEl = $("#digest-title", root);
  const subEl = $("#digest-sub", root);
  const roleSel = $("#subscriber-role");
  const regionSel = $("#subscriber-region");
  const salaryEl = $("#salary-slider");
  const REGION_MAP = { "latam-world": "worldwide", "latam-only": "latam", "us-remote": "america", eu: "eu" };
  let timer;

  async function refresh() {
    const role = roleSel ? roleSel.value : "tech";
    const region = regionSel ? REGION_MAP[regionSel.value] || "worldwide" : "worldwide";
    const salary = salaryEl ? Number(salaryEl.value) || 0 : 0;
    list.innerHTML = skeletonRows(3);
    try {
      const matches = await pickJobs({ role, region, salary, limit: 25 });
      const jobs = matches.slice(0, 3);
      if (countEl) {
        countEl.textContent = matches.length ? `${matches.length}${matches.length >= 25 ? "+" : ""} matches` : "no matches";
      }
      if (!jobs.length) {
        list.innerHTML = '<p class="font-body-sm text-body-sm text-on-surface-variant p-4">No openings match these filters yet. Lower the salary floor or widen the region.</p>';
        return;
      }
      list.innerHTML = jobs.map((j) => {
        const salary = money(j.salary_min, j.salary_max);
        return `<div class="p-4 rounded-lg bg-surface-container" data-zoom>
          <div class="flex flex-wrap items-start justify-between gap-2">
            <p class="font-headline-sm text-body-md text-on-surface font-semibold">${esc(j.title)}</p>
            <span class="font-mono-data text-body-md text-primary font-semibold">${salary ? esc(salary) : "Not disclosed"}</span>
          </div>
          <p class="font-body-sm text-body-sm text-on-surface-variant mt-1">
            ${esc(j.company)} · ${esc(REGION_LABEL[j.region] || "Worldwide")}
            ${j.posted_at ? ` · ${esc(ago(j.posted_at))}` : ""}
          </p>
        </div>`;
      }).join("");
      initZoom();
      if (subEl) subEl.textContent = "Live rows — the same matching query that builds Monday's email.";
    } catch {
      list.innerHTML = '<p class="font-body-sm text-body-sm text-on-surface-variant p-4">Preview unavailable right now.</p>';
    }
  }

  const schedule = () => { clearTimeout(timer); timer = setTimeout(refresh, 280); };
  [roleSel, regionSel, salaryEl].forEach((el) => el && el.addEventListener("input", schedule));
  refresh();
}

/* ------------------------------------------------------ start */
function stampYear() {
  const y = String(new Date().getFullYear());
  document.querySelectorAll("[data-year]").forEach((el) => { el.textContent = y; });
}

function boot() {
  stampYear();
  initZoom();
  mountSession();
  initHomeFeed();
  initDigest();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();

export { initZoom, mountSession };
