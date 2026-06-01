// ToolCallBubble.tsx — compact card showing a single agent tool call
// (Phase 1.3). Renders read/write/edit file ops, command execution, and
// search, with a spinner while running and a checkmark (or error icon) when
// done. Drives test T15 ("tool call shown in chat").
//
// Preact only. State for this bubble lives in the chatBubbles signal; this is
// a pure presentational component driven by props so it can be unit-tested in
// isolation and reused by agentEvents.ts (Phase 3) without DOM coupling.

import type { VNode } from 'preact';

export type ToolKind =
  | 'read_file'
  | 'write_file'
  | 'edit_file'
  | 'run_command'
  | 'search_files'
  | string;

export type ToolStatus = 'running' | 'done' | 'error' | 'denied';

export interface ToolCallBubbleProps {
  /** The tool that was invoked. */
  tool: ToolKind;
  /** Primary argument — file path for file ops, the command for run_command,
   *  the query for search. */
  target?: string;
  /** Current execution status. */
  status: ToolStatus;
  /** Optional short result/summary line (e.g. "42 lines", "exit 0"). */
  summary?: string;
  /** Optional command/search output or denial reason, shown when present. */
  detail?: string;
}

const TOOL_META: Record<string, { icon: string; verb: string }> = {
  read_file: { icon: 'ti-file-text', verb: 'Reading' },
  write_file: { icon: 'ti-file-plus', verb: 'Writing' },
  edit_file: { icon: 'ti-edit', verb: 'Editing' },
  run_command: { icon: 'ti-terminal-2', verb: 'Running' },
  search_files: { icon: 'ti-search', verb: 'Searching' },
};

function meta(tool: ToolKind) {
  return TOOL_META[tool] || { icon: 'ti-tool', verb: 'Tool' };
}

function statusIcon(status: ToolStatus): VNode {
  if (status === 'running') {
    return <i className="ti ti-loader-2 tcb-spin" aria-label="running"></i>;
  }
  if (status === 'error') {
    return <i className="ti ti-alert-circle" aria-label="error"></i>;
  }
  if (status === 'denied') {
    return <i className="ti ti-shield-off" aria-label="denied"></i>;
  }
  return <i className="ti ti-check" aria-label="done"></i>;
}

export function ToolCallBubble({ tool, target, status, summary, detail }: ToolCallBubbleProps) {
  const { icon, verb } = meta(tool);
  const cls = `tool-call tool-call-${status}`;
  return (
    <div className="msg spark" data-testid="tool-call-bubble">
      <div className="msg-av"><i className="ti ti-sparkles" style={{ fontSize: '17px' }}></i></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className={cls} data-tool={tool} data-status={status}>
          <div className="tool-call-ic"><i className={`ti ${icon}`}></i></div>
          <div className="tool-call-tx">
            <span className="tool-call-verb">{verb}</span>
            {target ? <span className="tool-call-target">{target}</span> : null}
            {summary ? <span className="tool-call-summary">{summary}</span> : null}
          </div>
          <div className="tool-call-status">{statusIcon(status)}</div>
        </div>
        {detail ? (
          <pre className="tool-call-detail" data-testid="tool-call-detail">{detail}</pre>
        ) : null}
      </div>
    </div>
  );
}
