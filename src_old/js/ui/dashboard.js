import * as ipc from '../ipc.js';
import { state } from '../state.js';
import { esc } from '../util.js';
import { loadEnforcement, updateCostDisplay } from '../app-v2.js';

export async function loadDashboard() {
  try {
    const [waveData, gatesData, costData, gitData, artifacts] = await Promise.allSettled([
      ipc.wave.get(),
      ipc.gates.getAll(),
      ipc.provider.getCost(),
      ipc.git.status(),
      ipc.project.artifacts(),
    ]);

    if (costData.status === "fulfilled") updateCostDisplay(costData.value);

    // Render gate stepper
    if (gatesData.status === "fulfilled" && gatesData.value) {
      renderGateStepper(gatesData.value);
      renderCurrentGate(gatesData.value);
    }

    // Update hero ring with wave progress
    if (waveData.status === "fulfilled" && waveData.value) {
      renderWaveHero(waveData.value, gatesData.value);
    }

    // Enforcement state
    await loadEnforcement();
  } catch (e) {
    console.warn("Dashboard load error:", e.message);
  }
}

function renderWaveHero(wave, gates) {
  const gateList = Array.isArray(gates) ? gates : (gates?.gates || []);
  const total = gateList.length || 7;
  const done = gateList.filter((g) => g.status === "signed" || g.signed).length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  const ringPct = document.getElementById("ringPct");
  if (ringPct) ringPct.textContent = pct + "%";
  const ring = document.getElementById("ring");
  if (ring) {
    const circumference = 276.46;
    ring.style.strokeDashoffset = circumference * (1 - pct / 100);
  }

  const activeName = wave?.current_gate_name || wave?.name || "";
  const h2 = document.querySelector(".hero-tx h2");
  if (h2 && activeName) h2.textContent = activeName;

  const heroSub = document.getElementById("heroSub");
  if (heroSub) {
    heroSub.textContent = done + " of " + total + " gates signed.";
  }
}

function renderGateStepper(gates) {
  const cells = document.querySelectorAll(".scell");
  const gateList = Array.isArray(gates) ? gates : (gates?.gates || []);
  gateList.forEach((gate, i) => {
    const cell = cells[i];
    if (!cell) return;
    cell.classList.remove("done", "active");
    const scirc = cell.querySelector(".scirc");
    const sstatus = cell.querySelector(".sstatus");
    const slbl = cell.querySelector(".slbl");
    if (slbl && gate.name) slbl.textContent = gate.name;
    if (gate.status === "signed" || gate.signed) {
      cell.classList.add("done");
      if (scirc) scirc.innerHTML = '<i class="ti ti-check"></i>';
      if (sstatus) sstatus.textContent = "Signed";
    } else if (gate.status === "active" || gate.is_current) {
      cell.classList.add("active");
      if (scirc) scirc.textContent = String(i + 1);
      if (sstatus) sstatus.textContent = "Current";
      state.currentGateId = gate.id || gate.gate_id || null;
    } else {
      if (scirc) scirc.innerHTML = '<i class="ti ti-lock"></i>';
      if (sstatus) sstatus.textContent = "Locked";
    }
  });
}

function renderCurrentGate(gates) {
  const gateList = Array.isArray(gates) ? gates : (gates?.gates || []);
  const activeGate = gateList.find((g) => g.status === "active" || g.is_current);
  if (!activeGate) return;

  state.currentGateId = activeGate.id || activeGate.gate_id || null;

  const gateHead = document.querySelector("#gateCard .gate-tx h3");
  if (gateHead) gateHead.textContent = activeGate.name || "Current Gate";

  // Update gate badge
  const gateBadge = document.getElementById("gateBadge");
  if (gateBadge) {
    if (activeGate.signed) {
      gateBadge.className = "gate-badge passed";
      gateBadge.innerHTML = '<i class="ti ti-check"></i> Signed';
    } else {
      gateBadge.className = "gate-badge";
      gateBadge.innerHTML = '<span class="dot"></span> Current gate';
    }
  }

  // Render activities from gate data
  if (activeGate.activities) {
    renderGateActivities(activeGate.activities);
  }

  // Render criteria
  if (activeGate.criteria) {
    renderGateCriteria(activeGate.criteria);
  }

  updateGateVerdictDisplay();
}

function renderGateActivities(activities) {
  const container = document.getElementById("acts");
  if (!container || !activities?.length) return;
  container.innerHTML = activities
    .map((act) => {
      const status = act.status || "pending";
      const cls = status === "completed" ? "done" : status === "in_progress" ? "ongoing" : "pending";
      let icHTML = "", pillHTML = "";
      if (cls === "done") {
        icHTML = '<i class="ti ti-check"></i>';
        pillHTML = "Done";
      } else if (cls === "ongoing") {
        icHTML = '<i class="ti ti-loader-2"></i>';
        pillHTML = '<span class="pdot"></span>Ongoing';
      } else {
        icHTML = "";
        pillHTML = "Pending";
      }
      return `<div class="act ${cls}" onclick="cycleActivity(this)">
        <div class="act-ic">${icHTML}</div>
        <div class="act-name">${esc(act.name || act.description || "")}</div>
        <div class="act-pill">${pillHTML}</div>
      </div>`;
    })
    .join("");
  updateGateVerdictDisplay();
}

function renderGateCriteria(criteria) {
  const container = document.getElementById("crits");
  if (!container || !criteria?.length) return;
  container.innerHTML = criteria
    .map((c) => {
      const status = c.status || "waiting";
      const cls =
        status === "passed"
          ? "passed"
          : status === "failed"
          ? "failed"
          : status === "checking"
          ? "checking"
          : "waiting";
      let icHTML = "", pillHTML = "";
      if (cls === "passed") {
        icHTML = '<i class="ti ti-shield-check"></i>';
        pillHTML = "Passed";
      } else if (cls === "failed") {
        icHTML = '<i class="ti ti-shield-x"></i>';
        pillHTML = "Needs a fix";
      } else if (cls === "checking") {
        icHTML = '<i class="ti ti-loader-2"></i>';
        pillHTML = '<span class="pdot"></span>Checking';
      } else {
        icHTML = '<i class="ti ti-shield"></i>';
        pillHTML = "Waiting";
      }
      return `<div class="crit ${cls}" onclick="runCheck(this)">
        <div class="crit-ic">${icHTML}</div>
        <div class="crit-name">${esc(c.name || c.description || "")}</div>
        <div class="crit-pill">${pillHTML}</div>
      </div>`;
    })
    .join("");
  updateGateVerdictDisplay();
}

function updateGateVerdictDisplay() {
  const acts = [...document.querySelectorAll(".act")];
  const aDone = acts.filter((a) => a.classList.contains("done")).length;
  const aOngoing = acts.filter((a) => a.classList.contains("ongoing")).length;
  const aPending = acts.filter((a) => a.classList.contains("pending")).length;

  const el = document.getElementById("cDone");
  if (el) el.textContent = aDone;
  const elO = document.getElementById("cOngoing");
  if (elO) elO.textContent = aOngoing;
  const elP = document.getElementById("cPending");
  if (elP) elP.textContent = aPending;

  const crits = [...document.querySelectorAll(".crit")];
  const cPassed = crits.filter((c) => c.classList.contains("passed")).length;
  const cCrit = document.getElementById("cCrit");
  if (cCrit) cCrit.textContent = cPassed;

  const heroSub = document.getElementById("heroSub");
  if (heroSub && acts.length > 0) {
    heroSub.textContent =
      aDone + " of " + acts.length + " activities done · " + cPassed + " of " + crits.length + " checks passed.";
  }

  const ready = aDone === acts.length && cPassed === crits.length && acts.length > 0;
  const verdict = document.getElementById("verdict");
  const openBtn = document.getElementById("openBtn");

  if (verdict && !state.gateOpen) {
    verdict.classList.toggle("ready", ready);
    verdict.classList.toggle("held", !ready);
    const vic = verdict.querySelector(".verdict-ic");
    const vtx = document.getElementById("verdictTx");
    if (ready) {
      if (vic) vic.innerHTML = '<i class="ti ti-circle-check"></i>';
      if (vtx) vtx.textContent = "All clear — sign the gate to advance.";
      if (openBtn) openBtn.disabled = false;
    } else {
      if (vic) vic.innerHTML = '<i class="ti ti-lock"></i>';
      const aLeft = acts.length - aDone, cLeft = crits.length - cPassed;
      const parts = [];
      if (aLeft) parts.push(aLeft + " activit" + (aLeft > 1 ? "ies" : "y") + " to finish");
      if (cLeft) parts.push(cLeft + " check" + (cLeft > 1 ? "s" : "") + " to pass");
      if (vtx) vtx.textContent = "Gate held" + (parts.length ? " — " + parts.join(" and ") + "." : ".");
      if (openBtn) openBtn.disabled = true;
    }
  }
}
