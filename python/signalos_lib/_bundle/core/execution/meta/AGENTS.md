<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# SignalOS — Agent Contribution & Extension Guidelines

`Canonical path: core/execution/meta/AGENTS.md · Maintained by: PE`

---

## Extending the agent swarm

SignalOS ships with 10 agent seats (see `core/execution/agents/README.md` for the full roster). If your team needs an additional agent seat:

1. **Justify the seat.** A new agent must fill a gap that no existing seat covers. Document why an existing agent cannot absorb the responsibility.
2. **Follow the file shape.** Every agent prompt file must match the contract defined in `Agents/README.md`: Purpose, Activates-at, Prerequisites, Inputs, Outputs, Refusal conditions, Handoff, Trust Tier ceiling.
3. **Assign a human owner.** Law 4: no agent without a named human owner. The new seat must map to one of the four human roles (PO, PE, QA, DevOps).
4. **Register in the Charter.** Add the seat to `docs/Team-Charters/AGENTIC_TEAM_CHARTER.md` before the agent file is considered active.
5. **Update the agent count** in `docs/CHANGELOG.md`, `docs/MANIFEST.md`, and `docs/SIGNALOS_REFERENCE.md`.

---

## Modifying existing agents

Agent prompt files are operational contracts — they shape agent behavior at activation time. Changes to agent prompts must be:

- Reviewed by the PE (prompt owner) and QA (behavioral validation).
- Tested against at least one full Wave cycle before merging.
- Documented as a Constitution delta in the Wave debrief.

Do not change refusal conditions or trust-tier ceilings without PO approval.

---

## Agent vs Skill vs Command

- **Agent** — a role with a system prompt, activation gate, inputs/outputs, and handoff. One per seat.
- **Skill** — a reusable capability invoked by name during a session. Lives in `core/execution/skills/`.
- **Command** — a slash-command that triggers a specific workflow. Lives in `core/execution/commands/`.

If your contribution is a reusable capability (not a full role with gates and handoff), it belongs as a Skill, not an Agent.
