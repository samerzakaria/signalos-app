import * as ipc from '../ipc.js';
import { state } from '../state.js';
import { loadEnforcement, updateCostDisplay } from '../app-v2.js';

export async function loadDashboard() {
  try {
    const [waveData, gatesData, costData] = await Promise.allSettled([
      ipc.wave.get(),
      ipc.gates.getAll(),
      ipc.provider.getCost(),
    ]);

    if (costData.status === "fulfilled") updateCostDisplay(costData.value);

    let gateList = [];
    if (gatesData.status === "fulfilled" && gatesData.value) {
      gateList = Array.isArray(gatesData.value) ? gatesData.value : (gatesData.value.gates || []);
      // The govGatesList signal already drives the Sidebar gov panel and
      // Dashboard stepper. Settings it here keeps both consistent.
      state.govGates = gateList;
    }

    if (waveData.status === "fulfilled" && waveData.value) {
      state.currentWaveSummary = {
        current_gate_name: waveData.value.current_gate_name || waveData.value.name || '',
        name: waveData.value.name || '',
        number: waveData.value.number,
        total_gates: waveData.value.total_gates ?? gateList.length,
      };
    }

    // get_gate_states() in signalos_ipc_server.py emits status='current' for
    // the active gate (and 'signed'/'locked' for the others). Accept both
    // 'current' and the legacy 'active'/is_current for forward compat.
    const activeGate = gateList.find(
      (g) => g.status === 'current' || g.status === 'active' || g.is_current,
    );
    if (activeGate) {
      state.currentGateInfo = activeGate;
      state.currentGateId = activeGate.id || activeGate.gate_id || null;
      // M3 emits criterion status as 'passing|failing|pending'. DashboardView
      // renders 'passed|failed|checking|waiting'. Translate at the boundary
      // so the view stays stable. Activities already use DashboardView's
      // vocabulary (completed|in_progress|pending|failed).
      const translateCriterion = (c) => {
        const s = c && c.status;
        const mapped =
          s === 'passing' ? 'passed' :
          s === 'failing' ? 'failed' :
          s === 'pending' ? 'waiting' :
          s;
        return { ...c, status: mapped };
      };
      state.gateActivities = Array.isArray(activeGate.activities) ? activeGate.activities : [];
      state.gateCriteria = Array.isArray(activeGate.criteria)
        ? activeGate.criteria.map(translateCriterion)
        : [];
    } else {
      state.currentGateInfo = null;
      state.currentGateId = null;
      state.gateActivities = [];
      state.gateCriteria = [];
    }

    await loadEnforcement();
  } catch (e) {
    console.warn("Dashboard load error:", e.message);
  }
}
