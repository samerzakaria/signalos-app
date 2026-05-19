<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Expectation Map — Wave {N}

`Canonical path per Wave: core/strategy/EXPECTATION_MAP.md (current) or core/strategy/expectation-maps/wave-{N}-expectation-map.md (archived) · Authored by: PO · Signed at: Gate 2 (PO + client)`

> Two columns on one page. The client signs it. Any row the client redlines is a **surfaced risk before a line of code is written** — that is the point.

---

## Front-matter

```yaml
wave: {N}
belief_path: core/strategy/BELIEF.md
author: {PO name}
date: YYYY-MM-DD
client_signer: {name · company · role}
```

---

## The two-column map

| # | What the client expects | What we are actually building |
|---|---|---|
| 1 | {Client's language, in full sentences. If they said "users can share reports by email" that's exactly what goes here.} | {Our precise build language: "a share button that copies a signed URL to clipboard"} |
| 2 | {next expectation} | {our build} |
| 3 | | |

---

## Redlines surfaced during signature

*Rows the client challenged, what they challenged, and how it was resolved.*

| Row | What the client objected to | How it was resolved |
|---|---|---|
| {#} | {quote the objection} | {A: expectation changed (row updated above), or B: build changed (row updated above), or C: deferred to Wave N+1 with PO note} |

*(If no redlines: state explicitly "No redlines surfaced." If no redlines and the client signs instantly, PO must ask two clarifying questions before accepting. A friction-free Expectation Map is a red flag.)*

---

## Out of scope (explicit exclusions)

*What the client mentioned but which this Wave will not deliver. Named here to prevent silent scope creep.*

- {exclusion}
- {exclusion}

---

## Platform + device scope

| Surface | In scope | Notes |
|---|---|---|
| Desktop web | ✅ / ❌ | |
| Mobile web | ✅ / ❌ | |
| Native iOS | ✅ / ❌ | |
| Native Android | ✅ / ❌ | |

---

## Accessibility floor

*Any accessibility requirement that applies to this Wave (WCAG AA, keyboard-only flows, screen-reader reads, contrast).*

- {requirement}

---

## Gate 2 signatures

**I confirm the above accurately represents what I have asked for and what the team will build. Rows I redlined have been resolved. Exclusions are accepted.**

Signed (client): __________  *Name · Role · Date: __________*  
Signed (PO): __________  *Date: __________*

*(Gate 2 is not satisfied without both signatures. An unsigned Expectation Map blocks `/signal-plan`.)*
