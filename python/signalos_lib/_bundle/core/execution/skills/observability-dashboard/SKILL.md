---
name: observability-dashboard
description: "Render deterministic static observability dashboards from session metrics sidecars."
---

<!-- Concept adapted from a5c-ai/babysitter (MIT). No source code copied. -->
<!-- SignalOS Core v1.1 — observability-dashboard skill (T1 per core/TRUST_TIER.md). -->

# Skill: observability-dashboard

`Canonical path: core/execution/skills/observability-dashboard/SKILL.md · Trust Tier: T1 (documentation only) · Wave: W1.1 · Amendment: AMD-CORE-003`

Render a static HTML snapshot of the metrics sidecar. No JavaScript, no CDN,
no external libraries. The dashboard reads
`.signalos/sessions/<sid>/metrics.jsonl` files (written by
`core/execution/hooks/_lib/metrics-append.sh`) and the session
`INDEX.jsonl`, then emits one self-contained HTML document with inline CSS
and inline SVG charts.

---

## When to regenerate

Regenerate the snapshot **after any of**:

1. A Wave closes (`signal-ship` rolls metrics into
   `core/governance/Retro/waves/<wave-id>/METRICS.md`). The
   dashboard snapshot is the human-readable complement.
2. An operator wants a git-tracked view of the last 20 sessions (e.g. for
   the Gate 4 review packet).
3. `signal-observe` flags an anomaly and a reviewer wants the per-session
   step timeline.
4. CI wants a reproducibility check — render twice, assert byte-identical
   output (already covered by `proof/scenarios/26_dashboard_renders.sh`).

Do **not** regenerate mid-session. The dashboard is a read-only projection
of the metrics sidecar and is never part of the hot path.

Command:

```bash
python3 core/observability/render-dashboard.py \
  --output core/observability/dashboard.html
```

Flags:

- `--root <dir>`: repo root (contains `.signalos/`). Defaults to cwd.
- `--output <path>`: write HTML to path. If omitted, HTML is written to
  stdout.

---

## How to extend a chart without adding JS

All charts are inline SVG composed of `<rect>`, `<text>`, and `<title>`
elements emitted by Python string concatenation in `render-dashboard.py`.
The extension contract is:

1. **No `<script>`, no `onclick`, no `fetch`, no external URLs.** The
   dashboard must render on a disconnected laptop. CI scenario 26 greps
   the output for forbidden constructs.
2. **Widths must be derived from already-aggregated scalars** (row counts,
   total durations). No new full-file scans — the renderer streams each
   `metrics.jsonl` once and accumulates per-session aggregates.
3. **Add a new chart function** next to `render_cost_bar_svg` and
   `render_session_timeline_svg`. It takes the same `list[SessionAgg]`
   and returns an SVG string. Keep `viewBox` integer-only so the output
   is deterministic across platforms.
4. **Determinism test**: the renderer must produce byte-identical output
   on two runs over the same input. Avoid `time.time()`, avoid
   `hash()`-ordered iteration (dicts in 3.11+ preserve insertion order —
   rely on that and on the `sorted()` calls already in place).

### Worked example — add an "errors per session" chart

```python
def render_error_bar_svg(sessions: list[SessionAgg]) -> str:
    max_err = max((s.error_count for s in sessions), default=0) or 1
    width, row_h = 600, 22
    height = row_h * len(sessions) + 8
    parts = [f'<svg viewBox="0 0 {width} {height}" width="{width}" '
             f'height="{height}" xmlns="http://www.w3.org/2000/svg">']
    for i, s in enumerate(sessions):
        y = 4 + i * row_h
        w = int((s.error_count / max_err) * 380)
        parts.append(
            f'<text x="0" y="{y+14}" font-family="monospace" font-size="11" '
            f'fill="#111827">{_esc(s.session_id[:18])}</text>'
            f'<rect x="180" y="{y+2}" width="{w}" height="16" fill="#dc2626"/>'
            f'<text x="{185+w}" y="{y+14}" font-family="monospace" '
            f'font-size="11" fill="#374151">{s.error_count}</text>'
        )
    parts.append('</svg>')
    return "".join(parts)
```

Wire it into `render_html` next to the cost chart. Run scenario 26. If the
byte-identical assertion fails, check for a stray dict iteration or a
floating-point value that is printed with uncontrolled precision.

---

## The redaction contract (no prompt bodies in metrics)

**Metric rows never carry prompt or response bodies.** This is enforced at
two points:

1. **Write side** — `core/execution/hooks/_lib/metrics-append.sh` rejects
   any row with a field outside the allowlist:

   ```
   ts, schema_version, session_id, step_id, hook, tool, duration_ms,
   tokens_in, tokens_out, cost_usd, wave_id, phase, actor, subagent_count
   ```

   Plus it pipes every row through `core/execution/hooks/_lib/redact.py
   --filter` — the SAME filter used by `journal-append.sh`, never forked.
2. **Read side** — `render-dashboard.py` re-checks the allowlist before
   aggregating. A row containing any unknown field is **dropped** from the
   aggregation and counted as `schema_rejected`. The rendered footer
   surfaces that count so a rogue writer cannot hide behind a green
   render. String values longer than 256 chars in a known field are also
   rejected — this catches the "stuff a prompt into `phase`" attack.

A failing redaction test is a Gate 4 blocker (see
`proof/scenarios/27_metrics_redacted.sh`).

---

## Reading raw metrics with `jq`

The sidecar is plain JSONL; any jq recipe works. Common one-liners:

```bash
# Total cost across every session
find .signalos/sessions -name metrics.jsonl \
  -exec cat {} + | jq -s 'map(.cost_usd // 0) | add'

# Top 10 slowest steps
find .signalos/sessions -name metrics.jsonl \
  -exec cat {} + | jq -c '{step_id, duration_ms}' \
  | jq -s 'sort_by(-.duration_ms) | .[0:10]'

# Error rate per session
find .signalos/sessions -name metrics.jsonl | while read f; do
  jq -s --arg f "$f" '
    {session: $f,
     n: length,
     errors: map(select(.hook // .tool | test("-failed$|^step-failed$"))) | length}
  ' "$f"
done

# Verify no row leaked an unknown field
find .signalos/sessions -name metrics.jsonl -exec cat {} + \
  | jq -r 'keys[]' | sort -u
# (compare against the allowlist above; any extra key is a bug.)
```

---

## `signal-observe` integration

`signal-observe` consumes `metrics.jsonl` as its canonical input and
renders a terminal summary. The dashboard is the HTML complement:

- `signal-observe` → fast text summary for the current terminal session.
- `render-dashboard.py` → git-trackable HTML for review packets and Wave
  close-outs.

Both share the same read-side rule: any row outside the allowlist is
ignored and tallied as `schema_rejected`. Keep them in lockstep — if you
add a field to the allowlist, update both sides in the same PR.

---

## Trust Tier footnote

This skill is **T1** — documentation only. The metrics sidecar writer it
documents is **T3** (same audit posture as the journal). A demotion of the
writer would defeat the redaction contract above and is refused by
`proof/scenarios/04_tier_demotion_refused.sh`.
