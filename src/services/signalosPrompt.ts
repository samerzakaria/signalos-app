import { userName, userRole, workspacePath, currentWave } from '../state';
import { buildContextBlock } from './protocolContext';

const BUILD_INTENT_RE = /\b(build|create|make|implement|design|add|generate|scaffold|write|set up|set-up|start|develop)\b/i;
const QUESTION_PREFIX_RE = /^(what|why|how|when|where|who|which|explain|tell me|describe|show me|can you tell)\b/i;

export function isBuildIntent(message: string): boolean {
  // Slash commands are routed elsewhere; this is for natural-language messages.
  if (message.startsWith('/')) return false;
  if (message.length < 6) return false;
  // Questions are not intent -- "what is signal-build?", "how does build work?"
  // would otherwise match the regex on the word inside the compound.
  const trimmed = message.trim();
  if (QUESTION_PREFIX_RE.test(trimmed)) return false;
  if (trimmed.endsWith('?')) return false;
  return BUILD_INTENT_RE.test(message);
}

export function wrapWithSignalosContext(userMessage: string): string {
  const ws = workspacePath.value || '(workspace not set)';
  const who = userName.value || 'the user';
  const role = userRole.value || 'PO';
  const wave = currentWave.value || '1';
  const buildIntent = isBuildIntent(userMessage);

  if (!buildIntent) {
    return userMessage;
  }

  const ctx = buildContextBlock();

  const preamble = `You are SignalOS, a guided AI build orchestrator running locally on ${who}'s machine.

Project workspace: ${ws}
Current wave: ${wave}
Signer role: ${role}
${ctx}
The user is asking you to build something. Follow this protocol:

1. Respond with one short sentence acknowledging what you'll build.

2. Then output a single fenced block with the language tag \`signalos-plan\` containing a JSON array of tasks. Schema for each task:
   {
     "id": "task-001",          // sequential, zero-padded
     "title": "Short title",    // <= 80 chars, imperative voice
     "description": "What needs to happen, including file paths",
     "files": ["src/path/to/file.tsx", "..."],   // files this task creates or modifies
     "tier": "T2",              // T1 Proceed | T2 Propose | T3 Suggest
     "effort_days": 0.5,        // 0.1 .. 1.0
     "status": "pending",
     "skills": ["security-audit", "test-generation"]  // optional, see step 4
   }

3. Constraints:
   - Maximum 8 tasks
   - Each task must be completable in under 30 minutes by an AI agent
   - Order tasks so each can run independently (dependencies via depends_on if needed)
   - Use realistic file paths relative to the workspace root
   - Prefer minimal scope: ship a working v0, defer polish to a later wave

4. Attach \`skills\` only when a task genuinely needs that domain guidance. Available keys (pick zero or more per task):
   Build:
   - "test-driven-development"  - writing failing tests FIRST, then the implementation. Enforced: a *.test.* file must run and fail before the impl is written.
   - "test-generation"          - adding/expanding tests AFTER code exists. Enforced: a *.test.* file must be produced.
   - "systematic-debugging"     - reproducing/fixing a reported bug. Artifact: .signalos/debug/<task>.md with Reproduce/Hypothesis/Test/Fix.
   - "verification-before-completion" - final self-check before claiming done.
   Plan:
   - "writing-plans"            - decomposing a large feature into tasks.
   - "executing-plans"          - dispatching tasks across waves.
   Review:
   - "comprehensive-code-review" - reviewing existing code for quality + safety. Artifact: .signalos/reviews/<task>.md with severity sections.
   - "receiving-code-review"    - addressing reviewer feedback. Artifact: .signalos/responses/<task>.md mapping each comment to action.
   - "requesting-code-review"   - preparing a PR for review.
   Governance:
   - "security-audit"           - auth, input validation, secrets, file paths, IPC, untrusted-input surfaces. Lint-enforced.
   - "retro-run" / "retrospective-analyze" - wave retros + trend analysis.
   Subagents:
   - "subagent-driven-development" / "dispatching-parallel-agents" - work that benefits from parallel sub-tasks.
   Worktree:
   - "using-git-worktrees" / "finishing-a-development-branch" - parallel branch work and cleanup.
   Cognitive (advisory; loaded when relevant): "belief-seed-generation", "brainstorming", "compress-context", "context", "design", "existing-product-kit", "headless-execution", "intent-router", "memory", "observability-dashboard", "operator-tooling", "parallel-orchestration", "plugin-registry", "product-surface-mapping", "review", "session-journal", "stakeholder-interview", "task-schema".
   Omit \`skills\` (or use \`[]\`) when the task is a straightforward implementation. The validator backfills obvious ones (e.g. tasks with "login" auto-tag "security-audit"); do not over-tag.

5. After the fenced block, write one short paragraph listing the files that will be created and any setup the user needs to do (install dependencies, set environment variables, etc.).

6. Do NOT write code in the response. The orchestrator will dispatch each task to a per-worktree harness that writes the actual files.

User request:
${userMessage}`;

  return preamble;
}

export interface PlanExtraction {
  tasks: import('../state').PlanTask[];
  rawJson: string;
  /** Per-task list of skills the validator added that the AI didn't tag.
   *  Empty array means the AI got every tag right. Surface via system
   *  bubble so the user sees defense-in-depth in action and we can
   *  measure tagging accuracy over time. */
  backfills: SkillBackfill[];
}

export interface PlanValidationError {
  kind: 'no_block' | 'invalid_json' | 'not_array' | 'no_valid_tasks' | 'schema_errors';
  details: string;
  perTaskIssues?: string[];
}

const VALID_TIERS = new Set(['T1', 'T2', 'T3']);
const VALID_STATUSES = new Set(['pending', 'in_progress', 'completed', 'failed', 'aborted', 'paused']);

// Catalog of skill keys the AI may attach to a task. Must stay in sync with
// _SKILL_KEY_TO_PATH in python/signalos_lib/orchestrator.py. All 34 routable
// bundle skills are listed -- the JS test_catalog_matches_js_side check
// (python/test_orchestrator_skills.py) breaks the build if they drift.
export const VALID_SKILL_KEYS = new Set([
  // Build
  'test-driven-development',
  'test-generation',
  'e2e-testing',
  'systematic-debugging',
  'verification-before-completion',
  // Plan
  'writing-plans',
  'executing-plans',
  // Review
  'comprehensive-code-review',
  'receiving-code-review',
  'requesting-code-review',
  // Governance
  'security-audit',
  'retro-run',
  'retrospective-analyze',
  // Subagents
  'subagent-driven-development',
  'dispatching-parallel-agents',
  // Worktree
  'using-git-worktrees',
  'finishing-a-development-branch',
  // Cognitive / process
  'belief-seed-generation',
  'brainstorming',
  'compress-context',
  'context',
  'design',
  'existing-product-kit',
  'headless-execution',
  'intent-router',
  'memory',
  'observability-dashboard',
  'operator-tooling',
  'parallel-orchestration',
  'plugin-registry',
  'product-surface-mapping',
  'review',
  'session-journal',
  'stakeholder-interview',
  'task-schema',
]);

// Server-side defense in depth: if the AI forgets to tag a task that
// clearly needs a skill (e.g. "Add login form" without security-audit),
// these triggers backfill the missing tag before the plan reaches disk.
// The AI is then a suggester, not the sole authority -- regardless of
// whether it tags 95% or 40% correctly, we backfill the rest.
//
// Triggers run against title + description + file paths together.
// First match wins per key; one key never duplicates an existing tag.
const SKILL_TRIGGERS: Array<{ key: string; pattern: RegExp; reason: string }> = [
  {
    key: 'security-audit',
    // Auth, validation, sanitization, crypto, secrets, IPC boundaries.
    // We use \w* on stems so "injection", "encrypted", "sanitised" all
    // match without exploding the alternation into every conjugation.
    pattern: /\b(auth\w*|login|logout|signup|password\w*|session\w*|token\w*|jwt|oauth|cookie\w*|xss|csrf|sql\s*inject\w*|inject\w*|sanitiz\w*|sanitis\w*|escape\w*|user\s*input|secret\w*|credential\w*|api[\s-]*key|encrypt\w*|decrypt\w*|hash\w*|cors|csp|ipc|tauri\s*command|allowlist|allow-list)\b|validate.{0,15}input/i,
    reason: 'security-sensitive surface (auth/validation/crypto/IPC)',
  },
  {
    key: 'test-generation',
    pattern: /\.(test|spec)\.(ts|tsx|js|jsx|py|mjs)\b|\b(add|write|generate|expand|backfill)\s+\w*\s*tests?\b|\btest\s+suite\b|\bcoverage\b/i,
    reason: 'test files or "add tests" language',
  },
  {
    key: 'e2e-testing',
    // Auto-tag only when the task is clearly producing a NEW user-facing
    // surface -- "build/add/create/implement the {form,page,view,...}".
    // We deliberately don't fire on every *.tsx edit because that would
    // trigger a 60s dev-server-start + Playwright run on routine
    // component tweaks. The AI can explicitly tag e2e-testing on tasks
    // we miss.
    pattern: /\b(build|add|create|implement|scaffold|render)\s+(?:the\s+|a\s+|an\s+)?(?:new\s+)?(form|page|view|screen|dialog|modal|navbar|menu|sidebar|footer|landing|login|signup|checkout|dashboard|onboarding|wizard)\b/i,
    reason: 'task ships a new user-facing surface -- verify with a headless browser, not just unit tests',
  },
  {
    key: 'systematic-debugging',
    pattern: /\b(fix|debug|reproduce|investigate)\s+(the\s+)?(bug|crash|error|regression|issue|exception)\b|\bstack\s*trace\b/i,
    reason: 'bug-fix / debugging task',
  },
  {
    key: 'comprehensive-code-review',
    pattern: /\b(code\s+review|review\s+the\s+pr|review\s+this\s+pr|audit\s+the\s+code|review\s+the\s+changes)\b/i,
    reason: 'code review task',
  },
  {
    key: 'writing-plans',
    pattern: /\b(decompose|breakdown|sub-?plan|task\s+plan|design\s+the\s+architecture)\b/i,
    reason: 'planning / decomposition task',
  },
];

interface SkillBackfill {
  taskId: string;
  added: Array<{ key: string; reason: string }>;
}

/** Public so approvePlan can surface backfills in the chat / audit trail. */
export function inferMissingSkills(
  title: string,
  description: string | undefined,
  files: string[],
  existing: string[],
): Array<{ key: string; reason: string }> {
  const haystack = [title, description || '', ...files].join(' ');
  const already = new Set(existing);
  const out: Array<{ key: string; reason: string }> = [];
  for (const { key, pattern, reason } of SKILL_TRIGGERS) {
    if (already.has(key)) continue;
    if (pattern.test(haystack)) {
      out.push({ key, reason });
      already.add(key);
    }
  }
  return out;
}

export function extractPlanFromResponse(text: string): PlanExtraction | null {
  const result = extractPlanWithErrors(text);
  return 'tasks' in result ? result : null;
}

/**
 * Strict extraction with structured error reporting. The plan card uses this
 * to surface schema problems to the user instead of silently dropping the
 * bubble back to a regular AI text bubble.
 */
export function extractPlanWithErrors(text: string): PlanExtraction | { error: PlanValidationError } {
  const match = text.match(/```signalos-plan\s*\n([\s\S]*?)```/);
  if (!match) {
    return { error: { kind: 'no_block', details: 'No ```signalos-plan fenced block found in the response.' } };
  }
  const json = match[1].trim();
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch (e) {
    return { error: { kind: 'invalid_json', details: `JSON parse failed: ${(e as Error).message}` } };
  }
  if (!Array.isArray(parsed)) {
    return { error: { kind: 'not_array', details: 'signalos-plan block must contain a JSON array of task objects.' } };
  }

  const issues: string[] = [];
  const tasks: import('../state').PlanTask[] = [];
  const backfills: SkillBackfill[] = [];
  parsed.forEach((raw: unknown, i: number) => {
    if (!raw || typeof raw !== 'object') {
      issues.push(`Task #${i + 1}: not an object`);
      return;
    }
    const t = raw as Record<string, unknown>;
    const taskIssues: string[] = [];
    const title = typeof t.title === 'string' ? t.title.trim() : '';
    if (!title) taskIssues.push('missing title');
    const id = typeof t.id === 'string' && t.id.trim() ? t.id.trim() : `task-${String(i + 1).padStart(3, '0')}`;
    const tier = typeof t.tier === 'string' ? t.tier.toUpperCase() : 'T2';
    if (!VALID_TIERS.has(tier)) taskIssues.push(`invalid tier "${t.tier}" (must be T1|T2|T3)`);
    const status = typeof t.status === 'string' ? t.status : 'pending';
    if (!VALID_STATUSES.has(status)) taskIssues.push(`invalid status "${t.status}"`);
    const effort = typeof t.effort_days === 'number' ? t.effort_days : 0.5;
    if (effort < 0 || effort > 5) taskIssues.push(`effort_days out of range (${effort})`);
    const files = Array.isArray(t.files) ? (t.files as unknown[]).map(String) : [];
    if (files.length === 0) taskIssues.push('no files declared (orchestrator needs file paths to write)');
    for (const f of files) {
      if (typeof f !== 'string' || !f) {
        taskIssues.push(`file entry not a non-empty string`);
        break;
      }
      if (f.includes('..') || f.startsWith('/') || (f.length > 2 && f[1] === ':')) {
        taskIssues.push(`file "${f}" escapes workspace`);
        break;
      }
    }
    // Optional skills array -- ignore unknowns silently (forward-compatible
    // with bundle additions) but reject non-string entries.
    let skills: string[] | undefined;
    if (Array.isArray(t.skills)) {
      const collected: string[] = [];
      for (const s of t.skills as unknown[]) {
        if (typeof s !== 'string') {
          taskIssues.push('skills entry not a string');
          break;
        }
        const key = s.trim().toLowerCase();
        if (key && VALID_SKILL_KEYS.has(key)) collected.push(key);
      }
      if (collected.length > 0) skills = collected;
    }
    if (taskIssues.length > 0) {
      issues.push(`Task ${id} (${title || 'no title'}): ${taskIssues.join('; ')}`);
      return;
    }

    // Heuristic backfill: add skills the AI should have tagged but
    // didn't. Defense in depth on top of the AI's own tagging.
    const description = typeof t.description === 'string' ? t.description : undefined;
    const inferred = inferMissingSkills(title, description, files, skills || []);
    if (inferred.length > 0) {
      const merged = [...(skills || []), ...inferred.map((b) => b.key)];
      skills = merged;
      backfills.push({ taskId: id, added: inferred });
    }

    tasks.push({
      id,
      title,
      description,
      files,
      tier,
      effort_days: effort,
      status,
      skills,
    });
  });

  if (tasks.length === 0) {
    return {
      error: {
        kind: issues.length > 0 ? 'schema_errors' : 'no_valid_tasks',
        details: issues.length > 0 ? 'All tasks failed schema validation.' : 'Empty plan.',
        perTaskIssues: issues,
      },
    };
  }
  return { tasks, rawJson: json, backfills };
}

/**
 * Emit the companion PLAN.md that worktree-manager.sh parses for task
 * IDs. The bash script greps for HTML-comment markers like:
 *   <!-- task: id=<id> tier=<T1|T2|T3> parallel=true -->
 * That's all it actually needs from PLAN.md; the rest is human-readable
 * Markdown for context. Without this file, the with-bash orchestrator
 * path finds zero tasks and runs an empty wave.
 *
 * Co-existence note: PLAN.tasks.yaml stays the source of truth for task
 * data (description, files, skills); PLAN.md is just an index the bash
 * script knows how to read.
 */
export function planToMarkdownTaskList(tasks: import('../state').PlanTask[], wave: string): string {
  const lines: string[] = [];
  lines.push(`# Wave ${wave} — Task Plan`);
  lines.push('');
  lines.push(`> Generated by SignalOS from PLAN.tasks.yaml.`);
  lines.push(`> **Do not edit by hand** — edit PLAN.tasks.yaml instead.`);
  lines.push('');
  lines.push('## Tasks');
  lines.push('');
  for (const t of tasks) {
    const tier = t.tier || 'T2';
    // HTML-comment marker the worktree-manager.sh script parses.
    lines.push(`<!-- task: id=${t.id} tier=${tier} parallel=true -->`);
    lines.push(`### ${t.title}`);
    if (t.description) lines.push('', t.description);
    if (t.files && t.files.length) {
      lines.push('', '**Files:**');
      for (const f of t.files) lines.push(`- \`${f}\``);
    }
    if (t.skills && t.skills.length) {
      lines.push('', `**Skills:** ${t.skills.join(', ')}`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

export function planToYaml(tasks: import('../state').PlanTask[], wave: string): string {
  // Emit PLAN.tasks.yaml content (orchestrator-readable).
  // Schema mirrors the existing plan.py Task class.
  const lines: string[] = [
    `# PLAN.tasks.yaml -- generated by SignalOS chat on ${new Date().toISOString()}`,
    `wave: "${wave}"`,
    `tasks:`,
  ];
  for (const t of tasks) {
    lines.push(`  - id: "${t.id}"`);
    lines.push(`    title: ${JSON.stringify(t.title)}`);
    if (t.description) {
      lines.push(`    description: ${JSON.stringify(t.description)}`);
    }
    if (t.files && t.files.length) {
      lines.push(`    files:`);
      for (const f of t.files) {
        lines.push(`      - ${JSON.stringify(f)}`);
      }
    }
    lines.push(`    tier: ${t.tier || 'T2'}`);
    lines.push(`    effort_days: ${t.effort_days ?? 0.5}`);
    lines.push(`    status: ${t.status || 'pending'}`);
    if (t.skills && t.skills.length) {
      lines.push(`    skills:`);
      for (const s of t.skills) {
        lines.push(`      - ${JSON.stringify(s)}`);
      }
    }
    if (t.previous_failure) {
      lines.push(`    previous_failure: ${JSON.stringify(t.previous_failure)}`);
    }
  }
  return lines.join('\n') + '\n';
}
