export function esc(s) {
  return String(s || "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

export function showError(msg) {
  console.error("[Foundry]", msg);
  showToast(msg, "var(--danger)", "#fff");
}

export function showWarning(msg) {
  console.warn("[Foundry]", msg);
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

function rawErrorMessage(error, fallback = "") {
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

function normalizeUserError(message, fallback) {
  const raw = String(message || "").trim();
  if (!raw || /^undefined$/i.test(raw) || /^null$/i.test(raw)) return fallback;
  if (/No workspace selected/i.test(raw)) {
    return "Open or create a product first. The projects root is only a container; Vault actions need an active product folder.";
  }
  const provider = readableProviderError(raw);
  if (provider) return provider;
  if (/Unexpected token .* is not valid JSON/i.test(raw)) {
    return "Foundry received a text response where structured data was expected. Refresh this panel or run the command again.";
  }
  return raw.replace(/^(Error|RuntimeError):\s*/i, "");
}

export function providerConnectionMessage(error, provider = "provider") {
  const name = String(provider || "provider");
  const raw = rawErrorMessage(error, "");
  if (/401|unauthori[sz]ed|invalid api key|invalid key|forbidden/i.test(raw)
      && /model list|fetch.*models|models/i.test(raw)) {
    return `${name} rejected the API key. Setup can continue; replace the key in Settings when ready.`;
  }
  const readable = readableProviderError(raw, name);
  if (readable) return readable;
  const normalized = errorMessage(error, "");
  if (/401|unauthori[sz]ed|invalid api key|invalid key|forbidden/i.test(raw)) {
    return `${name} rejected the API key. Setup can continue; replace the key in Settings when ready.`;
  }
  if (/chat failed:\s*HTTP 404|model.*not found|does not exist|not supported/i.test(raw)) {
    return `${name} rejected the selected model for chat. Pick a text/chat model in Settings, test it, then retry.`;
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
  return normalized || raw || `${name} is not connected yet. You can continue setup and configure it later in Settings.`;
}

export function isProviderAuthFailure(error) {
  return /401|unauthori[sz]ed|invalid api key|invalid key|forbidden/i.test(rawErrorMessage(error, ""));
}

function readableProviderError(raw, providerName = "") {
  if (!raw) return "";
  const extracted = extractProviderMessage(raw);
  const text = extracted || raw;
  const name =
    providerName ||
    (raw.match(/\b(Anthropic|OpenAI|Gemini|Ollama|OpenRouter|DeepSeek|Mistral|Groq|Cerebras|Together AI|Together|xAI|Qwen)\b/i)?.[1] || "AI provider");
  if (/credit balance is too low|insufficient.*credit|purchase credits/i.test(text)) {
    return `${name} account credit is too low. Add credits with that provider or choose another provider/model in Settings.`;
  }
  if (/LLM Provider NOT provided|provider.*not provided|unmapped llm provider/i.test(text)) {
    return `Foundry could not route the selected model to ${name}. Re-select the provider and model in Settings, then retry.`;
  }
  if (/chat failed:\s*HTTP 404|HTTP 404|model.*not found|does not exist|not supported/i.test(text)) {
    return `${name} rejected the selected model for chat. Pick a text/chat model in Settings, test it, then retry.`;
  }
  if (/api key|api_key|unauthori[sz]ed|authentication|401|forbidden/i.test(text)) {
    return `${name} rejected the API key. Replace the key in Settings, then retry.`;
  }
  if (/rate limit|quota/i.test(text)) {
    return `${name} is rate-limiting this request. Wait a bit or choose another provider/model.`;
  }
  return "";
}

function extractProviderMessage(raw) {
  const match = raw.match(/"message"\s*:\s*"([^"]+)"/);
  if (match?.[1]) return match[1];
  return raw
    .replace(/(?:Provider call failed:\s*)+/gi, "")
    .replace(/^(BadRequestError|AuthenticationError|RateLimitError|RuntimeError):\s*/i, "")
    .trim();
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
