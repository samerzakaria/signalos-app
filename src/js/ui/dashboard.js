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

    const activeGate = gateList.find((g) => g.status === 'active' || g.is_current);
    if (activeGate) {
      state.currentGateInfo = activeGate;
      state.currentGateId = activeGate.id || activeGate.gate_id || null;
      state.gateActivities = Array.isArray(activeGate.activities) ? activeGate.activities : [];
      state.gateCriteria = Array.isArray(activeGate.criteria) ? activeGate.criteria : [];
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
