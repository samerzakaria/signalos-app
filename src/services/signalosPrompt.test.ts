import { describe, it, expect } from 'vitest';
import {
  isBuildIntent,
  extractPlanFromResponse,
  extractPlanWithErrors,
  planToYaml,
  planToMarkdownTaskList,
  wrapWithSignalosContext,
  inferMissingSkills,
  buildAgentBlock,
} from './signalosPrompt';

// --------------------------------------------------------------------------
// isBuildIntent
// --------------------------------------------------------------------------

describe('isBuildIntent', () => {
  it('returns true for the canonical build phrasings', () => {
    expect(isBuildIntent('build me a todo app')).toBe(true);
    expect(isBuildIntent('Create a Vite + Preact project')).toBe(true);
    expect(isBuildIntent('make a markdown editor')).toBe(true);
    expect(isBuildIntent('implement OAuth login')).toBe(true);
    expect(isBuildIntent('scaffold a Next.js app')).toBe(true);
    expect(isBuildIntent('design a landing page')).toBe(true);
    expect(isBuildIntent('Set up Tailwind')).toBe(true);
  });

  it('returns false for slash commands (those route to the sidecar separately)', () => {
    expect(isBuildIntent('/signal-build')).toBe(false);
    expect(isBuildIntent('/build something now')).toBe(false);
  });

  it('returns false for chat / casual messages', () => {
    expect(isBuildIntent('hi')).toBe(false);
    expect(isBuildIntent('what is signal-build?')).toBe(false);
    expect(isBuildIntent('how do gates work')).toBe(false);
    expect(isBuildIntent('thanks')).toBe(false);
  });

  it('rejects messages too short to be intentful', () => {
    expect(isBuildIntent('build')).toBe(false);
    expect(isBuildIntent('make')).toBe(false);
  });

  it('requires the keyword on a word boundary', () => {
    // "rebuild" contains "build" but only as a suffix -- our regex uses \b
    expect(isBuildIntent('explain the rebuild process')).toBe(false);
  });
});

// --------------------------------------------------------------------------
// extractPlanWithErrors — strict schema validation
// --------------------------------------------------------------------------

const wellFormedPlan = `Acknowledged.

\`\`\`signalos-plan
[
  {"id":"task-001","title":"Scaffold project","files":["package.json"],"tier":"T2","effort_days":0.3,"status":"pending"},
  {"id":"task-002","title":"Write TodoList","files":["src/TodoList.tsx"],"tier":"T2","effort_days":0.5,"status":"pending"}
]
\`\`\`

Two tasks. Files listed above.`;

describe('extractPlanWithErrors', () => {
  it('returns tasks for a well-formed plan block', () => {
    const result = extractPlanWithErrors(wellFormedPlan);
    expect('tasks' in result).toBe(true);
    if ('tasks' in result) {
      expect(result.tasks).toHaveLength(2);
      expect(result.tasks[0].id).toBe('task-001');
      expect(result.tasks[0].title).toBe('Scaffold project');
      expect(result.tasks[0].files).toEqual(['package.json']);
      expect(result.tasks[0].tier).toBe('T2');
    }
  });

  it('returns error: no_block when no fenced signalos-plan exists', () => {
    const r = extractPlanWithErrors('Just chatting, no plan here.');
    expect('error' in r).toBe(true);
    if ('error' in r) expect(r.error.kind).toBe('no_block');
  });

  it('returns error: invalid_json when block body fails JSON.parse', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{not valid json}]\n```');
    expect('error' in r).toBe(true);
    if ('error' in r) expect(r.error.kind).toBe('invalid_json');
  });

  it('returns error: not_array when block contains an object instead of an array', () => {
    const r = extractPlanWithErrors('```signalos-plan\n{"tasks":[]}\n```');
    expect('error' in r).toBe(true);
    if ('error' in r) expect(r.error.kind).toBe('not_array');
  });

  it('rejects tasks with missing title', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"id":"task-001","files":["a.ts"]}]\n```');
    expect('error' in r).toBe(true);
    if ('error' in r) {
      expect(r.error.kind).toBe('schema_errors');
      expect(r.error.perTaskIssues?.[0]).toMatch(/missing title/);
    }
  });

  it('rejects tasks with invalid tier', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"title":"x","tier":"BANANA","files":["a.ts"]}]\n```');
    expect('error' in r).toBe(true);
    if ('error' in r) expect(r.error.perTaskIssues?.[0]).toMatch(/invalid tier/i);
  });

  it('normalises lowercase tier to uppercase', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"title":"x","tier":"t1","files":["a.ts"]}]\n```');
    expect('tasks' in r).toBe(true);
    if ('tasks' in r) expect(r.tasks[0].tier).toBe('T1');
  });

  it('rejects file paths that escape the workspace', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"title":"x","tier":"T2","files":["../../etc/passwd"]}]\n```');
    expect('error' in r).toBe(true);
    if ('error' in r) expect(r.error.perTaskIssues?.[0]).toMatch(/escapes workspace/);
  });

  it('rejects file paths starting with /', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"title":"x","tier":"T2","files":["/etc/hosts"]}]\n```');
    expect('error' in r).toBe(true);
  });

  it('rejects Windows-drive-letter file paths', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"title":"x","tier":"T2","files":["C:\\\\windows"]}]\n```');
    expect('error' in r).toBe(true);
  });

  it('rejects tasks with empty file lists (orchestrator needs file paths)', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"title":"x","tier":"T2","files":[]}]\n```');
    expect('error' in r).toBe(true);
    if ('error' in r) expect(r.error.perTaskIssues?.[0]).toMatch(/no files declared/);
  });

  it('rejects effort_days outside [0, 5]', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"title":"x","tier":"T2","files":["a"],"effort_days":99}]\n```');
    expect('error' in r).toBe(true);
    if ('error' in r) expect(r.error.perTaskIssues?.[0]).toMatch(/effort_days out of range/);
  });

  it('auto-generates a task id when none is supplied', () => {
    const r = extractPlanWithErrors('```signalos-plan\n[{"title":"first","tier":"T2","files":["a"]},{"title":"second","tier":"T2","files":["b"]}]\n```');
    expect('tasks' in r).toBe(true);
    if ('tasks' in r) {
      expect(r.tasks[0].id).toBe('task-001');
      expect(r.tasks[1].id).toBe('task-002');
    }
  });
});

describe('extractPlanFromResponse (back-compat wrapper)', () => {
  it('returns null on error instead of an error object', () => {
    expect(extractPlanFromResponse('no plan')).toBeNull();
    expect(extractPlanFromResponse('```signalos-plan\n{}\n```')).toBeNull();
  });

  it('returns the extraction object on success', () => {
    const r = extractPlanFromResponse(wellFormedPlan);
    expect(r).not.toBeNull();
    expect(r?.tasks).toHaveLength(2);
  });
});

// --------------------------------------------------------------------------
// planToYaml — orchestrator-readable schema
// --------------------------------------------------------------------------

describe('planToYaml', () => {
  it('emits the orchestrator schema with wave + tasks keys', () => {
    const yaml = planToYaml([
      { id: 'task-001', title: 'A', files: ['a.ts'], tier: 'T2', effort_days: 0.5, status: 'pending' },
    ], '1');
    expect(yaml).toMatch(/^wave: "1"$/m);
    expect(yaml).toMatch(/^tasks:$/m);
    expect(yaml).toMatch(/id: "task-001"/);
    expect(yaml).toMatch(/title: "A"/);
    expect(yaml).toMatch(/tier: T2/);
    expect(yaml).toMatch(/status: pending/);
  });

  it('lists files under the task with proper indent', () => {
    const yaml = planToYaml([
      { id: 'task-001', title: 'A', files: ['src/foo.ts', 'src/bar.ts'], tier: 'T2' },
    ], '1');
    expect(yaml).toMatch(/    files:\n      - "src\/foo\.ts"\n      - "src\/bar\.ts"/);
  });

  it('escapes title with JSON-quoting so colons and quotes are safe', () => {
    const yaml = planToYaml([
      { id: 'task-001', title: 'Add: "real" thing', files: ['a.ts'], tier: 'T2' },
    ], '1');
    // JSON.stringify produces: "Add: \"real\" thing"
    expect(yaml).toMatch(/title: "Add: \\"real\\" thing"/);
  });

  it('omits the optional description when not provided', () => {
    const yaml = planToYaml([
      { id: 'task-001', title: 'A', files: ['a.ts'], tier: 'T2' },
    ], '1');
    expect(yaml).not.toMatch(/description:/);
  });
});

// --------------------------------------------------------------------------
// wrapWithSignalosContext
// --------------------------------------------------------------------------

describe('wrapWithSignalosContext (AMD-CORE-102: always-wrap)', () => {
  it('passes slash commands through unchanged (they route elsewhere)', () => {
    const msg = '/signal-status';
    expect(wrapWithSignalosContext(msg)).toBe(msg);
  });

  it('wraps conversational/non-build messages WITH the protocol preamble', () => {
    // The whole point of AMD-CORE-102: no regex gate. Even "hi there" is
    // wrapped so the LLM gets the SignalOS protocol context and decides
    // for itself whether to chat or emit a plan.
    const msg = 'hi there';
    const wrapped = wrapWithSignalosContext(msg);
    expect(wrapped).not.toBe(msg);
    expect(wrapped).toMatch(/You are SignalOS/);
    expect(wrapped).toMatch(/signalos-plan/);
    expect(wrapped).toMatch(/hi there/);
  });

  it('wraps build-intent messages and signals the plan path is likely', () => {
    const wrapped = wrapWithSignalosContext('build me a todo app');
    expect(wrapped).toMatch(/You are SignalOS/);
    expect(wrapped).toMatch(/signalos-plan/);
    expect(wrapped).toMatch(/build me a todo app/);
    expect(wrapped).toMatch(/emitting a `signalos-plan` block is likely the right response/);
  });

  it('wraps a non-regex natural-language build request (the v0.1 #6 case)', () => {
    // "I want to do a financial dashboard" had no regex match before
    // AMD-CORE-102 — would have been sent unwrapped. Now it wraps with
    // the conversational-default hint; the LLM can still emit a plan
    // if it judges that to be the right shape.
    const wrapped = wrapWithSignalosContext('I want to do a financial dashboard');
    expect(wrapped).toMatch(/You are SignalOS/);
    expect(wrapped).toMatch(/signalos-plan/);
    expect(wrapped).toMatch(/I want to do a financial dashboard/);
    expect(wrapped).toMatch(/Default to a conversational reply.*decide the user is asking/s);
  });

  it('the wrapped message ends with the original user request', () => {
    const wrapped = wrapWithSignalosContext('create an FTP client');
    expect(wrapped.endsWith('User request:\ncreate an FTP client')).toBe(true);
  });

  it('teaches the AI about the skills catalog so it can tag tasks', () => {
    const wrapped = wrapWithSignalosContext('build me a login form');
    expect(wrapped).toMatch(/"skills"/);
    expect(wrapped).toMatch(/security-audit/);
    expect(wrapped).toMatch(/test-generation/);
    expect(wrapped).toMatch(/comprehensive-code-review/);
  });

  it('tells agents to protect non-technical users from implementation questions', () => {
    const wrapped = wrapWithSignalosContext('build a team task app');
    expect(wrapped).toMatch(/The user may be non-technical/);
    expect(wrapped).toMatch(/Do not ask them to choose frameworks, libraries, databases/);
    expect(wrapped).toMatch(/Make technical decisions yourself/);
  });
});

// --------------------------------------------------------------------------
// skills field round-trip
// --------------------------------------------------------------------------

describe('skills field (plan->orchestrator handoff)', () => {
  function plan(tasks: Record<string, unknown>[]): string {
    return '```signalos-plan\n' + JSON.stringify(tasks) + '\n```';
  }

  const base = {
    id: 'task-001',
    title: 'Add auth',
    files: ['src/auth.ts'],
    tier: 'T2',
    effort_days: 0.5,
    status: 'pending',
  };

  it('preserves valid skill keys on the parsed task', () => {
    const result = extractPlanWithErrors(plan([{ ...base, skills: ['security-audit', 'test-generation'] }]));
    if ('error' in result) throw new Error('expected success: ' + result.error.details);
    expect(result.tasks[0].skills).toEqual(['security-audit', 'test-generation']);
  });

  it('lower-cases and trims skill keys for resilience', () => {
    const result = extractPlanWithErrors(plan([{ ...base, skills: ['  Security-Audit  ', 'TEST-GENERATION'] }]));
    if ('error' in result) throw new Error('expected success');
    expect(result.tasks[0].skills).toEqual(['security-audit', 'test-generation']);
  });

  it('silently drops unknown skill keys (forward compatible)', () => {
    const result = extractPlanWithErrors(plan([{ ...base, skills: ['security-audit', 'made-up-skill'] }]));
    if ('error' in result) throw new Error('expected success');
    expect(result.tasks[0].skills).toEqual(['security-audit']);
  });

  it('rejects non-string skill entries', () => {
    const result = extractPlanWithErrors(plan([{ ...base, skills: ['security-audit', 42] }]));
    expect('error' in result).toBe(true);
  });

  it('omits skills field entirely when absent (no empty array noise)', () => {
    // Use a title that doesn't trigger heuristic backfill, so the
    // "no skills" path stays observable in this test.
    const innocuous = { ...base, title: 'Render the home page', files: ['src/Home.tsx'] };
    const result = extractPlanWithErrors(plan([innocuous]));
    if ('error' in result) throw new Error('expected success');
    expect(result.tasks[0].skills).toBeUndefined();
  });

  it('planToYaml emits skills entries when present', () => {
    const yaml = planToYaml(
      [{ id: 'task-001', title: 'Audit auth', files: ['src/auth.ts'], tier: 'T2', effort_days: 0.5, status: 'pending', skills: ['security-audit'] }],
      '1',
    );
    expect(yaml).toMatch(/skills:\s*\n\s+-\s+"security-audit"/);
  });

  it('planToYaml skips the skills block when no skills are attached', () => {
    const yaml = planToYaml(
      [{ id: 'task-001', title: 'Simple task', files: ['src/x.ts'], tier: 'T2', effort_days: 0.5, status: 'pending' }],
      '1',
    );
    expect(yaml).not.toMatch(/skills:/);
  });
});

// --------------------------------------------------------------------------
// planToMarkdownTaskList -- companion file the bash worktree script parses
// --------------------------------------------------------------------------

describe('planToMarkdownTaskList', () => {
  // The exact regex worktree-manager.sh runs against PLAN.md. If our
  // emission stops matching this, the with-bash orchestrator path silently
  // finds zero tasks. Re-run this test when either side changes.
  // grep -oE '<!--\s*task:\s*id=[^ >]+[^>]*-->'
  const TASK_COMMENT_RE = /<!--\s*task:\s*id=[^ >]+[^>]*-->/g;
  // grep -oE 'id=[^ >]+' (extracts the id token from the matched comment)
  const ID_TOKEN_RE = /id=[^ >]+/;
  // grep -oE 'tier=(T[123])' (extracts the tier)
  const TIER_TOKEN_RE = /tier=(T[123])/;

  it('emits one HTML-comment task marker per task', () => {
    const md = planToMarkdownTaskList(
      [
        { id: 'task-001', title: 'A', files: ['a.ts'], tier: 'T2', effort_days: 0.5, status: 'pending' },
        { id: 'task-002', title: 'B', files: ['b.ts'], tier: 'T1', effort_days: 0.5, status: 'pending' },
      ],
      '1',
    );
    const matches = md.match(TASK_COMMENT_RE) || [];
    expect(matches).toHaveLength(2);
  });

  it('matches the exact bash regex that worktree-manager.sh uses', () => {
    const md = planToMarkdownTaskList(
      [{ id: 'task-001', title: 'A', files: ['a.ts'], tier: 'T2', effort_days: 0.5, status: 'pending' }],
      '1',
    );
    const comment = md.match(TASK_COMMENT_RE)?.[0];
    expect(comment).toBeDefined();
    expect(comment!.match(ID_TOKEN_RE)?.[0]).toBe('id=task-001');
    expect(comment!.match(TIER_TOKEN_RE)?.[0]).toBe('tier=T2');
  });

  it('defaults missing tier to T2 (matches planToYaml behavior)', () => {
    const md = planToMarkdownTaskList(
      [{ id: 'task-001', title: 'A', files: ['a.ts'], effort_days: 0.5, status: 'pending' }],
      '1',
    );
    expect(md.match(TIER_TOKEN_RE)?.[0]).toBe('tier=T2');
  });

  it('emits a # heading with the wave id so humans reading PLAN.md know what they have', () => {
    const md = planToMarkdownTaskList([], '7');
    expect(md).toMatch(/^# Wave 7/m);
  });

  it('renders human-readable task sections (title + files) under the HTML comment', () => {
    const md = planToMarkdownTaskList(
      [{ id: 'task-001', title: 'Implement TodoList', files: ['src/TodoList.tsx', 'src/storage.ts'], tier: 'T2', effort_days: 0.5, status: 'pending' }],
      '1',
    );
    expect(md).toMatch(/### Implement TodoList/);
    expect(md).toMatch(/`src\/TodoList\.tsx`/);
    expect(md).toMatch(/`src\/storage\.ts`/);
  });

  it('includes the skills line when a task has explicit skills', () => {
    const md = planToMarkdownTaskList(
      [{ id: 'task-001', title: 'Audit auth', files: ['src/auth.ts'], tier: 'T2', effort_days: 0.5, status: 'pending', skills: ['security-audit'] }],
      '1',
    );
    expect(md).toMatch(/\*\*Skills:\*\* security-audit/);
  });
});

// --------------------------------------------------------------------------
// inferMissingSkills + auto-tag backfill (defense in depth against the AI
// forgetting to tag a task that clearly needs a skill)
// --------------------------------------------------------------------------

describe('inferMissingSkills (heuristic backfill)', () => {
  it('flags security-audit on auth / login / password titles', () => {
    expect(inferMissingSkills('Add login form', '', ['src/Login.tsx'], []).map((b) => b.key))
      .toContain('security-audit');
    expect(inferMissingSkills('Hash user passwords with bcrypt', '', [], []).map((b) => b.key))
      .toContain('security-audit');
    expect(inferMissingSkills('Validate user input on the composer', '', [], []).map((b) => b.key))
      .toContain('security-audit');
  });

  it('flags security-audit on XSS / CSRF / SQL-injection phrasing', () => {
    expect(inferMissingSkills('Fix XSS in markdown preview', '', [], []).map((b) => b.key))
      .toContain('security-audit');
    expect(inferMissingSkills('Sanitize html before render', '', [], []).map((b) => b.key))
      .toContain('security-audit');
    expect(inferMissingSkills('Patch SQL injection in search', '', [], []).map((b) => b.key))
      .toContain('security-audit');
  });

  it('flags security-audit on IPC / Tauri-command surfaces', () => {
    expect(inferMissingSkills('Add Tauri command to read workspace file', '', [], []).map((b) => b.key))
      .toContain('security-audit');
  });

  it('flags test-generation on .test/.spec file paths', () => {
    expect(inferMissingSkills('Add storage logic', '', ['src/storage.ts', 'src/storage.test.ts'], []).map((b) => b.key))
      .toContain('test-generation');
  });

  it('flags test-generation on "add tests" / "expand coverage" language', () => {
    expect(inferMissingSkills('Add unit tests for filter logic', '', [], []).map((b) => b.key))
      .toContain('test-generation');
    expect(inferMissingSkills('Expand coverage on parser', '', [], []).map((b) => b.key))
      .toContain('test-generation');
  });

  it('flags systematic-debugging on "fix the bug" phrasing', () => {
    expect(inferMissingSkills('Fix the crash on save', '', [], []).map((b) => b.key))
      .toContain('systematic-debugging');
    expect(inferMissingSkills('Reproduce the regression in wave 3', '', [], []).map((b) => b.key))
      .toContain('systematic-debugging');
  });

  it('does NOT re-add a skill the AI already tagged', () => {
    const inferred = inferMissingSkills('Add login form', '', ['src/Login.tsx'], ['security-audit']);
    expect(inferred.map((b) => b.key)).not.toContain('security-audit');
  });

  it('does NOT trigger false positives on innocuous tasks', () => {
    const inferred = inferMissingSkills(
      'Implement the TodoList component',
      'Renders a list of todos with check/uncheck',
      ['src/TodoList.tsx'],
      [],
    );
    expect(inferred).toEqual([]);
  });

  it('extractPlanWithErrors emits backfills alongside tasks', () => {
    const response = '```signalos-plan\n' + JSON.stringify([
      // The AI tagged 'security-audit' correctly here.
      { id: 'task-001', title: 'Sanitize user input', files: ['src/sanitize.ts'], tier: 'T2', effort_days: 0.5, status: 'pending', skills: ['security-audit'] },
      // The AI forgot to tag this one even though "login" is in the title.
      { id: 'task-002', title: 'Add login form', files: ['src/Login.tsx'], tier: 'T2', effort_days: 0.5, status: 'pending' },
      // Innocuous; no backfill.
      { id: 'task-003', title: 'Render the home page', files: ['src/Home.tsx'], tier: 'T2', effort_days: 0.3, status: 'pending' },
    ]) + '\n```';

    const result = extractPlanWithErrors(response);
    if ('error' in result) throw new Error('expected success: ' + result.error.details);

    expect(result.backfills).toHaveLength(1);
    expect(result.backfills[0].taskId).toBe('task-002');
    expect(result.backfills[0].added.map((a) => a.key)).toContain('security-audit');
    // And the task itself now carries the backfilled skill.
    expect(result.tasks[1].skills).toContain('security-audit');
    // Untouched task keeps its original tag.
    expect(result.tasks[0].skills).toEqual(['security-audit']);
    // Innocuous task got nothing added.
    expect(result.tasks[2].skills).toBeUndefined();
  });
});


// --------------------------------------------------------------------------
// buildAgentBlock — WAVE-ENGINE-DESIGN §4 per-gate agent injection
// --------------------------------------------------------------------------

describe('buildAgentBlock', () => {
  it('returns empty string for empty/whitespace agent content', () => {
    expect(buildAgentBlock('')).toBe('');
    expect(buildAgentBlock('   \n   ')).toBe('');
  });

  it('emits the agent body inside an "## Active gate agent" header', () => {
    const block = buildAgentBlock('# Agent — Onboarding\n\nMap a product…');
    expect(block).toContain('## Active gate agent');
    expect(block).toContain('# Agent — Onboarding');
    expect(block).toContain('Map a product');
  });

  it('includes the gate label in the header when provided', () => {
    const block = buildAgentBlock('body', 'G0');
    expect(block).toContain('## Active gate agent (G0)');
  });

  it('trims oversized agent bodies to the prompt budget', () => {
    const huge = 'x'.repeat(8000);
    const block = buildAgentBlock(huge);
    expect(block).toContain('trimmed for prompt budget');
    // The body in the block is bounded by the budget + the trim marker.
    expect(block.length).toBeLessThan(huge.length);
  });
});


// --------------------------------------------------------------------------
// wrapWithSignalosContext — options.agentSystemContext integration
// --------------------------------------------------------------------------

describe('wrapWithSignalosContext with agent system context', () => {
  it('injects the agent block into the preamble when content is provided', () => {
    const wrapped = wrapWithSignalosContext('build a thing', {
      agentSystemContext: '# Agent — Onboarding\n\nMap a product…',
      gate: 'G0',
    });
    expect(wrapped).toContain('## Active gate agent (G0)');
    expect(wrapped).toContain('# Agent — Onboarding');
  });

  it('omits the agent block when no agent content provided', () => {
    const wrapped = wrapWithSignalosContext('build a thing');
    expect(wrapped).not.toContain('## Active gate agent');
  });

  it('omits the agent block when content is empty string', () => {
    const wrapped = wrapWithSignalosContext('build a thing', { agentSystemContext: '' });
    expect(wrapped).not.toContain('## Active gate agent');
  });

  it('passes through slash commands without wrapping (agent context ignored)', () => {
    const wrapped = wrapWithSignalosContext('/signal-status', {
      agentSystemContext: '# Agent body',
    });
    expect(wrapped).toBe('/signal-status');
  });

  it('keeps the "## How to respond" section after the agent block', () => {
    const wrapped = wrapWithSignalosContext('build a thing', {
      agentSystemContext: 'Agent guidance',
      gate: 'G2',
    });
    const agentIdx = wrapped.indexOf('## Active gate agent');
    const howIdx = wrapped.indexOf('## How to respond');
    expect(agentIdx).toBeGreaterThan(0);
    expect(howIdx).toBeGreaterThan(agentIdx);
  });
});
