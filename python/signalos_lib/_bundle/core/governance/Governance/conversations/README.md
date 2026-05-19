<!-- SignalOS v1.0 — Locked 2026-04-16 -->

# Conversation Archive

`Folder: Governance/conversations/ · Authored by: PO (primary) + any agent that receives a client/stakeholder transcript · Cadence: one file per session · Naming: wave-{N}-session-{S}-{short-tag}.md`

> The **Conversation Archive** holds raw, verbatim transcripts of client and stakeholder sessions. It sits beneath the Client Signal Log: SIG entries quote short fragments; this folder stores the full transcript the quotes came from.

---

## Why a separate archive from the Client Signal Log

- `CLIENT-SIGNAL-LOG.md` is the **distilled receipt** — one SIG entry per meaningful signal, cross-referenced to CRs and Expectation-Map redlines.
- `Governance/conversations/` is the **raw record** — full transcripts, recordings-to-text, email threads, Slack exports. When the SIG quotes "we need export to CSV", the Archive carries the 45-minute call the quote came from.

The Signal Log is what the squad reads before a Wave; the Archive is what's retrieved during audits, doctrinal disputes, or when a Belief needs re-interrogation against original source material.

---

## File naming

```
wave-00-session-01-kickoff.md
wave-01-session-01-discovery.md
wave-01-session-02-expectation-map-review.md
wave-03-session-01-post-ship-feedback.md
```

- `wave-00` = pre-Wave (onboarding, discovery, kickoff)
- `session-{S}` = session index within that Wave (zero-padded to 2 digits)
- `{short-tag}` = slug of the session's purpose

---

## File shape (contract)

Every transcript file MUST follow this structure:

```
# Session — {date} — {short tag}

## Metadata
- Wave: Wave {N}
- Date: YYYY-MM-DD HH:MM
- Duration: {minutes}
- Participants: {names + roles}
- Channel: call / meeting / email thread / Slack / in-person
- Recorded: yes / no / partial
- Source: {Zoom recording URL, or "live notes by {name}"}

## Verbatim transcript
{full transcript, quoted or transcribed — no paraphrase}

## Signals extracted
- SIG-{NNNN}: {link to Client Signal Log entry}
- SIG-{NNNN}: …

## Redlines surfaced
- Wave-{N}-expectation-map row {#}: {what changed}

## Unresolved questions
- {question the client raised we did not answer in-session}

## Archive integrity
- Transcribed by: {person or agent}
- Reviewed by: {PO}
- Redactions: {none, or "PII names removed, see Governance/incidents/…"}
```

---

## Redactions

If the transcript contains PII or legally-sensitive content, redact in-place and record the redaction under **Archive integrity**. Never delete raw content without a corresponding incident file explaining why.

---

## Retention

Conversation transcripts follow the product's retention policy (defined in the Soul Document). Default: retain for the life of the product. A client who asks for their data deleted triggers an incident + PE-reviewed redaction — not a silent file deletion.
