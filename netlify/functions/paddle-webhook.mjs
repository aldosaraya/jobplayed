// Netlify Function: webhook de Paddle Billing
// Verifica la firma y sincroniza el estado de la suscripción en Supabase.
//
// Env vars requeridas:
//   PADDLE_WEBHOOK_SECRET     -> secret del destino de notificaciones en Paddle
//   SUPABASE_URL              -> https://juijmkcfbjthohjpgflr.supabase.co
//   SUPABASE_SERVICE_ROLE_KEY -> service_role key (bypassa RLS; NUNCA al frontend)
//
// Node 18+ (fetch global). Sin dependencias externas.

import crypto from "node:crypto";

const PADDLE_SECRET = process.env.PADDLE_WEBHOOK_SECRET;
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

const MAX_SKEW_SECONDS = 300; // rechaza webhooks de más de 5 min (replay)

/**
 * Paddle manda: Paddle-Signature: ts=1671552777;h1=<hex>
 * El payload firmado es `${ts}:${rawBody}` con HMAC-SHA256.
 */
function verifySignature(rawBody, signatureHeader, secret) {
  if (!signatureHeader || !secret) return false;

  const parts = {};
  for (const pair of signatureHeader.split(";")) {
    const idx = pair.indexOf("=");
    if (idx === -1) continue;
    parts[pair.slice(0, idx).trim()] = pair.slice(idx + 1).trim();
  }

  const ts = parts.ts;
  const h1 = parts.h1;
  if (!ts || !h1) return false;

  const age = Math.abs(Math.floor(Date.now() / 1000) - Number(ts));
  if (!Number.isFinite(age) || age > MAX_SKEW_SECONDS) return false;

  const expected = crypto
    .createHmac("sha256", secret)
    .update(`${ts}:${rawBody}`)
    .digest("hex");

  const a = Buffer.from(expected, "utf8");
  const b = Buffer.from(h1, "utf8");
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

/** PATCH a Supabase vía REST con service role (bypassa RLS). */
async function supabaseUpdate(table, filters, payload) {
  const qs = Object.entries(filters)
    .map(([k, v]) => `${encodeURIComponent(k)}=eq.${encodeURIComponent(v)}`)
    .join("&");

  const res = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${qs}`, {
    method: "PATCH",
    headers: {
      apikey: SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
      "Content-Type": "application/json",
      Prefer: "return=representation",
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Supabase ${res.status}: ${text}`);
  }
  return res.json();
}

/** Mapea el estado de Paddle al status interno. */
function mapStatus(paddleStatus) {
  switch (paddleStatus) {
    case "active":
    case "trialing":
      return "active";
    case "past_due":
      return "past_due";
    case "paused":
      return "paused";
    case "canceled":
      return "canceled";
    default:
      return "pending";
  }
}

function periodEnd(attrs) {
  return attrs?.current_billing_period?.ends_at ?? attrs?.billing_period?.ends_at ?? null;
}

export default async (request) => {
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const rawBody = await request.text();
  const signature = request.headers.get("paddle-signature");

  if (!verifySignature(rawBody, signature, PADDLE_SECRET)) {
    console.warn("paddle-webhook: firma inválida");
    return new Response("Invalid signature", { status: 401 });
  }

  let event;
  try {
    event = JSON.parse(rawBody);
  } catch {
    return new Response("Invalid JSON", { status: 400 });
  }

  const type = event?.event_type ?? "";
  const data = event?.data ?? {};
  const attrs = data?.attributes ?? {};

  // El user_id de Supabase viaja en custom_data (lo manda el checkout).
  const userId =
    attrs?.custom_data?.user_id ??
    data?.custom_data?.user_id ??
    null;

  const customerId = attrs?.customer_id ?? null;
  const subscriptionId = data?.id ?? null;

  console.log(`paddle-webhook: ${type} user=${userId} sub=${subscriptionId}`);

  try {
    // Identificamos la fila por user_id; si no viene, caemos a customer_id.
    const filters = userId
      ? { id: userId }
      : customerId
        ? { paddle_customer_id: customerId }
        : null;

    if (!filters) {
      console.warn("paddle-webhook: sin user_id ni customer_id, se ignora");
      return new Response("No identifier", { status: 200 });
    }

    switch (type) {
      case "subscription.created":
      case "subscription.activated":
      case "subscription.updated":
      case "subscription.trialing": {
        await supabaseUpdate("subscriptions", filters, {
          paddle_customer_id: customerId,
          paddle_subscription_id: subscriptionId,
          status: mapStatus(attrs.status),
          period_ends: periodEnd(attrs),
          updated_at: new Date().toISOString(),
        });
        break;
      }

      case "subscription.canceled": {
        await supabaseUpdate("subscriptions", filters, {
          paddle_subscription_id: subscriptionId,
          status: "canceled",
          period_ends: periodEnd(attrs),
          updated_at: new Date().toISOString(),
        });
        break;
      }

      case "subscription.past_due":
      case "transaction.payment_failed": {
        await supabaseUpdate("subscriptions", filters, {
          status: "past_due",
          updated_at: new Date().toISOString(),
        });
        break;
      }

      case "transaction.completed": {
        // Respaldo: si el pago entró, la sub está activa.
        await supabaseUpdate("subscriptions", filters, {
          status: "active",
          paddle_customer_id: customerId,
          updated_at: new Date().toISOString(),
        });
        break;
      }

      default:
        // Evento no manejado: respondemos 200 para que Paddle no reintente.
        break;
    }

    return new Response("OK", { status: 200 });
  } catch (err) {
    console.error("paddle-webhook error:", err);
    // 500 hace que Paddle reintente más tarde.
    return new Response("Handler error", { status: 500 });
  }
};
