/**
 * chatResponseGuard.ts -- Milestone 2-a: scan LLM-generated chat output for
 * secrets and dangerous patterns before it's rendered in the user's chat
 * bubble.
 *
 * The IPC layer already redacts secrets on the *command-output* path via
 * python/signalos_secret_guard.py. Streaming LLM replies (provider chat
 * stream) bypass that path entirely -- they flow Rust provider -> chat:token
 * events -> JS accumulator -> bubble. This module is the missing client-side
 * guard for that path.
 *
 * Rule set mirrors python/signalos_lib/_bundle/core/execution/hooks/_lib/redact.py
 * (the canonical SignalOS Core redaction policy):
 *   - AWS access key shape       AKIA[0-9A-Z]{16}            -> redact
 *   - GitHub PAT shape           ghp_[A-Za-z0-9]{36}         -> redact
 *   - Generic key/secret literal (api_key|password|...)='..' -> redact
 *   - Dangerous bash snippets    rm -rf /, curl|sh, fork bomb -> redact
 *   - Hallucinated absolute paths /, C:\, /etc/passwd, ~/.ssh -> flag only
 *
 * "Flag only" paths are kept in the rendered text -- the user might
 * legitimately need to discuss /etc/passwd or ~/.ssh/config. We just want
 * the audit trail to know the model produced them, so a reviewer can spot
 * a hallucinated path-injection later.
 */

export type RedactionKind = 'secret' | 'dangerous-bash' | 'hallucinated-path';

export interface Redaction {
  kind: RedactionKind;
  original: string;
  replacement: string;
  reason: string;
}

export interface ScanResult {
  clean: string;
  redactions: Redaction[];
}

// --------------------------------------------------------------------------
// Patterns -- kept as module-level constants so the regex caches are reused
// across the (potentially many) chat scans a single session performs.
// --------------------------------------------------------------------------

// Secrets -- these are replaced in `clean` with [REDACTED:secret].
const SECRET_RULES: { name: string; pattern: RegExp; reason: string }[] = [
  {
    name: 'aws-access-key',
    // \b doesn't work well here because AKIA is followed by uppercase
    // alphanumerics, but we still want word-boundary semantics. Use a
    // negative lookbehind/lookahead on word chars.
    pattern: /\bAKIA[0-9A-Z]{16}\b/g,
    reason: 'AWS access key id shape (AKIA + 16 uppercase alnum)',
  },
  {
    name: 'github-pat',
    pattern: /\bghp_[A-Za-z0-9]{36}\b/g,
    reason: 'GitHub personal access token shape (ghp_ + 36 alnum)',
  },
  {
    name: 'generic-secret-literal',
    // Matches `api_key = "xxxxxxxxxxxxxxxx"`, `password: 'hunter2hunter2'`,
    // `token = "abcdef0123456789..."`, etc. Requires at least 16 chars of
    // value so we don't flag every `password = "ok"` example.
    //
    // NOTE: we intentionally don't use the `m` flag because the secret
    // assignment must be on one line -- multi-line capture would over-match
    // markdown code blocks that happen to mention "token:".
    pattern: /(?:api[_-]?key|password|token|secret)\s*[:=]\s*['"][^'"]{16,}['"]/gi,
    reason: 'Generic api_key/password/token/secret literal with long value',
  },
];

// Dangerous bash snippets -- redacted (replaced with [REDACTED:dangerous-bash]).
// We scan unconditionally rather than only inside fenced code blocks: a user
// reading `... run: rm -rf / ...` in chat may still copy-paste the snippet,
// and an LLM that emits the snippet at all is more interesting than the
// surrounding context. False-positive rate on this rule set is near zero
// (no benign chat mentions of `:(){:|:&};:`).
const DANGEROUS_BASH_RULES: { name: string; pattern: RegExp; reason: string }[] = [
  {
    name: 'rm-rf-root',
    // `rm -rf /` and `rm -rf /*` and `rm -rf / --no-preserve-root`.
    // Match `rm` (any flags including -r/-f/-rf/-fr) followed by `/`
    // as a path argument. The trailing boundary is whitespace, end of
    // string, or another shell metachar.
    pattern: /\brm\s+(?:-[a-zA-Z]+\s+)*(?:-[rRfF]+\s+)?\/(?:\s|$|[*\\])/g,
    reason: 'rm -rf / (filesystem nuke)',
  },
  {
    name: 'curl-pipe-sh',
    // curl ... | sh, curl ... | bash, curl ... | zsh
    pattern: /\bcurl\s+[^\n|]+\|\s*(?:sh|bash|zsh|ksh)\b/g,
    reason: 'curl <url> | sh pattern (remote code execution)',
  },
  {
    name: 'wget-pipe-bash',
    pattern: /\bwget\s+[^\n|]+\|\s*(?:sh|bash|zsh|ksh)\b/g,
    reason: 'wget <url> | bash pattern (remote code execution)',
  },
  {
    name: 'fork-bomb',
    // The classic :(){ :|:& };: fork bomb. Be permissive about whitespace.
    pattern: /:\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:/g,
    reason: 'Classic bash fork bomb',
  },
];

// Hallucinated/sensitive paths -- FLAGGED ONLY, not redacted. The user might
// genuinely need to discuss /etc/passwd; we just want the audit trail to
// know the model produced the reference.
const FLAG_PATH_RULES: { name: string; pattern: RegExp; reason: string }[] = [
  {
    name: 'unix-system-secret',
    // The exact files most often leaked by path-traversal bugs.
    pattern: /\/etc\/(?:passwd|shadow|sudoers)\b/g,
    reason: 'Reference to a unix system credential file',
  },
  {
    name: 'ssh-key-dir',
    pattern: /~\/\.ssh\/[A-Za-z0-9_.\-/]*/g,
    reason: 'Reference to the user SSH key directory',
  },
  {
    name: 'absolute-unix-path',
    // Any other absolute unix path (length >= 4 to avoid catching just "/" or "/a").
    // We exclude the patterns already matched above so we don't double-report.
    // We also exclude code-fence delimiters and URL slashes.
    //
    // Heuristic: an absolute path starts at a non-word, non-`/` character (or
    // start-of-string) and is a sequence of `/segment` with at least one
    // segment of 2+ chars.
    pattern: /(?:^|[\s(])(\/(?!\/)[A-Za-z0-9_.\-]+(?:\/[A-Za-z0-9_.\-]+)+)/g,
    reason: 'Absolute unix path the model invented (verify before trusting)',
  },
  {
    name: 'absolute-windows-path',
    // C:\Users\... or D:\foo\... or any drive letter.
    pattern: /\b[A-Za-z]:\\(?:[A-Za-z0-9_.\- ]+\\)*[A-Za-z0-9_.\- ]*/g,
    reason: 'Absolute windows path the model invented (verify before trusting)',
  },
];

// --------------------------------------------------------------------------
// Public API
// --------------------------------------------------------------------------

/**
 * Scan an LLM chat response. Returns a redacted `clean` string for rendering
 * plus a list of `redactions` describing every rule that fired.
 *
 * Order of operations matters:
 *   1. Secrets -- redact unconditionally (anywhere in the text).
 *   2. Dangerous bash -- redact only inside fenced code blocks.
 *   3. Hallucinated paths -- flag only, don't modify `clean`.
 *
 * Secrets are matched on the ORIGINAL text and replaced in `clean` so that
 * a long token containing a substring that also matches another rule isn't
 * partially-redacted twice.
 */
export function scanChatResponse(text: string): ScanResult {
  if (!text) return { clean: text, redactions: [] };

  const redactions: Redaction[] = [];
  let clean = text;

  // -- 1. Secrets --------------------------------------------------------
  for (const rule of SECRET_RULES) {
    rule.pattern.lastIndex = 0;
    const matches = Array.from(text.matchAll(rule.pattern));
    for (const m of matches) {
      const original = m[0];
      const replacement = '[REDACTED:secret]';
      redactions.push({
        kind: 'secret',
        original,
        replacement,
        reason: `${rule.name}: ${rule.reason}`,
      });
      // Replace ALL occurrences of this exact original string in `clean`
      // (a token might appear more than once). split/join is O(n) and
      // avoids the regex-vs-literal-string escaping headache.
      clean = clean.split(original).join(replacement);
    }
  }

  // -- 2. Dangerous bash --------------------------------------------------
  // Scan the already-cleaned text so we don't re-find snippets that were
  // inside a redacted secret block. (Belt + braces -- in practice the two
  // pattern sets don't overlap.)
  for (const rule of DANGEROUS_BASH_RULES) {
    rule.pattern.lastIndex = 0;
    const matches = Array.from(clean.matchAll(rule.pattern));
    for (const m of matches) {
      const original = m[0];
      const replacement = '[REDACTED:dangerous-bash]';
      redactions.push({
        kind: 'dangerous-bash',
        original,
        replacement,
        reason: `${rule.name}: ${rule.reason}`,
      });
      clean = clean.split(original).join(replacement);
    }
  }

  // -- 3. Hallucinated paths (flag only, do not touch `clean`) -----------
  // We re-scan against the (post-redaction) `clean` so paths inside a
  // redacted block don't get flagged.
  const seenPaths = new Set<string>();
  for (const rule of FLAG_PATH_RULES) {
    rule.pattern.lastIndex = 0;
    const matches = Array.from(clean.matchAll(rule.pattern));
    for (const m of matches) {
      // For the absolute-unix-path rule we used a capture group to skip
      // the leading whitespace/(.
      const found = (m[1] || m[0]).trim();
      if (!found) continue;
      if (seenPaths.has(found)) continue;
      seenPaths.add(found);
      redactions.push({
        kind: 'hallucinated-path',
        original: found,
        replacement: found, // unchanged
        reason: `${rule.name}: ${rule.reason}`,
      });
    }
  }

  return { clean, redactions };
}

/**
 * Convenience: tally redactions by kind for the UI's system-bubble summary.
 * "Filtered: 1 secret, 0 dangerous bash, 1 flagged path."
 */
export function summariseRedactions(reds: Redaction[]): {
  secret: number;
  dangerousBash: number;
  hallucinatedPath: number;
} {
  let secret = 0;
  let dangerousBash = 0;
  let hallucinatedPath = 0;
  for (const r of reds) {
    if (r.kind === 'secret') secret += 1;
    else if (r.kind === 'dangerous-bash') dangerousBash += 1;
    else if (r.kind === 'hallucinated-path') hallucinatedPath += 1;
  }
  return { secret, dangerousBash, hallucinatedPath };
}
