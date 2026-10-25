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

/** Subscription state for the logged-in user: { profile, subscription, isActive }. */
export async function getSubscriptionState() {
  const session = await getSession();
  if (!session) return { profile: null, subscription: null, isActive: false };

  const uid = session.user.id;
  const [profileRes, subRes] = await Promise.all([
    supabase.from("profiles").select("id,email,role").eq("id", uid).maybeSingle(),
    supabase.from("subscriptions").select("status,plan,period_ends").eq("id", uid).maybeSingle(),
  ]);

  return {
    profile: profileRes.data ?? null,
    subscription: subRes.data ?? null,
    isActive: subRes.data?.status === "active",
  };
}

/** Redirects to login when there is no session. Use on private pages. */
export async function requireAuth(redirectTo = "login.html") {
  const session = await getSession();
  if (!session) {
    const next = encodeURIComponent(location.pathname.split("/").pop() || "jobs.html");
    location.href = `${redirectTo}?next=${next}`;
    return null;
  }
  return session;
}

/**
 * Swaps the nav "Log In / Sign Up" links for the user's email + "Sign out"
 * when a session exists. No-ops on pages without [data-auth-link].
 */
export async function mountNavState() {
  const session = await getSession();
  const authLinks = document.querySelectorAll("[data-auth-link]");
  if (!authLinks.length) return;

  if (!session) {
    authLinks.forEach((el) => {
      el.textContent = el.dataset.authLink === "login" ? "Log In" : "Sign Up";
      el.href = el.dataset.authLink === "login" ? "login.html" : "signup.html";
    });
    return;
  }

  const email = session.user.email ?? "account";
  authLinks.forEach((el, i) => {
    if (i === 0) {
      el.textContent = email;
      el.href = "jobs.html";
      el.title = "Signed in";
    } else {
      el.textContent = "Sign out";
      el.href = "#";
      el.addEventListener("click", (e) => {
        e.preventDefault();
        signOut();
      });
    }
  });
}

// Auto-mount on any page that imports this module.
if (document.readyState !== "loading") mountNavState();
else document.addEventListener("DOMContentLoaded", mountNavState);
