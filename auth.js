// Shared Supabase client + auth helpers.
// The anon key is public by design (it ships in the frontend); RLS is what protects data.

import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm";

export const SUPABASE_URL = "https://juijmkcfbjthohjpgflr.supabase.co";
export const SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp1aWpta2NmYmp0aG9oanBnZmxyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI1NzQ3MDEsImV4cCI6MjEwODE1MDcwMX0.nXQcCeRno_SXhQziizZ4sM4vQZxLKqgO6uv1tWcmAAU";

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: { persistSession: true, autoRefreshToken: true },
});

/** Current session or null. */
export async function getSession() {
  const { data, error } = await supabase.auth.getSession();
  if (error) return null;
  return data.session ?? null;
}

/** Creates the account. The DB trigger creates profiles + subscriptions rows (status 'pending'). */
export async function signUp({ email, password, role = "tech", region = "worldwide", salaryMin = 70000 }) {
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      data: { role, region, salary_min: salaryMin },
      emailRedirectTo: `${location.origin}/login.html`,
    },
  });
  if (error) throw error;
  return data;
}

/** Email + password login. */
export async function signIn(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return data;
}

/** Sign out and go back to the landing page. */
export async function signOut() {
  await supabase.auth.signOut();
  location.href = "index.html";
}

/** Everything the account page needs: { session, profile, subscription, isActive }. */
export async function getAccount() {
  const session = await getSession();
  if (!session) return { session: null, profile: null, subscription: null, isActive: false };

  const uid = session.user.id;
  const [profileRes, subRes] = await Promise.all([
    supabase
      .from("profiles")
      .select("id,email,role,job_role,region,salary_min,digest_opt_out")
      .eq("id", uid)
      .maybeSingle(),
    supabase
      .from("subscriptions")
      .select("status,plan,period_ends,last_digest_at")
      .eq("id", uid)
      .maybeSingle(),
  ]);

  return {
    session,
    profile: profileRes.data ?? null,
    subscription: subRes.data ?? null,
    isActive: subRes.data?.status === "active",
  };
}

/** Persists the weekly-alert filters the digest function reads. */
export async function saveAlerts({ job_role, region, salary_min, digest_opt_out }) {
  const session = await getSession();
  if (!session) throw new Error("Not signed in");

  const { data, error } = await supabase
    .from("profiles")
    .update({
      job_role,
      region,
      salary_min,
      digest_opt_out,
      updated_at: new Date().toISOString(),
    })
    .eq("id", session.user.id)
    .select("job_role,region,salary_min,digest_opt_out")
    .maybeSingle();

  if (error) throw error;
  return data;
}

/** Redirects to login when there is no session. Use on private pages. */
export async function requireAuth(redirectTo = "login.html") {
  const session = await getSession();
  if (!session) {
    const next = encodeURIComponent(location.pathname.split("/").pop() || "account.html");
    location.href = `${redirectTo}?next=${next}`;
    return null;
  }
  return session;
}

/**
 * A `?next=` value is attacker-controlled. Only same-site relative paths may be
 * followed — otherwise the login page becomes an open redirect.
 */
export function safeNext(raw, fallback = "jobs.html") {
  if (!raw) return fallback;
  const value = String(raw).trim();
  if (!value) return fallback;
  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) return fallback; // http:, javascript:, data:
  if (value.startsWith("//") || value.includes("\\") || value.includes("..")) return fallback;
  if (!/^[a-z0-9._~!$&'()*+,;=:@%/?#-]+$/i.test(value)) return fallback;
  return value;
}

/** Sends the Supabase recovery email (link lands back on login.html?recovery=1). */
export async function requestPasswordReset(email) {
  const { error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo: `${location.origin}/login.html?recovery=1`,
  });
  if (error) throw error;
}

/** Sets a new password for the recovery session. */
export async function updatePassword(password) {
  const { error } = await supabase.auth.updateUser({ password });
  if (error) throw error;
}

