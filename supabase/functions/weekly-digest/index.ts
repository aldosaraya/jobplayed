import "jsr:@supabase/functions-js/edge-runtime.d.ts";

/**
 * weekly-digest — sends the Monday Radar VIP digest via Brevo.
 *
 * POST /weekly-digest   body: { dry_run?: boolean, limit?: number }
 *   Requires header: x-digest-secret: <DIGEST_SECRET>
 *
 *   QA without touching real users:
 *   body: { test: true, dry_run: true, test_email?, test_role?, test_region?, test_salary? }
 *
 * GET /weekly-digest?unsubscribe=<user_id>&token=<hmac>
 *   One-click opt-out, no login required.
 */

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const BREVO_KEY = Deno.env.get("BREVO_API_KEY") ?? "";
const FROM_EMAIL = Deno.env.get("FROM_EMAIL") ?? "alerts@example.com";
const FROM_NAME = Deno.env.get("FROM_NAME") ?? "RadarJobs";
const DIGEST_SECRET = Deno.env.get("DIGEST_SECRET") ?? "change-me";
const SITE = Deno.env.get("SITE_URL") ?? "https://radarjobs.pages.dev";

type Job = {
  title: string; company: string; url: string;
  salary_min: string | null; salary_max: string | null;
  region: string; posted_at: string | null;
};

type Sub = {
  user_id: string; email: string; job_role: string | null;
  region: string | null; salary_min: number | null; plan: string | null;
};

function sbHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
  h["api" + "key"] = SERVICE_KEY;
  h["Authorization"] = "Bearer " + SERVICE_KEY;
  return h;
}

function brevoHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json", Accept: "application/json" };
  h["api" + "-key"] = BREVO_KEY;
  return h;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

async function rpc<T>(fn: string, args: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/${fn}`, {
    method: "POST", headers: sbHeaders(), body: JSON.stringify(args),
  });
  if (!res.ok) throw new Error(`rpc ${fn} ${res.status}: ${await res.text()}`);
  return await res.json() as T;
}

async function hmacHex(msg: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(DIGEST_SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const esc = (s: string) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function salaryLine(j: Job): string {
  if (!j.salary_min) return "Salary not disclosed";
  if (!j.salary_max || j.salary_max === j.salary_min) return `${j.salary_min} USD`;
  return `${j.salary_min} - ${j.salary_max} USD`;
}

function renderEmail(sub: Sub, jobs: Job[], unsubUrl: string): { subject: string; html: string } {
  const roleLabel: Record<string, string> = {
    tech: "engineering", data: "data", design: "design", product: "product", devops: "devops",
    support: "customer support", marketing: "marketing", sales: "sales",
    admin: "admin & ops", writing: "writing", "data-entry": "data entry",
  };
  const label = roleLabel[(sub.job_role ?? "tech").toLowerCase()] ?? "engineering";
  const region = sub.region ?? "worldwide";
  const subject = `Your Radar: ${jobs.length} new ${label} roles (${region})`;

  const rows = jobs.map((j, i) => `
    <tr><td style="padding:14px 0;border-bottom:1px solid #e8e6ef">
      <div style="font:600 15px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;color:#121317">${i + 1}. ${esc(j.title)}</div>
      <div style="font:400 13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#5b5568;margin-top:2px">${esc(j.company)} &middot; ${esc(j.region)} &middot; ${esc(salaryLine(j))}</div>
      <a href="${esc(j.url)}" style="display:inline-block;margin-top:8px;font:600 13px -apple-system,Segoe UI,Roboto,sans-serif;color:#6d28d9;text-decoration:none">View posting &rarr;</a>
    </td></tr>`).join("");

  const floor = sub.salary_min
    ? ` and a floor of <strong>$${sub.salary_min.toLocaleString("en-US")}</strong>`
    : "";

  const html = `<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f6f5fa">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f5fa;padding:24px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e8e6ef">
<tr><td style="background:#121317;padding:20px 24px">
  <span style="font:700 15px/1 -apple-system,Segoe UI,Roboto,sans-serif;color:#ddb7ff;letter-spacing:.08em">RADARJOBS</span>
  <span style="float:right;font:500 12px -apple-system,Segoe UI,Roboto,sans-serif;color:#988d9f">Weekly digest</span>
</td></tr>
<tr><td style="padding:24px 24px 8px">
  <h1 style="margin:0 0 6px;font:600 20px/1.3 -apple-system,Segoe UI,Roboto,sans-serif;color:#121317">${jobs.length} fresh ${esc(label)} roles</h1>
  <p style="margin:0;font:400 14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:#5b5568">Matched to <strong>${esc(region)}</strong>${floor}. Postings older than 30 days are filtered out.</p>
</td></tr>
<tr><td style="padding:8px 24px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">${rows}</table></td></tr>
<tr><td style="padding:20px 24px 24px">
  <a href="${SITE}/jobs" style="display:inline-block;background:#ddb7ff;color:#490080;font:700 14px -apple-system,Segoe UI,Roboto,sans-serif;padding:11px 18px;border-radius:8px;text-decoration:none">Open the full feed</a>
</td></tr>
<tr><td style="padding:16px 24px;background:#f6f5fa;border-top:1px solid #e8e6ef">
  <p style="margin:0;font:400 11px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:#8b8598">
    You receive this because you have an active Radar VIP subscription.<br>
    <a href="${esc(unsubUrl)}" style="color:#6d28d9">Unsubscribe from the digest</a>
    &middot; <a href="${SITE}/pricing" style="color:#6d28d9">Manage subscription</a>
  </p>
</td></tr>
</table></td></tr></table></body></html>`;

  return { subject, html };
}

async function sendViaBrevo(to: string, subject: string, html: string) {
  if (!BREVO_KEY) throw new Error("BREVO_API_KEY not set");
  const res = await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST", headers: brevoHeaders(),
    body: JSON.stringify({ sender: { name: FROM_NAME, email: FROM_EMAIL }, to: [{ email: to }], subject, htmlContent: html }),
  });
  if (!res.ok) throw new Error(`brevo ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return await res.json() as { messageId?: string };
}

async function logDigest(userId: string, email: string, count: number, status: string, error?: string) {
  await fetch(`${SUPABASE_URL}/rest/v1/digest_log`, {
    method: "POST", headers: sbHeaders({ Prefer: "return=minimal" }),
    body: JSON.stringify({ user_id: userId, email, job_count: count, status, error: error ?? null }),
  });
}

async function markSent(userId: string) {
  await fetch(`${SUPABASE_URL}/rest/v1/subscriptions?id=eq.${userId}`, {
    method: "PATCH", headers: sbHeaders({ Prefer: "return=minimal" }),
    body: JSON.stringify({ last_digest_at: new Date().toISOString() }),
  });
}

Deno.serve(async (req) => {
  const url = new URL(req.url);

  /* ---------- one-click unsubscribe ---------- */
  if (req.method === "GET" && url.searchParams.has("unsubscribe")) {
    const uid = url.searchParams.get("unsubscribe")!;
    const token = url.searchParams.get("token") ?? "";
    const expected = await hmacHex(uid);
    if (token !== expected) return new Response("Invalid unsubscribe link.", { status: 400 });

    await fetch(`${SUPABASE_URL}/rest/v1/profiles?id=eq.${uid}`, {
      method: "PATCH", headers: sbHeaders({ Prefer: "return=minimal" }),
      body: JSON.stringify({ digest_opt_out: true }),
    });

    return new Response(
      `<html><body style="font:16px system-ui;padding:40px;background:#121317;color:#e3e2e8">
         <h2 style="color:#ddb7ff">You're unsubscribed</h2>
         <p>You won't receive the weekly digest anymore. Your subscription is unchanged.</p></body></html>`,
      { headers: { "Content-Type": "text/html" } },
    );
  }

  /* ---------- send ---------- */
  if (req.method !== "POST") return json({ error: "method not allowed" }, 405);

  const secret = req.headers.get("x-digest-secret") ?? "";
  if (secret !== DIGEST_SECRET) return json({ error: "unauthorized" }, 401);

  let body: {
    dry_run?: boolean; limit?: number;
    test?: boolean; test_email?: string; test_role?: string; test_region?: string; test_salary?: number;
  } = {};
  try { body = await req.json(); } catch { /* empty body ok */ }

  const subs = await rpc<Sub[]>("rj_digest_audience", {});
  const audience = body.limit ? subs.slice(0, body.limit) : subs;

  /* QA: render a synthetic digest without touching real users */
  const target: Sub[] = body.test
    ? [{
        user_id: "00000000-0000-0000-0000-000000000000",
        email: body.test_email ?? "preview@example.com",
        job_role: body.test_role ?? "tech",
        region: body.test_region ?? "worldwide",
        salary_min: body.test_salary ?? 0,
        plan: "monthly",
      }]
    : audience;

  const results: Array<Record<string, unknown>> = [];
  let sent = 0, skipped = 0, failed = 0;

  for (const sub of target) {
    try {
      const jobs = await rpc<Job[]>("rj_pick_jobs", {
        p_role: sub.job_role ?? "tech", p_region: sub.region ?? "worldwide",
        p_salary: sub.salary_min ?? 0, p_limit: 10,
      });

      if (!jobs.length) {
        skipped++;
        results.push({ email: sub.email, status: "skipped", reason: "no matching jobs" });
        if (!body.dry_run && !body.test) await logDigest(sub.user_id, sub.email, 0, "skipped", "no matching jobs");
        continue;
      }

      const token = await hmacHex(sub.user_id);
      const unsubUrl = `${SUPABASE_URL}/functions/v1/weekly-digest?unsubscribe=${sub.user_id}&token=${token}`;
      const { subject, html } = renderEmail(sub, jobs, unsubUrl);

      if (body.dry_run || body.test) {
        results.push({ email: sub.email, status: "dry_run", jobs: jobs.length, subject, html });
        continue;
      }

      await sendViaBrevo(sub.email, subject, html);
      await logDigest(sub.user_id, sub.email, jobs.length, "sent");
      await markSent(sub.user_id);
      sent++;
      results.push({ email: sub.email, status: "sent", jobs: jobs.length });
    } catch (err) {
      failed++;
      const msg = err instanceof Error ? err.message : String(err);
      if (!body.dry_run && !body.test) await logDigest(sub.user_id, sub.email, 0, "failed", msg);
      results.push({ email: sub.email, status: "failed", error: msg });
    }
  }

  return json({
    ok: true, dry_run: Boolean(body.dry_run || body.test), audience: target.length,
    sent, skipped, failed, brevo_configured: Boolean(BREVO_KEY),
    results: results.slice(0, body.dry_run || body.test ? 1 : 50),
  });
});
