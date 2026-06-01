# Foundry Opportunity Validation Lifecycle Plan

## Purpose

Foundry must not move directly from idea to code.

The system must first help the user understand whether the idea is worth
building, how it should be shaped, and what commercial risks exist. A No-Go,
Pivot, or More Discovery outcome is a valid successful outcome when the
evidence does not support development.

This plan adds a full product-management and commercial-validation lane before
the governed build loop.

## Relationship To The V4 Governed Agent Loop

This plan extends `docs/V4_GOVERNED_AGENT_LOOP_PLAN.md`.

The V4 governed agent loop plan covers the engineering delivery gates `G0-G5`.
This opportunity validation plan adds pre-build opportunity gates `O0-O5`.

The full lifecycle is:

```text
O0-O5 opportunity validation -> G0-G5 governed engineering delivery
```

Both plans share the same:

- governed agent loop;
- specialist-agent model;
- governance invariants;
- LiteLLM/provider runtime;
- audit trail;
- evidence model;
- human approval rules;
- no-silent-skip policy;
- Foundry design system outputs.

The opportunity lane decides whether and how the product should be built. The
engineering lane builds only after the opportunity lane produces a signed Go or
approved override decision and a signed PRD baseline.

## Core Principle

Agents analyze, recommend, and prepare artifacts.

The user decides.

Foundry must never silently decide to build, pivot, price, launch, or abandon a
product on behalf of the user. Foundry can recommend a decision and explain the
evidence. The final decision is a signed user choice.

## Consulting-Grade Output Standard

Foundry must generate professional strategy artifacts and presentation decks
with the quality expected from top-tier management consulting work, including
McKinsey, Deloitte, and BCG-style executive communication patterns.

This means consulting-grade structure and clarity, not copying proprietary
templates, brand marks, slide designs, or confidential material.

Required presentation qualities:

- issue-led structure;
- executive summary first;
- clear "so what" slide titles;
- one main message per slide;
- MECE decomposition where appropriate;
- pyramid-principle storyline;
- quantified assumptions and confidence levels;
- evidence-backed recommendations;
- clear risks and tradeoffs;
- source appendix and assumption log;
- decision page with explicit user choices;
- visual consistency with Foundry brand assets.
- mandatory use of the Foundry design system.

Forbidden:

- using McKinsey, Deloitte, BCG, or any third-party logo;
- claiming a firm produced or endorsed the artifact;
- copying proprietary slide templates;
- producing decks or reports in non-Foundry visual styles;
- inventing market data or sources;
- hiding low-confidence assumptions;
- treating an agent recommendation as a user decision.

## Foundry Design System Requirement

Every user-facing output from this lifecycle must use the Foundry design system.

This applies to:

- Markdown reports;
- HTML previews;
- deck outlines;
- PPTX exports;
- PDF exports;
- decision packets;
- opportunity closeouts;
- handoff executive decks.

Approved design system inputs:

| Asset | Source |
|---|---|
| Primary mark | `src/assets/brand/foundry-mark.svg` |
| App icon source | `src/assets/brand/foundry-app-icon.svg` |
| Monochrome mark | `src/assets/brand/foundry-mark-mono.svg` |
| Light wordmark | `src/assets/brand/foundry-wordmark.svg` |
| Dark wordmark | `src/assets/brand/foundry-wordmark-dark.svg` |
| Design tokens | `src/assets/brand/foundry-brand-tokens.json` |
| CSS tokens | `src/assets/brand/foundry-brand.css` |
| Usage guide | `docs/brand/APPROVED_BRAND_ASSETS.md` |

Output styling rules:

- use Foundry paper `#f6f5f2` as the default light background;
- use Foundry ink `#1e1d1a` for primary text and dark hero surfaces;
- use trust blue `#2457d6` for trust, evidence, endorsement, and primary emphasis;
- use signal orange `#d97845` only for active build, decision, or callout signals;
- use `by SignalOS` casing exactly;
- use the approved Foundry mark or wordmark on title/cover pages;
- keep cards at 8px radius or less;
- keep layouts clean, executive, and information-dense;
- avoid decorative gradients, bokeh, or generic AI visuals;
- avoid purple, neon, muddy brown, or unrelated accent palettes;
- every deck/report must include an appendix page or section for sources and assumptions.

Proof rule: if an exported artifact does not consume the Foundry design tokens
or approved brand assets, it is not a valid output.

## Lifecycle Overview

The full idea-to-production lifecycle becomes:

```text
Idea
-> Need Analysis
-> Customer Research
-> Market Analysis
-> Competitive Analysis
-> SWOT/TOWS
-> Business Case
-> Pricing and Monetization
-> Go-To-Market
-> Opportunity Refinement
-> Go/No-Go Gate
-> PRD Baseline
-> UX and Solution Design
-> Technical Architecture
-> Build
-> Verification
-> Security and Compliance
-> Release Readiness
-> Launch or Deploy Package
-> Handoff
-> Measure and Learn
```

User-facing simplified flow:

```text
Discover -> Analyze -> Decide -> Design -> Build -> Validate -> Launch -> Learn
```

## Adaptive Depth

Not every product needs every artifact at the same depth. Foundry must classify
the opportunity and choose the minimum responsible validation depth.

| Product Type | Required Depth |
|---|---|
| Personal/internal utility | Lightweight need, value, risk, and scope analysis |
| Internal enterprise tool | Need analysis, stakeholder map, ROI, workflow risk, adoption plan |
| Commercial SaaS/startup | Full market, customer, competitor, business case, pricing, GTM |
| Regulated product | Full commercial lane plus compliance, privacy, legal, and risk review |
| Existing product improvement | Usage/customer pain, ROI, roadmap fit, competitive impact |
| Prototype/experiment | Lean opportunity brief, experiment design, success criteria |

If the user requests speed, Foundry may reduce depth only by recording explicit
assumptions and risk. Mandatory gates cannot be satisfied by skipped work.

## Expert Agent Team

Foundry assigns specialists automatically. The user does not manage agents.

| Specialist | Responsibility | Primary Artifacts |
|---|---|---|
| Product Strategist | Frames the opportunity and product bet | opportunity brief, decision memo |
| Customer Researcher | Identifies users, jobs, pain, buying triggers | personas, JTBD, need analysis |
| Market Analyst | Sizes market and evaluates segments | TAM/SAM/SOM, segment map |
| Competitive Analyst | Reviews alternatives and differentiation | competitor matrix, advantage analysis |
| Business Case Analyst | Evaluates value, cost, ROI, and risk | business case, sensitivity model |
| Pricing Strategist | Designs monetization and packaging | pricing tiers, willingness-to-pay assumptions |
| GTM Strategist | Defines channels, positioning, launch path | GTM plan, launch plan |
| Risk and Compliance Advisor | Reviews regulatory, legal, privacy, and operational risk | risk register, compliance note |
| Product Designer | Turns refined opportunity into experience direction | journey, IA, prototype brief |
| Solution Architect | Converts approved baseline into technical direction | architecture, stack, data model |
| Governance Lead | Enforces evidence, gates, confidence, and signatures | gate packet, audit, closeout |

## Required Artifacts

Each artifact is generated as structured data plus human-readable output.

Minimum output formats:

- Markdown report;
- JSON or YAML structured artifact;
- presentation-ready slide outline;
- deck export when presentation generation is enabled;
- evidence/source appendix where research claims are made.

### 1. Opportunity Brief

Purpose: capture the idea and initial hypothesis.

Required fields:

- original user idea;
- target user hypothesis;
- problem hypothesis;
- solution hypothesis;
- expected value;
- known constraints;
- unknowns;
- assumptions;
- decision needed.

### 2. Need Analysis

Purpose: decide whether the problem is real and painful enough.

Required fields:

- problem statement;
- affected users;
- current workflow;
- pain severity;
- frequency;
- cost of inaction;
- existing workaround;
- urgency;
- evidence quality;
- unresolved questions.

### 3. Customer and Persona Research

Purpose: define who the product is really for.

Required fields:

- user personas;
- buyer personas, when different from users;
- jobs to be done;
- switching triggers;
- objections;
- buying committee;
- adoption barriers;
- success moments;
- language customers would use.

### 4. Market Analysis

Purpose: determine whether the opportunity is commercially meaningful.

Required fields:

- market definition;
- segment map;
- TAM, SAM, SOM where applicable;
- growth drivers;
- adoption trends;
- market maturity;
- barriers to entry;
- confidence rating;
- sources or assumption labels.

Rules:

- market numbers require sources or must be labeled as assumptions;
- no fake market-share claims;
- no closed-data claims without evidence.

### 5. Competitive Analysis

Purpose: understand the alternative choices available to users.

Required fields:

- direct competitors;
- indirect competitors;
- substitute workflows;
- competitor positioning;
- pricing where known;
- feature comparison;
- strengths and weaknesses;
- customer complaints;
- differentiation gaps.

### 6. Competitive Advantage Analysis

Purpose: explain why this product can win.

Required fields:

- unique advantage hypothesis;
- defensibility;
- speed or cost advantage;
- UX/workflow advantage;
- data advantage;
- distribution advantage;
- compliance/trust advantage;
- niche focus;
- proof required.

### 7. SWOT and TOWS

Purpose: diagnose the opportunity and convert diagnosis into strategy.

SWOT required fields:

- strengths;
- weaknesses;
- opportunities;
- threats;
- impact ranking;
- evidence/confidence per item.

TOWS required fields:

- SO strategies: use strengths to capture opportunities;
- WO strategies: fix weaknesses to capture opportunities;
- ST strategies: use strengths to reduce threats;
- WT strategies: defensive moves to reduce exposure.

Rule: SWOT without TOWS is incomplete. SWOT diagnoses. TOWS turns it into
action.

### 8. Business Case

Purpose: decide whether investment is justified.

Required fields:

- strategic fit;
- expected benefits;
- cost estimate;
- time-to-value;
- implementation complexity;
- risk level;
- ROI hypothesis;
- sensitivity analysis;
- best/base/worst case;
- recommendation.

### 9. Pricing and Monetization

Purpose: define how the product captures value.

Required fields:

- monetization model;
- pricing tiers;
- packaging;
- free trial/freemium decision if applicable;
- value metric;
- competitor pricing comparison;
- cost-to-serve assumptions;
- willingness-to-pay assumptions;
- pricing risks;
- validation experiment.

### 10. Go-To-Market Plan

Purpose: define how users will discover, trust, buy, and adopt the product.

Required fields:

- positioning;
- target segment;
- primary channel;
- secondary channels;
- launch motion;
- sales motion;
- onboarding motion;
- activation metric;
- retention metric;
- launch risks;
- first 30/60/90 day plan.

### 11. Risk Assessment

Purpose: expose what can kill the idea or the launch.

Required fields:

- market risk;
- customer risk;
- competition risk;
- technical risk;
- compliance risk;
- security/privacy risk;
- operational risk;
- financial risk;
- mitigation;
- contingency.

### 12. Opportunity Refinement

Purpose: improve the idea before the user commits to build.

Inputs:

- original idea;
- all discovery artifacts;
- identified gaps;
- user constraints.

Allowed recommendations:

- sharpen target customer;
- narrow or expand scope;
- change positioning;
- change pricing model;
- change GTM channel;
- add missing workflow;
- remove low-value workflow;
- pivot solution;
- run discovery experiment;
- No-Go.

Required output:

- original idea;
- refined idea;
- changes made;
- rationale;
- tradeoffs;
- new risks;
- user approval status.

### 13. Go/No-Go Decision Packet

Purpose: present the decision clearly.

Required sections:

- executive recommendation;
- evidence summary;
- key assumptions;
- unresolved risks;
- financial/business case summary;
- GTM feasibility;
- build feasibility;
- recommendation confidence;
- user decision options.

Allowed user decisions:

- Go;
- Go with changes;
- Run more discovery;
- Pivot;
- No-Go;
- Override and build anyway.

Rules:

- No-Go is a valid successful closeout.
- Override is allowed only with explicit signed risk acceptance.
- Go cannot start build until the PRD baseline is generated and signed.

### 14. PRD Baseline

Purpose: convert the approved opportunity into a buildable baseline.

Required fields:

- approved/refined product idea;
- target users;
- primary workflows;
- MVP scope;
- out-of-scope items;
- acceptance criteria;
- success metrics;
- business constraints;
- GTM constraints;
- compliance/security constraints;
- artifacts feeding the build.

## Presentation Deck Outputs

Foundry must generate consulting-grade presentation decks for the business lane.

Deck types:

| Deck | When Generated | Purpose |
|---|---|---|
| Opportunity Assessment Deck | After market/need/competitive analysis | Decide if the opportunity is real |
| Business Case Deck | Before Go/No-Go | Decide if investment is justified |
| GTM Strategy Deck | Before launch planning | Explain market entry and sales motion |
| Product Strategy Deck | Before PRD baseline | Align product bet and MVP scope |
| Go/No-Go Decision Deck | At decision gate | Support signed user decision |
| Handoff Executive Deck | After build/launch | Summarize product, proof, and next steps |

Deck quality standard:

- 8 to 15 slides for normal opportunities;
- 15 to 25 slides for commercial SaaS or enterprise opportunities;
- executive summary on slide 1 or 2;
- decision slide near the front;
- no wall-of-text slides;
- charts/tables used when they clarify judgment;
- source notes or assumption labels;
- appendix for details;
- consistent Foundry brand identity.
- Foundry design system tokens and approved assets are mandatory.

Mandatory slide patterns:

| Pattern | Use |
|---|---|
| Situation-complication-resolution | Executive storyline |
| Market map | Segment and opportunity explanation |
| Competitor matrix | Competitive comparison |
| 2x2 positioning map | Strategic differentiation |
| Business case summary | Value, cost, ROI, risk |
| Risk heatmap | Risk visibility |
| Pricing architecture | Packaging and monetization |
| GTM funnel | Channel and launch motion |
| Decision page | User choice and recommendation |
| Assumption register | Confidence and unknowns |

## Artifact Storage

Artifacts should live under:

```text
.signalos/product/opportunity/
  ORIGINAL_IDEA.md
  OPPORTUNITY_BRIEF.yaml
  NEED_ANALYSIS.yaml
  CUSTOMER_RESEARCH.yaml
  MARKET_ANALYSIS.yaml
  COMPETITIVE_ANALYSIS.yaml
  COMPETITIVE_ADVANTAGE.yaml
  SWOT_TOWS.yaml
  BUSINESS_CASE.yaml
  PRICING.yaml
  GTM.yaml
  RISK_ASSESSMENT.yaml
  OPPORTUNITY_REFINEMENT.yaml
  GO_NO_GO_PACKET.yaml
  PRD_BASELINE.yaml
  decks/
    opportunity-assessment.md
    business-case.md
    gtm-strategy.md
    go-no-go.md
    handoff-executive.md
  evidence/
    sources.yaml
    assumptions.yaml
    confidence.yaml
```

Deck exports may additionally produce:

```text
.signalos/product/opportunity/decks/export/
  opportunity-assessment.pptx
  business-case.pptx
  gtm-strategy.pptx
  go-no-go.pptx
  handoff-executive.pptx
  *.pdf
```

## Governance Gates

Add pre-build opportunity gates before engineering work.

| Gate | Name | Required Before |
|---|---|---|
| O0 | Idea Intake Signed | Discovery starts |
| O1 | Need and Customer Evidence Reviewed | Market/business work starts |
| O2 | Market and Competitive Evidence Reviewed | Business case starts |
| O3 | Business Case and GTM Reviewed | Go/No-Go decision |
| O4 | Go/No-Go Decision Signed | PRD/design/build |
| O5 | PRD Baseline Signed | Engineering build |

Gate rules:

- agents recommend;
- user signs;
- unsupported claims block gate completion;
- skipped mandatory artifacts become blockers;
- No-Go closes the opportunity with evidence;
- override requires signed risk acceptance.

## User Decision Model

At O4, Foundry presents the recommendation and user choices.

| Decision | Meaning | Next Step |
|---|---|---|
| Go | Evidence supports building | Generate PRD baseline and design |
| Go with changes | Build after applying approved refinements | Update baseline, then design |
| Run more discovery | Evidence is insufficient | Return to relevant analysis phase |
| Pivot | Problem/opportunity exists, but product shape is wrong | Create revised opportunity baseline |
| No-Go | Do not build now | Close with business evidence package |
| Override and build anyway | User accepts weak evidence or known risk | Record override, generate risk-heavy baseline |

## Source and Evidence Rules

No external research claim can appear as fact without evidence.

Every claim must be classified:

| Classification | Meaning |
|---|---|
| Sourced | Backed by source/citation |
| User-provided | Provided by the user |
| Inferred | Derived from available context |
| Assumption | Unverified, needs validation |
| Unknown | Cannot be determined |

Rules:

- use citations for live market/competitor facts when web research is enabled;
- use assumption labels when web research is disabled;
- show confidence levels;
- never invent market share;
- never invent revenue numbers;
- never present estimates as facts;
- keep source appendix in every consulting deck.

## Implementation Workstreams

### W1 - Lifecycle and State

Add opportunity lifecycle states before product delivery:

```text
idea-intake
need-analysis
customer-research
market-analysis
competitive-analysis
business-case
pricing
gtm
opportunity-refinement
go-no-go
prd-baseline
design
build
validate
launch
learn
```

### W2 - Artifact Schemas

Create schemas for every opportunity artifact. Schemas must fail closed when
mandatory decision fields are missing.

### W3 - Specialist Agent Prompts

Create governed specialist prompts for:

- Market Analyst;
- Customer Researcher;
- Competitive Analyst;
- Business Case Analyst;
- Pricing Strategist;
- GTM Strategist;
- Risk and Compliance Advisor;
- Product Strategist.

Each prompt must include:

- role;
- objective;
- required inputs;
- forbidden behavior;
- required output schema;
- confidence rules;
- source rules;
- success criteria.

### W4 - Presentation Generator

Add a deck generation layer that renders:

- Markdown slide outline;
- HTML preview;
- PPTX when local tooling supports it;
- PDF when local tooling supports it.

The first version may generate Markdown and HTML only, but the schemas must be
ready for PPTX export.

All renderers must consume Foundry design tokens from
`src/assets/brand/foundry-brand-tokens.json`. HTML renderers must also consume
or mirror `src/assets/brand/foundry-brand.css`. PPTX/PDF renderers must use the
same token values and approved mark/wordmark assets.

### W5 - Go/No-Go Gate

Add gate UI and signing flow for O4:

- recommendation;
- evidence packet;
- decision choices;
- risk acceptance;
- override handling;
- audit entry;
- downstream lifecycle routing.

### W6 - PRD Baseline Integration

Only after O4 Go or Go-with-changes may Foundry generate the PRD baseline used
by design and build agents.

### W7 - Proof Cages

Add tests that prove:

- No-Go closes cleanly without code generation;
- weak evidence cannot silently pass as Go;
- override is signed and audited;
- PRD cannot be generated before O4;
- build cannot start before O5;
- deck contains decision page and source appendix;
- deck/report uses Foundry design tokens and approved brand assets;
- market facts require source or assumption label;
- user decision is required.

## Proof Scenarios

Add scenarios:

```text
proof/scenarios/260_opportunity_artifact_schemas.sh
proof/scenarios/261_need_market_competitive_analysis.sh
proof/scenarios/262_business_case_pricing_gtm.sh
proof/scenarios/263_swot_tows_refinement.sh
proof/scenarios/264_go_no_go_gate.sh
proof/scenarios/265_no_go_closeout.sh
proof/scenarios/266_override_build_anyway.sh
proof/scenarios/267_consulting_deck_generation.sh
proof/scenarios/268_prd_baseline_before_build.sh
proof/scenarios/269_foundry_design_system_artifacts.sh
```

## Completion Levels

| Level | Capability |
|---|---|
| L0 | Current V4 build loop only |
| L1 | Opportunity artifacts generated from prompt with assumptions |
| L2 | Need, market, competitor, business case, pricing, GTM artifacts |
| L3 | Opportunity refinement creates approved baseline |
| L4 | Go/No-Go gate blocks build until signed |
| L5 | No-Go, Pivot, More Discovery, Override all work |
| L6 | Consulting-grade deck outlines generated |
| L7 | PPTX/PDF exports generated where tooling exists |
| L8 | Web-backed research with citations and confidence |
| L9 | End-to-end idea-to-production with commercial evidence and product proof |

## Definition of Done

This lane is complete when:

- all required opportunity artifacts have schemas;
- specialist agents generate those artifacts through governed packets;
- every market/commercial claim has source, user-provided, inferred,
  assumption, or unknown classification;
- SWOT includes TOWS strategy;
- pricing and GTM exist for commercial products;
- business case exists before Go/No-Go;
- Go/No-Go gate is signed by the user;
- No-Go is a valid closeout outcome;
- override-and-build is signed and audited;
- PRD baseline cannot exist before the decision gate;
- build cannot start before PRD baseline approval;
- consulting-grade decks are generated with executive summary, decision page,
  source appendix, and assumption register;
- all generated decks/reports use Foundry design tokens and approved brand
  assets;
- all proof scenarios pass.
