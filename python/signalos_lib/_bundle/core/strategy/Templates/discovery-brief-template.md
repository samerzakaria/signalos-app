<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Discovery Brief
# 
# Complete within 30 minutes of any client session.

---

**Wave:** {N}  
**Session date:** {YYYY-MM-DD}  
**Session type:** Demo / Planning / Check-in / Crisis / Kick-off  
**Attendees:** {names and roles}  
**Duration:** {minutes}  
**Raw transcript archive:** `Governance/conversations/wave-{N}-session-{S}-{tag}.md` *(verbatim record; this Brief is the distillation)*

---

## Field 1 — What changed in the client's mind?

*What did the client believe before this session that they no longer believe, or now believe differently?*

> {Write in the client's voice if possible. Be specific. "They want the dashboard faster" is not enough. "They now believe the dashboard needs to show data from the last 7 days by default — they assumed 30 days was our default and were surprised" is useful.}

---

## Field 2 — What surprised you?

*What happened in this session that you did not expect?*

> {This is the highest-signal field. If nothing surprised you, you were not paying close enough attention. Think harder. Even a small surprise — a hesitation, an unusual question, an offhand remark — belongs here.}

---

## Field 3 — Open assumptions exposed

*What assumptions did either side hold that were made explicit for the first time?*

| Assumption | Held by | Now resolved as | Action needed |
|-----------|---------|-----------------|---------------|
| {e.g. "Data exports would be CSV"} | Client | {e.g. "Client actually needs PDF"} | {Add to backlog as raw CR} |
| {next assumption} | Dev | {resolution} | {action} |

---

## Field 4 — Decisions taken

*What was explicitly decided in this session? (Not implied — explicitly stated and agreed.)*

| Decision | Owner | Conditions / caveats |
|----------|-------|----------------------|
| {e.g. "Launch date moved to May 15"} | {Client — PM} | {"If auth feature is complete"} |
| {next decision} | {owner} | {conditions} |

→ Each decision here must be added to `governance/DECISION-DNA.md` before next Pre-Wave.

---

## Field 5 — Signal to watch

*Based on what you learned in this session, what one thing should you monitor after next ship?*

> {e.g. "Client was uncertain whether their users would use the bulk export feature. Watch: export button click rate in first 72h. If <5 uses, feature is low-value and we should deprioritise the planned v2 enhancements."}

Metric: {specific event or number}  
Threshold: {what counts as confirmed vs refuted}  
Window: {how long to observe}

---

## Expectation Map update needed?

- [ ] YES — update `governance/plans/wave-{N}-expectation-map.md` before next Pre-Wave
  - What changed: {brief description}
- [ ] NO — no changes to Expectation Map required

## Backlog items triggered by this session

| Item | Type | Blast Radius | Action |
|------|------|-------------|--------|
| {e.g. "Add PDF export"} | New Requirement | {Contained} | Add to BACKLOG.yaml as raw |
| {next item} | {type} | {radius} | {action} |

---

*Brief completed by:* _______________  
*Time to complete:* ___ minutes
