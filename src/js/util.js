export function esc(s) {
  return String(s || "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

export function showError(msg) {
  console.error("[SignalOS]", msg);
  showToast(msg, "var(--danger)", "#fff");
}

export function showWarning(msg) {
  console.warn("[SignalOS]", msg);
  showToast(msg, "var(--amber-soft)", "var(--amber-deep)", "warningToast", 7000);
}

function showToast(msg, background, color, id = "errorToast", timeout = 4000) {
  const existing = document.getElementById("errorToast");
  if (existing) existing.remove();
  const existingWarning = document.getElementById("warningToast");
  if (existingWarning) existingWarning.remove();
  const t = document.createElement("div");
  t.id = id;
  t.style.cssText =
    `position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:${background};color:${color};border-radius:var(--r);padding:11px 18px;font-size:13px;font-weight:600;z-index:9999;box-shadow:var(--sh-lg);animation:rise 0.3s var(--ease);max-width:min(760px,calc(100vw - 48px));line-height:1.45;text-align:center`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => { if (t.parentElement) t.remove(); }, timeout);
}

export function errorMessage(error, fallback = "Unknown error") {
  if (error instanceof Error && error.message) return normalizeUserError(error.message, fallback);
  if (typeof error === "string" && error.trim()) return normalizeUserError(error, fallback);
  if (typeof error === "number" || typeof error === "boolean") return String(error);
  if (error && typeof error === "object") {
    for (const key of ["message", "error", "detail", "reason"]) {
      const value = error[key];
      if (typeof value === "string" && value.trim()) return normalizeUserError(value, fallback);
    }
    try {
      const json = JSON.stringify(error);
      if (json && json !== "{}") return normalizeUserError(json, fallback);
    } catch {}
  }
  return fallback;
}

function normalizeUserError(message, fallback) {
  const raw = String(message || "").trim();
  if (!raw || /^undefined$/i.test(raw) || /^null$/i.test(raw)) return fallback;
  if (/No workspace selected/i.test(raw)) {
    return "No product workspace is selected. Create or open a product before running this action.";
  }
  return raw;
}

export function providerConnectionMessage(error, provider = "provider") {
  const raw = errorMessage(error, "");
  const name = String(provider || "provider");
  if (/401|unauthori[sz]ed|invalid api key|invalid key|forbidden/i.test(raw)) {
    return `${name} rejected the API key. Setup can continue; replace the key in Settings when ready.`;
  }
  if (/content is blocked|site owner|cloudflare|blocked by/i.test(raw)) {
    return `${name} model fetching is blocked by the provider or network. Your key is saved; refresh models again or switch provider.`;
  }
  if (/requires an api key|api key.*not found|no api key/i.test(raw)) {
    return `${name} needs an API key before models can be fetched.`;
  }
  if (/model list|fetch.*models|returned no models|no models/i.test(raw)) {
    return `${name} models could not be loaded right now. You can continue setup and refresh models later in Settings.`;
  }
  return raw || `${name} is not connected yet. You can continue setup and configure it later in Settings.`;
}

export function isProviderAuthFailure(error) {
  return /401|unauthori[sz]ed|invalid api key|invalid key|forbidden/i.test(errorMessage(error, ""));
}

export function formatTs(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return "just now";
    if (diff < 3600000) return Math.round(diff / 60000) + "m ago";
    if (diff < 86400000) return Math.round(diff / 3600000) + "h ago";
    return d.toLocaleDateString();
  } catch {
    return ts;
  }
}
