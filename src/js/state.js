import * as signals from '../state';

export const state = new Proxy({}, {
  get(target, prop) {
    if (signals[prop]) return signals[prop].value;
    if (prop === 'secrets') return signals.secretsList.value;
    if (prop === 'brainEntries') return signals.brainList.value;
    if (prop === 'govGates') return signals.govGatesList.value;
    if (prop === 'auditTrail') return signals.auditList.value;
    if (prop === 'cost') return signals.currentCost.value;
    if (prop === 'workspace') return signals.workspacePath.value;
    return target[prop];
  },
  set(target, prop, value) {
    if (signals[prop]) {
      signals[prop].value = value;
      return true;
    }
    if (prop === 'secrets') { signals.secretsList.value = value; return true; }
    if (prop === 'brainEntries') { signals.brainList.value = value; return true; }
    if (prop === 'govGates') { signals.govGatesList.value = value; return true; }
    if (prop === 'auditTrail') { signals.auditList.value = value; return true; }
    if (prop === 'cost') { signals.currentCost.value = value; return true; }
    if (prop === 'workspace') { signals.workspacePath.value = value; return true; }
    target[prop] = value;
    return true;
  }
});

