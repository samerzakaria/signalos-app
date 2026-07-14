import * as ipc from '../ipc.js';
import { state } from '../state.js';
import { providerConnectionMessage, showError } from '../util.js';
import { activeBuildId, appendTurn, loadHistory as loadConvHistory } from '../conversation.js';
import { loadEnforcement, updateCostDisplay } from '../app-v2.js';
import { wrapWithSignalosContext, extractPlanWithErrors } from '../../services/signalosPrompt.ts';
import { scanChatResponse, summariseRedactions } from '../../services/chatResponseGuard.ts';
import { tryBegin as waveEngineTryBegin, translateExternal as waveEngineTranslateExternal } from '../../services/waveEngineClient.ts';
import { isGovernedCommand, splitGovernedCommand } from '../../services/governedShell.ts';
import {
  isGate0AwaitingApproval,
  reconcileGate0ApprovalAffordance,
  refreshGovernanceGates,
} from '../../services/workspace.ts';
import { activeProjectId } from '../../services/projectPicker.ts';

function nowId() {
  return (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random();
}

// Phase 3 (3.7) — every plain natural-language message is routed to the
// governed agent loop. There is NO feature flag: the agent loop IS the chat
// path. Streaming text, tool rows, gate pauses, and completion arrive over
// the "agent:event" Tauri channel and are rendered by agentEvents.ts.
//
// Phase 5 (T39) — a "build X" / delivery request starts the governed G0->G5
// walk (`agent:deliver` -> GateOrchestrator). Everything else is a normal
// `agent:run` turn. The classifier stays broad on product-change verbs and
// product artifacts, while pure questions remain conversational.
const _DELIVERY_ACTION = /\b(build|create|make|develop|scaffold|generate|ship|implement|add|change|update|modify|edit|fix|repair|redesign|polish|improve|write)\b/i;
// The artifact vocabulary is deliberately broad: a non-technical founder says
// "app" but an engineer says "module / endpoint / migration". Both are build
// requests. Omitting the software-noun vocabulary routed "Create an
// authentication module" / "Add login" to the write-forbidden conversation
// mode (agent:run), so every file write was refused. (Claim 8.)
const _DELIVERY_ARTIFACT = /\b(app|application|system|tool|product|site|website|web\s*page|html|css|javascript|typescript|component|page|screen|view|form|dashboard|api|service|platform|feature|prototype|mvp|game|tracker|manager|portal|store|bot|workflow|ui|ux|file|code|module|library|package|auth|authentication|login|signup|logout|endpoint|route|router|hook|model|schema|migration|cli|script|integration|middleware|worker|job|queue|database|function|class|widget|plugin)\b/i;
const _PRODUCT_OUTCOME = /\b(i\s+want|i\s+need|we\s+need|let'?s|please)\b[\s\S]*\b(app|application|system|tool|site|website|dashboard|game|tracker|manager|portal|store|bot|workflow|ui|page|module|library|package|auth|authentication|login|endpoint|route|api|feature|integration)\b/i;
const _PURE_QUESTION = /^(what|why|how|when|where|who|which|explain|tell me|describe|show me|can you tell)\b/i;
// Imperative edit/implementation follow-ups whose object is the CURRENT product
// or a pronoun ("fix it", "refactor this", "undo that", "make it dark mode",
// "remove the login page"). Statelessly these look conversational -- no artifact
// noun to pair with an action -- so they routed to the write-forbidden
// conversation mode (agent:run, execution_context="conversation") and the
// user's change was silently dropped. Against an EXISTING product they ARE build
// work and must reach the write-capable path. Genuine questions are excluded by
// _PURE_QUESTION before this ever runs.
const _IMPERATIVE_EDIT = /\b(fix|change|remove|delete|drop|refactor|rename|replace|undo|revert|redo|add|update|modify|edit|redesign|restyle|rework|tweak|adjust|improve|polish|simplify|make\s+it|turn\s+it\s+into|convert\s+it)\b/i;
// A trailing "?" alone must NOT force conversational: "Can you build me an
// app?" is a build request phrased politely. Only a genuine leading-
// interrogative question ("what is X?", "how does Y work?") stays
// conversational, via _PURE_QUESTION. (Claim 8.)
//
// `context.hasProduct` (a product/workspace is already open) turns an imperative
// edit into a build request -- the context-aware routing the backend also
// enforces as a safety net. Callers without context stay backward-compatible
// (a bare imperative with no product is left conversational, since there is
// nothing to change yet).
export function isDeliveryIntent(text, context = {}) {
  const t = (text || '').trim();
  if (!t || t.startsWith('/')) return false;
  if (_PURE_QUESTION.test(t)) return false;
  if ((_DELIVERY_ACTION.test(t) && _DELIVERY_ARTIFACT.test(t)) || _PRODUCT_OUTCOME.test(t)) {
    return true;
  }
  // Context-aware: an imperative edit/implementation command against an existing
  // product is build work, even without an explicit artifact noun.
  if (context && context.hasProduct && _IMPERATIVE_EDIT.test(t)) return true;
  return false;
}

// G0 approval is also a dual-seat authority declaration, so generic positive
// sentiment ("looks good", "I agree") is not consent. Accept one anchored,
// auditable sentence only. Anchoring fails closed for negation, questions,
// modal uncertainty, and unrelated product language.
const _GATE0_APPROVAL_CONSENT = 'I approve Gate 0 as sole founder';
export function isApprovalIntent(text) {
  return typeof text === 'string' && text === _GATE0_APPROVAL_CONSENT;
}

// C1: is Gate 0 (the setup / governance-agreement gate) still awaiting the
// founder's approval? Reads the in-memory gate list via the state proxy
// (state.govGates -> govGatesList). A gate is signed when its status is
// 'signed' or its signed flag is true.
function gate0AwaitingApproval() {
  try {
    return isGate0AwaitingApproval(state.govGates || []);
  } catch {
    return false;
  }
}

async function buildEntrypointAllowed() {
  const result = await ipc.enforcement.precheck('auto', { rules: ['wave-freeze'] });
  if (result && result.allowed === false) {
    const rule = result.blocking_rule || 'build precheck';
    const reason = result.reason || `Build is blocked by ${rule}.`;
    return { allowed: false, message: reason };
  }
  return { allowed: true, message: '' };
}

// A build needs a product *project* (workspace) to build into. Onboarding only
// sets the projects root; the active workspace stays (none) until a project is
// created or opened — so a first delivery (or a reinstall that skipped
// onboarding via persisted state) otherwise dies in the Rust precheck with a
// cryptic "No workspace selected". Instead, guide the user to make one.
function guideToCreateProject() {
  state.chatBubbles = [...state.chatBubbles, {
    id: nowId(),
    kind: 'system',
    text: 'No project open yet — Foundry builds inside a product folder. Create or open a project to start building. Opening New Project…',
  }];
  if (typeof window.openNewProject === 'function') window.openNewProject();
}

function parseGovernedChatCommand(value) {
  const normalized = String(value || '').trim().replace(/\s+/g, ' ');
  const lower = normalized.toLowerCase();
  if (lower === 'signalos status' || lower === '/signal-status') {
    return { command: 'signal-status', args: [], timeoutMs: 60000 };
  }
  if (lower === 'signalos check' || lower === '/signal-release-readiness') {
    return { command: 'signal-release-readiness', args: [], timeoutMs: 120000 };
  }
  if (lower === 'signalos gates' || lower === '/state:gates') {
    return { command: 'state:gates', args: [], timeoutMs: 60000 };
  }
  if (normalized.startsWith('/')) {
    const tokens = splitGovernedCommand(normalized.replace(/^\//, ''));
    return { command: tokens[0] || '', args: tokens.slice(1), timeoutMs: 60000 };
  }
  if (lower.startsWith('signalos ')) {
    const tokens = splitGovernedCommand(normalized).slice(1);
    return { command: tokens[0] || '', args: tokens.slice(1), timeoutMs: 60000 };
  }
  return { command: '', args: [], timeoutMs: 60000 };
}

// Milestone 2-a: keep the originating user prompt for each in-flight LLM
// stream so the audit-trail entry (written when the response guard fires)
// can include a short trace of what the model was answering. Cleared in
// finaliseStream / showStreamError so the map doesn't grow unbounded.
const streamPrompts = new Map();

function activeAgentPayload(extra = {}) {
  const provider = String(state.ai || '').trim();
  const model = String(state.aiModel || '').trim();
  if (!provider || !model) {
    throw new Error('Choose an AI provider and model in Settings before sending a chat message.');
  }
  return { ...extra, provider, model };
}

export async function loadBuild() {
  try {
    // #50: loadBuild runs on EVERY switch to the Build tab (switchTab's
    // loaders). It must NOT clobber a live or already-loaded conversation --
    // unconditionally overwriting state.chatBubbles here wiped a mid-flight
    // chat (streaming/tool/gate bubbles that aren't persisted turns yet) back
    // to just the welcome message every time the user navigated away and
    // back ("new chat without history"). Only HYDRATE when the chat is empty
    // (first visit, or after a project switch has cleared it); otherwise keep
    // whatever is already on screen.
    const current = state.chatBubbles || [];
    if (current.length === 0) {
      const buildId = await activeBuildId();
      const turns = await loadConvHistory(buildId);
      const bubbles = [
        {
          id: 'welcome',
          kind: 'ai',
          text: `Hi ${state.userName || 'there'}! What do you want to build today?`,
          historical: true,
        },
      ];
      if (turns && turns.length > 0) {
        turns.forEach((t) => {
          if (t.user_idea || t.user) bubbles.push({ id: nowId(), kind: 'user', text: t.user_idea || t.user, historical: true });
          if (t.ai_summary || t.summary) bubbles.push({ id: nowId(), kind: 'ai', text: t.ai_summary || t.summary, historical: true });
        });
      }
      state.chatBubbles = bubbles;
    }
  } catch (e) {
    console.warn('Could not load conversation history:', e.message);
  }

  // Backend gate truth is the durable source for this affordance. Re-fetch on
  // every Build load so an app reload/project reopen reconstructs the card and
  // a backend-completed approval resolves it without relying on chat history.
  if (String(state.workspace || '').trim()) {
    try {
      await refreshGovernanceGates();
    } catch (e) {
      console.warn('Could not refresh Gate 0 approval state:', e && e.message ? e.message : e);
      reconcileGate0ApprovalAffordance(state.govGates || []);
    }
  }

  await loadEnforcement().catch(() => {});
}

// Auto-switch sidebar tab based on user intent keywords.
function autoSwitchSidebarForIntent(text) {
  const t = String(text || '').toLowerCase();
  if (/\b(build|make|create|add|fix|edit|generate)\b/.test(t)) { window.switchSbTab?.('files'); return; }
  if (/\b(sign|gate|freeze|unfreeze|pause|observe|wave|status|onboard)\b/.test(t)) { window.switchSbTab?.('gov'); return; }
  if (/\b(remember|note|brain|audit|history|debrief|review|retrospective)\b/.test(t)) { window.switchSbTab?.('projects'); return; }
}

async function sendMsg() {
  // Keep the original bytes for authority-bearing consent. Normal chat is
  // still trimmed for usability, but whitespace/casing/punctuation variants
  // must not be normalized into the exact sole-founder declaration.
  const rawVal = String(state.chatInputValue || '');
  const val = rawVal.trim();
  if (!val || state.busy) return;
  state.busy = true;

  state.cmdPaletteOpen = false;

  addUserBubble(val);
  state.chatInputValue = '';

  // Auto-switch sidebar to the relevant tab based on message keywords.
  autoSwitchSidebarForIntent(val);

  // Governed commands route to the Python sidecar, not to the AI provider.
  if (isGovernedCommand(val)) {
    try {
      const { command, args, timeoutMs } = parseGovernedChatCommand(val);
      if (!command) throw new Error(`Unsupported command: ${val}`);
      const output = await ipc.signal.runAndWait(command, args, timeoutMs);
      addAIBubble(typeof output === 'string' ? output : JSON.stringify(output ?? '(no output)'));

      // Milestone 2-b / AMD-CORE-107 — freeze-state consolidation.
      // The Python CLI writes the durable audit-trail freeze record but does
      // NOT touch the Rust mutex that the Toolbar's "Frozen" indicator reads
      // from. To keep both stores in sync, after a successful signal-freeze
      // (or unfreeze) CLI call we additionally flip the Rust mutex via the
      // existing enforcement IPC. The Rust path also appends its own audit
      // entry (see src-tauri/src/enforcement.rs freeze_wave/unfreeze_wave),
      // so callers always end with: Python freeze record + Rust mutex bit
      // + Rust audit entry, all in agreement. A Rust IPC failure here is
      // logged but doesn't break the user's chat flow — the audit record
      // is already written and is the durable source.
      if (command === 'signal-freeze') {
        try {
          await ipc.enforcement.freeze();
        } catch (rustErr) {
          console.warn('[freeze-consolidation] Rust enforcement.freeze failed after CLI success:', rustErr && rustErr.message ? rustErr.message : rustErr);
        }
      } else if (command === 'signal-unfreeze') {
        try {
          await ipc.enforcement.unfreeze();
        } catch (rustErr) {
          console.warn('[freeze-consolidation] Rust enforcement.unfreeze failed after CLI success:', rustErr && rustErr.message ? rustErr.message : rustErr);
        }
      }
    } catch (e) {
      state.chatBubbles = [...state.chatBubbles, { id: nowId(), kind: 'error', text: 'Command failed: ' + (e.message || e) }];
      showError(e.message || 'Command failed');
    } finally {
      state.busy = false;
    }
    return;
  }

  // Only the exact sole-founder consent grammar can enter this path. Generic
  // approval language remains a normal chat turn and cannot grant PO+PE power.
  if (gate0AwaitingApproval() && isApprovalIntent(rawVal)) {
    try {
      const res = window.approveGate0
        ? await window.approveGate0({
            via: 'chat',
            // Pass the user's exact accepted bytes; never replace a looser
            // phrase with the canonical authority declaration in the audit.
            consent: rawVal,
            expectedWorkspace: String(state.workspace || ''),
            expectedProjectId: String(activeProjectId.value || ''),
          })
        : { signed: false, reason: 'Gate 0 approval service is unavailable.' };
      state.chatBubbles = [...state.chatBubbles, {
        id: nowId(),
        kind: 'system',
        text: res && res.signed
          ? 'Gate 0 approved — your governance agreement is signed. You can start building now.'
          : `Could not record the Gate 0 approval. ${res && res.reason ? res.reason : 'Check governance status and try again.'}`,
        gate: 'G0',
        waveAction: res && res.signed ? 'gate-approved' : 'gate-approval-failed',
      }];
    } catch (e) {
      state.chatBubbles = [...state.chatBubbles, {
        id: nowId(),
        kind: 'error',
        text: 'Gate 0 approval failed: ' + (e && e.message ? e.message : e),
      }];
    } finally {
      state.busy = false;
    }
    return;
  }

  // Phase 3 Stream C — agent-loop path. When the v4 agent loop is enabled,
  // route plain natural-language messages to the governed tool-using agent
  // loop in the Python sidecar via the `agent:run` command. The command is
  // fire-and-forget from the UI's perspective: streamed text, tool rows, and
  // turn completion all arrive asynchronously over the "agent:event" Tauri
  // channel and are rendered by src/services/agentEvents.ts. We keep `busy`
  // set so the working indicator shows until an end_turn/error/cancelled
  // event clears it (agentEvents flips busy back to false).
  //
  // No flag: natural-language always drives the agent loop. (The legacy
  // wave-engine + LLM-stream code remains below but is unreachable for NL.)
  {
    let buildPrecheckComplete = false;
    // An open workspace means there is already a product to change, so an
    // imperative edit ("fix it", "remove the login page", "make it dark mode")
    // is build work -- route it to the write-capable path, not conversational
    // agent:run where writes are refused. (The backend re-checks this too.)
    const hasProduct = !!String(state.workspace || '').trim();
    try {
      // A build/delivery request ("build a task manager", "create an app
      // that...") starts the governed G0->G5 delivery via `agent:deliver`
      // (GateOrchestrator). Any other message is a normal `agent:run` turn.
      // Both stream via the "agent:event" channel; gate pauses, verdicts,
      // and cancel/resume are driven by agentEvents.ts.
      const isDelivery = isDeliveryIntent(val, { hasProduct });
      if (isDelivery) {
        // Guard before the precheck: a build with no active project would fail
        // the Rust precheck with "No workspace selected". Route to New Project.
        if (!String(state.workspace || '').trim()) {
          guideToCreateProject();
          state.busy = false;
          return;
        }
        const precheck = await buildEntrypointAllowed();
        if (!precheck.allowed) {
          state.chatBubbles = [...state.chatBubbles, {
            id: nowId(),
            kind: 'error',
            text: precheck.message,
          }];
          showError(precheck.message);
          state.busy = false;
          return;
        }
        buildPrecheckComplete = true;
      }
      const command = isDelivery ? 'agent:deliver' : 'agent:run';
      // A governed delivery walks G0->G5 and each gate agent builds/tests on
      // disk, so it can legitimately run far longer than a chat turn. The old
      // hard 10-min cap abandoned the awaited call (surfacing a spurious
      // "Agent run failed") while the backend kept working. Pass 0 to disable
      // the transport timeout for delivery (ipc.js: timeoutMs<=0 => no timer);
      // the terminal agent events (end_turn / delivery_complete / error /
      // cancelled) clear `busy` and settle the run. A normal turn keeps its
      // bound so a dead backend on a plain chat message still fails fast.
      const commandTimeoutMs = isDelivery ? 0 : 600000;
      await ipc.signal.runAndWait(command, [JSON.stringify(activeAgentPayload({ prompt: val }))], commandTimeoutMs);
    } catch (e) {
      const raw = e && e.message ? e.message : String(e);
      // Safety net: if the sidecar/Rust side reports no workspace despite the
      // pre-guard (state can diverge), guide instead of surfacing raw internals.
      if (/no workspace selected/i.test(raw)) {
        guideToCreateProject();
        state.busy = false;
        return;
      }
      const message = isDeliveryIntent(val, { hasProduct }) && !buildPrecheckComplete
        ? `Build precheck failed: ${raw}`
        : providerConnectionMessage(e, state.ai || 'AI provider');
      state.chatBubbles = [...state.chatBubbles, {
        id: nowId(),
        kind: 'error',
        text: 'Agent run failed: ' + message,
      }];
      showError(message);
    } finally {
      // agentEvents normally clears busy on the terminal event; clear here too
      // so the composer never locks if the run ends without one.
      state.busy = false;
    }
    return;
  }

  // WAVE-ENGINE-DESIGN §5 — pre-LLM engine pre-check. Surfaces a
  // transparent system bubble naming the current gate (or a scope-drift
  // / complete bubble) before the LLM stream starts. tryBegin swallows
  // sidecar failures; the LLM path runs unchanged when the engine is
  // unavailable, so this hook is non-destructive.
  let scopeDriftFired = false;
  let waveAgentContent = '';
  let waveAgentGate = '';
  try {
    const wave = await waveEngineTryBegin(val);
    if (wave && wave.system_bubble && wave.system_bubble.text) {
      const bubble = {
        id: nowId(),
        kind: 'system',
        text: wave.system_bubble.text,
        gate: wave.current_gate || null,
        waveAction: wave.action,
      };
      // For scope-drift prompts, attach the user's original request so
      // the ChatBubbleSystem buttons can fire wave:scope-drift-resolve.
      if (wave.action === 'scope-drift-prompt') {
        bubble.waveUserRequest = val;
        // GATE-REOPEN-DESIGN #5: thread the drift verdict extras through so
        // the prompt can grow option (e) "Reopen <gate>" when the request
        // conflicts with a signed later gate (G2/G3).
        if (wave.drift) {
          bubble.waveDrift = {
            recommended_action: wave.drift.recommended_action,
            conflicting_gate: wave.drift.conflicting_gate || null,
            conflicting_summary: wave.drift.conflicting_summary,
          };
        }
        scopeDriftFired = true;
      }
      state.chatBubbles = [...state.chatBubbles, bubble];
    }
    // WAVE-ENGINE-DESIGN §4 — when the engine fires a gate agent,
    // capture its .md content so wrapWithSignalosContext can inject it
    // as the LLM's system prompt for this turn. Honours the gate's
    // prerequisites / outputs / refusal conditions per the agent contract.
    if (wave && wave.agent && wave.agent.exists && wave.agent.content) {
      waveAgentContent = wave.agent.content;
      waveAgentGate = wave.current_gate || '';
    }
    // When the engine returns scope-drift, the user needs to pick an
    // option before the LLM stream is meaningful — skip the stream and
    // let the prompt buttons drive the next turn.
    if (scopeDriftFired) {
      state.busy = false;
      return;
    }
  } catch (waveErr) {
    // Non-fatal — log and proceed to the LLM stream.
    console.warn('[wave-engine] pre-LLM check failed:', waveErr && waveErr.message ? waveErr.message : waveErr);
  }

  const streamId = nowId();
  startStream(streamId);
  // M2-a: record the originating user prompt so the chat-response guard
  // can include a trace string in any audit entries it writes when the
  // model's reply triggers a redaction.
  streamPrompts.set(streamId, val);

  // AMD-CORE-102: every non-slash message is wrapped with the SignalOS
  // protocol context (SOUL/CONSTITUTION/DECISION-DNA + plan schema). The
  // wrapped prompt tells the LLM it may either respond conversationally
  // or emit a `signalos-plan` block — the LLM decides, no regex gate.
  // The user always sees their original message in the user bubble.
  //
  // WAVE-ENGINE-DESIGN §4: when the engine has dispatched a gate-agent,
  // pass its .md content so the wrap injects an "## Active gate agent"
  // preamble block — the LLM honours the agent's contract for this turn.
  const wrapOptions = waveAgentContent
    ? { agentSystemContext: waveAgentContent, gate: waveAgentGate }
    : {};
  const wrapped = wrapWithSignalosContext(val, wrapOptions);
  if (!state.aiModel) {
    state.busy = false;
    showError('Select a model before sending a provider chat message.');
    return;
  }

  try {
    await ipc.provider.chatStream(streamId, state.ai, state.aiModel, wrapped);
    const cost = await ipc.provider.getCost();
    updateCostDisplay(cost);
    const buildId = await activeBuildId().catch(() => null);
    if (buildId) {
      await appendTurn(buildId, { user_idea: val, ai_summary: '(streaming)' }).catch(() => {});
    }
    // ALWAYS try to extract a plan from the finalised bubble — the LLM
    // decided whether to emit one, and the parser is cheap. Three outcomes:
    //  - The response contains a valid `signalos-plan` block → upgrade
    //    the bubble to a plan card.
    //  - The response contains a `signalos-plan` block that fails schema
    //    validation → surface the schema issues so the user can ask for
    //    a revision.
    //  - The response has no plan block → leave the bubble as plain AI
    //    text (the conversational path).
    const last = state.chatBubbles.find((b) => b.id === streamId);
    if (last) {
      const result = extractPlanWithErrors(last.text || '');
      if ('tasks' in result) {
        state.chatBubbles = state.chatBubbles.map((b) =>
          b.id === streamId
            ? { ...b, kind: 'plan', plan: result.tasks, planStatus: 'pending' }
            : b
        );
        if (result.backfills && result.backfills.length > 0) {
          const lines = result.backfills.map((bf) => {
            const adds = bf.added.map((a) => `${a.key} (${a.reason})`).join('; ');
            return `• ${bf.taskId}: ${adds}`;
          });
          state.chatBubbles = [
            ...state.chatBubbles,
            {
              id: nowId(),
              kind: 'system',
              text: 'Auto-tagged the following tasks with skills the AI didn\'t mark:\n' + lines.join('\n'),
            },
          ];
        }
      } else if (result.error.kind !== 'no_block') {
        const detail = result.error.perTaskIssues?.length
          ? result.error.perTaskIssues.slice(0, 6).join('\n• ')
          : result.error.details;
        state.chatBubbles = [
          ...state.chatBubbles,
          {
            id: nowId(),
            kind: 'system',
            text: 'Plan didn\'t pass schema validation. Issues:\n• ' + detail + '\n\nAsk Foundry to revise (e.g. "rewrite that plan with valid tiers").',
          },
        ];
      }
      // no_block: conversational path — leave bubble as plain AI text
    }
  } catch (e) {
    showStreamError(streamId, providerConnectionMessage(e, state.ai || 'AI provider'));
  } finally {
    state.busy = false;
  }
}
window.sendMsg = sendMsg;

// WAVE-ENGINE-DESIGN §7 — translator-mode UI hook. Lets the user pick
// (or drag-drop) a local file or paste an external URL (Figma,
// markdown, PDF, .docx) so the engine ingests it and surfaces the
// SignalOS-format version for the gate agent to consume.
//
// Three entry paths (richest first; graceful fallback):
//   1. Tauri native file picker via @tauri-apps/plugin-dialog
//   2. Tauri webview drag-drop event (registered on module load below)
//   3. window.prompt() — last resort for non-Tauri dev / browser
//
// All three call into runTranslatorOn() which is the single render
// path so the chat output is identical regardless of entry method.

async function pickArtifactPath() {
  // Try Tauri native file picker first — works in the desktop shell.
  try {
    const dialog = await import('@tauri-apps/plugin-dialog');
    if (dialog && typeof dialog.open === 'function') {
      const selected = await dialog.open({
        multiple: false,
        directory: false,
        title: 'Translator-mode — pick external artifact',
        filters: [
          { name: 'Supported docs', extensions: ['md', 'markdown', 'pdf', 'docx'] },
          { name: 'All files', extensions: ['*'] },
        ],
      });
      if (typeof selected === 'string' && selected.trim()) {
        return selected.trim();
      }
      // User dismissed the dialog — don't fall through to prompt;
      // that would feel like nagging.
      if (selected === null) return null;
    }
  } catch (err) {
    // Plugin unavailable (non-Tauri browser dev) or runtime error —
    // log + fall through to prompt() so the path still ships.
    if (typeof console !== 'undefined' && console.debug) {
      console.debug('[translator] Tauri dialog unavailable, falling back to prompt():', err && err.message ? err.message : err);
    }
  }

  // Fallback: window.prompt for URL entry (Figma / generic URL — no
  // file picker UI handles those) and for non-Tauri dev environments.
  if (typeof window !== 'undefined' && typeof window.prompt === 'function') {
    return window.prompt(
      'Translator-mode (WAVE-ENGINE-DESIGN §7)\n\n'
      + 'Paste a local file path (.md / .pdf / .docx) or a URL '
      + '(Figma / generic):'
    );
  }
  return null;
}

export async function attachExternalDoc() {
  const input = await pickArtifactPath();
  const artifact = (input || '').trim();
  if (!artifact) return;
  await runTranslatorOn(artifact);
}

async function runTranslatorOn(artifact) {
  if (!artifact) return;

  // Show the user what we're ingesting so the next system bubble has
  // visible provenance.
  state.chatBubbles = [...state.chatBubbles, {
    id: nowId(),
    kind: 'user',
    text: `[Translator-mode] ${artifact}`,
    ts: 'just now',
  }];

  let result = null;
  try {
    result = await waveEngineTranslateExternal(artifact);
  } catch (err) {
    state.chatBubbles = [...state.chatBubbles, {
      id: nowId(),
      kind: 'error',
      text: 'Translator-mode failed: ' + (err && err.message ? err.message : String(err)),
    }];
    return;
  }

  // Engine bubble (re-route style, names the gate + format).
  if (result && result.system_bubble && result.system_bubble.text) {
    state.chatBubbles = [...state.chatBubbles, {
      id: nowId(),
      kind: 'system',
      text: result.system_bubble.text,
      gate: result.gate || null,
      waveAction: 'translator-result',
    }];
  }

  // Body bubble — for markdown/pdf/docx the extracted text goes into
  // an AI bubble (so the user can copy it / refine). For URL/Figma
  // shapes the body is empty by design (recorded as reference); we
  // just confirm the reference was captured.
  const t = result && result.translation;
  if (t && t.supported && t.text) {
    const preview = t.text.length > 4000 ? t.text.slice(0, 4000) + '\n\n[…trimmed]' : t.text;
    addAIBubble(preview);
  } else if (t && t.supported && (t.source_url || t.source_path)) {
    const where = t.source_url || t.source_path;
    addAIBubble(`Recorded ${t.format} reference: ${where}`);
  } else if (t && !t.supported) {
    const hint = t.install_hint
      ? `Install hint: ${t.install_hint}`
      : (t.error ? `Error: ${t.error}` : 'See translator output for details.');
    state.chatBubbles = [...state.chatBubbles, {
      id: nowId(),
      kind: 'error',
      text: `Translator-mode could not ingest ${t.format || 'this artifact'}. ${hint}`,
    }];
  }
}
window.attachFile = attachExternalDoc;

// WAVE-ENGINE-DESIGN §7 — Tauri webview drag-drop registration.
// When the user drops a file onto the Foundry window, route the first
// path through the same translator pipeline as the paperclip button.
// Registered once at module load; non-Tauri dev environments silently
// no-op because the @tauri-apps/api import resolves to a stub.
(async function registerWebviewDragDrop() {
  try {
    const webviewMod = await import('@tauri-apps/api/webview');
    if (!webviewMod || typeof webviewMod.getCurrentWebview !== 'function') return;
    const webview = webviewMod.getCurrentWebview();
    if (!webview || typeof webview.onDragDropEvent !== 'function') return;
    await webview.onDragDropEvent(async (event) => {
      const payload = event && event.payload;
      if (!payload || payload.type !== 'drop') return;
      const paths = Array.isArray(payload.paths) ? payload.paths : [];
      if (paths.length === 0) return;
      // Multi-file drop: take the first (engine processes one artifact
      // per turn). The user can drop more in subsequent turns.
      await runTranslatorOn(paths[0]);
    });
  } catch (err) {
    if (typeof console !== 'undefined' && console.debug) {
      console.debug('[translator] webview drag-drop unavailable (non-Tauri dev?):', err && err.message ? err.message : err);
    }
  }
})();

export function addUserBubble(text) {
  state.chatBubbles = [...state.chatBubbles, { id: nowId(), kind: 'user', text, ts: 'just now' }];
}

export function addAIBubble(text) {
  state.chatBubbles = [...state.chatBubbles, { id: nowId(), kind: 'ai', text, ts: 'just now' }];
}

function startStream(streamId) {
  state.chatBubbles = [...state.chatBubbles, { id: streamId, kind: 'streaming', text: '' }];
}

export function appendStreamToken(streamId, delta) {
  state.chatBubbles = state.chatBubbles.map((b) =>
    b.id === streamId ? { ...b, text: b.text + delta } : b
  );
}

export function finaliseStream(streamId) {
  // M2-a: scan the completed LLM reply for secrets / dangerous bash /
  // hallucinated paths before the bubble flips from `streaming` to `ai`.
  // We pull the current bubble text (built up from delta events), pass it
  // through the chat-response guard, and replace the bubble text with the
  // sanitised `clean` string. If any rules fired we also drop a small
  // system bubble so the user knows the reply was filtered, and append an
  // audit-trail entry via the existing audit:append IPC.
  const bubble = state.chatBubbles.find((b) => b.id === streamId);
  const original = bubble && typeof bubble.text === 'string' ? bubble.text : '';
  let cleaned = original;
  let redactions = [];
  try {
    const result = scanChatResponse(original);
    cleaned = result.clean;
    redactions = result.redactions;
  } catch (e) {
    // The guard is best-effort. If a regex blows up (shouldn't happen but
    // we don't want to gate rendering on it) we render the unfiltered text
    // rather than swallowing the model's reply.
    console.warn('[chatResponseGuard] scan failed, rendering original:', e);
  }

  state.chatBubbles = state.chatBubbles.map((b) =>
    b.id === streamId ? { ...b, kind: 'ai', ts: 'just now', text: cleaned } : b
  );

  if (redactions.length > 0) {
    const counts = summariseRedactions(redactions);
    const summary =
      `⚠ Filtered: ${counts.secret} secret, ${counts.dangerousBash} dangerous bash, ` +
      `${counts.hallucinatedPath} flagged path. Original LLM output is in your audit trail.`;
    state.chatBubbles = [
      ...state.chatBubbles,
      { id: nowId(), kind: 'system', text: summary, ts: 'just now' },
    ];

    // Append an entry to .signalos/AUDIT_TRAIL.jsonl via the sidecar.
    // Fire-and-forget: a failure to write the audit trail (e.g. no
    // workspace selected) must not block rendering the filtered reply.
    const prompt = streamPrompts.get(streamId) || '';
    const entry = {
      action: 'chat-response-filtered',
      kind_counts: counts,
      prompt_head: prompt.slice(0, 80),
      redactions: redactions.map((r) => ({
        kind: r.kind,
        reason: r.reason,
        original: r.original,
        replacement: r.replacement,
      })),
    };
    try {
      // Tauri's invoke for an unknown command throws; ipc.signal.runAndWait
      // wraps that in a Promise rejection we swallow below.
      ipc.signal
        .runAndWait('audit:append', [JSON.stringify(entry)], 5000)
        .catch((e) => console.warn('[chatResponseGuard] audit append failed:', e && e.message ? e.message : e));
    } catch (e) {
      console.warn('[chatResponseGuard] audit append threw synchronously:', e);
    }
  }

  streamPrompts.delete(streamId);
}

export function showStreamError(streamId, msg) {
  const raw = providerConnectionMessage(msg, state.ai || 'AI provider');
  const helpful = raw && raw !== 'undefined'
    ? raw
    : 'Chat provider did not return a usable response. Open Settings, select a provider and model, then retry.';
  state.chatBubbles = state.chatBubbles.map((b) =>
    b.id === streamId ? { ...b, kind: 'error', text: 'Error: ' + helpful } : b
  );
  streamPrompts.delete(streamId);
  showError(helpful);
}

function composerInput(_e) {
  const val = state.chatInputValue || '';
  state.cmdPaletteOpen = val.startsWith('/');
}
window.composerInput = composerInput;

function composerKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMsg();
  }
  if (e.key === 'Escape') {
    state.cmdPaletteOpen = false;
  }
}
window.composerKey = composerKey;

function runCmd(cmd) {
  state.chatInputValue = cmd;
  state.cmdPaletteOpen = false;
  sendMsg();
}
window.runCmd = runCmd;

function sendChip(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return;
  if (!state.aiModel) {
    showError('Select a model before sending a provider chat message.');
    return;
  }
  addUserBubble(trimmed);
  const streamId = nowId();
  startStream(streamId);
  // M2-a: track the prompt for the response guard's audit trail.
  streamPrompts.set(streamId, trimmed);
  ipc.provider
    .chatStream(streamId, state.ai, state.aiModel, trimmed)
    .then(() => ipc.provider.getCost().then(updateCostDisplay).catch(() => {}))
    .catch((e) => showStreamError(streamId, providerConnectionMessage(e, state.ai || 'AI provider')));
}
window.sendChip = sendChip;
