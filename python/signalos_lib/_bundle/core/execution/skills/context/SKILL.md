---
name: context
description: "Gathers and synthesizes context for the current task from available sources."
---

<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# ─── CONTEXT BUDGET ZONES (copy this block into Soul Document header) ────────
#
# 
# Source: FlorianBruniaux/claude-code-ultimate-guide
#
# At the start of every session, confirm your context budget zone before coding.
# Check zone before picking up each new ticket.
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  ZONE     │  CONTEXT %  │  ACTION                                      │
# ├─────────────────────────────────────────────────────────────────────────┤
# │  🟢 Green  │  0 – 60%    │  Work freely. No action needed.             │
# │  🟡 Yellow │  60 – 80%   │  Run /compact BEFORE next ticket.           │
# │            │             │  Summarise: what was built, key decisions.  │
# │  🔴 Red    │  80% +      │  HARD STOP.                                 │
# │            │             │  1. Log current state to Decision DNA        │
# │            │             │  2. Note next ticket in Soul Document        │
# │            │             │  3. Start a fresh session                   │
# │            │             │  4. Load Soul Document first                │
# │            │             │  5. Verify first output before committing   │
# └─────────────────────────────────────────────────────────────────────────┘
#
# Degradation signals to watch for (even in Yellow zone):
# - Forgetting project-specific naming conventions
# - Repeating a decision already made this session
# - Producing code that contradicts Constitution rules
# - Losing track of which acceptance criteria are still pending
#
# If you notice any degradation signal: treat as Red regardless of % shown.
# ─────────────────────────────────────────────────────────────────────────────
