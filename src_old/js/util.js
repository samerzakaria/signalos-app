export function esc(s) {
  return String(s || "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

export function showError(msg) {
  console.error("[SignalOS]", msg);
  const existing = document.getElementById("errorToast");
  if (existing) existing.remove();
  const t = document.createElement("div");
  t.id = "errorToast";
  t.style.cssText =
    "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--danger);color:#fff;border-radius:var(--r);padding:11px 18px;font-size:13px;font-weight:600;z-index:9999;box-shadow:var(--sh-lg);animation:rise 0.3s var(--ease)";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => { if (t.parentElement) t.remove(); }, 4000);
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
