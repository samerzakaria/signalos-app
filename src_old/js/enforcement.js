/**
 * enforcement.js — Topbar enforcement pill + override modal (Wave 3 / G2-21..25)
 *
 * Renders the 🛡 pill, the rules popover, and the override modal that runs
 * before any blocked action proceeds. All overrides are audit-logged via
 * the Rust enforcement::override_rule command.
 *
 * Spec: docs/DEEP_REVIEW_AS_END_USER_2026-05-14.md §11.4d
 */

import * as ipc from "./ipc.js";

const RULE_LABELS = {
  "gate-gating":            "Gate gating on Build",
  "plan-gating":            "Plan gating on file writes",
  "trust-tier":             "Trust-tier confirmation",
  "audit-append":           "Atomic audit-append",
  "secret-block":           "Secret-file write block",
  "role-sign":              "Role enforcement on gate signing",
  "stack-contract":         "Stack contract enforcement",
  "wave-freeze":            "Wave freeze respected",
  "test-first":             "Test-first for beliefs",
  "gate-compliance":        "Gate compliance is binary",
  "zero-manual-regression": "Zero manual regression",
  "mutation-threshold":     "Mutation threshold",
};

let dom = {};
let pendingOverride = null;
let cachedState = null;
let onToast = (m) => console.log(m);

export function attachEnforcementUi({ toast }) {
  if (toast) onToast = toast;
  dom = {
    pill:           document.getElementById("enforcementPill"),
    popover:        document.getElementById("enforcementPopover"),
    close:          document.getElementById("enforcementClose"),
    rules:          document.getElementById("enforcementRules"),
    overridesCount: document.getElementById("enforcementOverridesCount"),
    freezeBtn:      document.getElementById("freezeWaveBtn"),
    modal:          document.getElementById("overrideModal"),
    modalClose:     document.getElementById("overrideClose"),
    ruleNameHdr:    document.getElementById("overrideRuleName"),
    ruleNameBody:   document.getElementById("overrideRuleNameBody"),
    reasonText:     document.getElementById("overrideReasonText"),
    reasonInput:    document.getElementById("overrideReason"),
    cancel:         document.getElementById("overrideCancel"),
    confirm:        document.getElementById("overrideConfirm"),
  };
  if (!dom.pill) return;
  dom.pill.addEventListener("click", togglePopover);
  dom.close?.addEventListener("click", () => { dom.popover.hidden = true; });
  dom.freezeBtn?.addEventListener("click", async () => {
    try {
      if (cachedState?.wave_frozen) {
        await ipc.enforcement.unfreeze();
        onToast("Wave unfrozen.");
      } else {
        await ipc.enforcement.freeze();
        onToast("Wave frozen.");
      }
      await refresh();
    } catch (e) { onToast(e?.message || "Could not toggle freeze."); }
  });
  dom.modalClose?.addEventListener("click", closeOverride);
  dom.cancel?.addEventListener("click", closeOverride);
  dom.confirm?.addEventListener("click", confirmOverride);
  refresh();
}

export async function refresh() {
  try {
    cachedState = await ipc.enforcement.state();
  } catch {
    cachedState = null;
  }
  renderPill();
  renderPopover();
}

/// Public: gate any action through this. If the action is allowed, the
/// callback runs. If blocked, the override modal opens; on confirm + reason
/// the callback runs after the audit entry lands.
export async function gateBuild(stack, onAllow) {
  try {
    const result = await ipc.enforcement.precheck(stack);
    if (result.allowed) {
      onAllow();
      return;
    }
    // Open the override modal
    pendingOverride = {
      rule: result.blocking_rule,
      reason: result.reason,
      onAllow,
      stack,
    };
    openOverride();
  } catch (e) {
    onToast(e?.message || "Enforcement check failed; not running.");
  }
}

function renderPill() {
  if (!dom.pill || !cachedState) return;
  const strictCount = cachedState.modes.filter((m) => m.mode === "strict").length;
  const overrides = cachedState.overrides_this_wave || 0;
  let cls = "green";
  let label = "🛡 Enforced";
  if (cachedState.wave_frozen) {
    cls = "amber";
    label = "🛡 Frozen";
  } else if (strictCount === 0) {
    cls = "red";
    label = "⚠ Advisory";
  } else if (overrides > 0 || strictCount < cachedState.modes.length) {
    cls = "amber";
    label = `🛡 ${overrides ? overrides + " override" + (overrides > 1 ? "s" : "") : "partial"}`;
  }
  dom.pill.className = `pill enforcement-pill ${cls}`;
  dom.pill.innerHTML = `<span class="pill-dot"></span><span>${label}</span>`;
}

function renderPopover() {
  if (!dom.rules || !cachedState) return;
  dom.rules.innerHTML = cachedState.modes.map((rs) => {
    const label = RULE_LABELS[rs.rule] || rs.rule;
    return `
      <div class="enforcement-rule">
        <div>${escapeHtml(label)}</div>
        <select data-rule="${escapeAttr(rs.rule)}" class="mode-select">
          <option value="strict" ${rs.mode === "strict" ? "selected" : ""}>strict</option>
          <option value="warn"   ${rs.mode === "warn" ? "selected" : ""}>warn</option>
          <option value="off"    ${rs.mode === "off" ? "selected" : ""}>off</option>
        </select>
      </div>
    `;
  }).join("");
  dom.overridesCount.textContent = `${cachedState.overrides_this_wave || 0} overrides this wave · ${cachedState.signed_gates?.length || 0}/${cachedState.required_gates?.length || 0} required gates signed`;
  if (dom.freezeBtn) dom.freezeBtn.textContent = cachedState.wave_frozen ? "Unfreeze wave" : "Freeze wave";
  dom.rules.querySelectorAll("select.mode-select").forEach((sel) => {
    sel.addEventListener("change", async () => {
      try {
        await ipc.enforcement.setMode(sel.dataset.rule, sel.value);
        await refresh();
      } catch (e) { onToast(e?.message || "Could not change mode."); }
    });
  });
}

function togglePopover() {
  if (!dom.popover) return;
  dom.popover.hidden = !dom.popover.hidden;
  if (!dom.popover.hidden) refresh();
}

function openOverride() {
  if (!pendingOverride) return;
  const label = RULE_LABELS[pendingOverride.rule] || pendingOverride.rule;
  dom.ruleNameHdr.textContent = label;
  dom.ruleNameBody.textContent = label;
  dom.reasonText.textContent = pendingOverride.reason || "";
  dom.reasonInput.value = "";
  dom.modal.hidden = false;
  setTimeout(() => dom.reasonInput?.focus(), 50);
}

function closeOverride() {
  dom.modal.hidden = true;
  pendingOverride = null;
}

async function confirmOverride() {
  if (!pendingOverride) return;
  const reason = dom.reasonInput.value.trim();
  if (!reason) {
    onToast("Override needs a reason.");
    dom.reasonInput.focus();
    return;
  }
  try {
    await ipc.enforcement.override(pendingOverride.rule, reason, pendingOverride.stack);
    const cb = pendingOverride.onAllow;
    closeOverride();
    cb?.();
    await refresh();
  } catch (e) {
    onToast(e?.message || "Override failed.");
  }
}

function escapeHtml(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}
function escapeAttr(v) { return escapeHtml(v); }
