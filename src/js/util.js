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
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error.trim()) return error;
  if (typeof error === "number" || typeof error === "boolean") return String(error);
  if (error && typeof error === "object") {
    for (const key of ["message", "error", "detail", "reason"]) {
      const value = error[key];
      if (typeof value === "string" && value.trim()) return value;
    }
    try {
      const json = JSON.stringify(error);
      if (json && json !== "{}") return json;
    } catch {}
  }
  return fallback;
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
