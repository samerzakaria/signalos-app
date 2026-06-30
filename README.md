# SignalOS

SignalOS is an agentic software house packaged as a desktop application. You describe what you want built; SignalOS assigns agents, enforces governance at every gate, and delivers a running, tested, governed product. Agents write the code. SignalOS runs the house.

**Version: v3.1.0-internal**

## How It Works

A product delivery follows this pipeline:

```
Client prompt -> Intent -> Design -> Acceptance -> Agent builds -> Validation -> Security -> Proof -> Closeout
```

The desktop UI presents a 5-step wizard (Intent, Design, Build, Validate, Deliver). The user reviews and approves at every gate using one of five verdicts: approve, request changes, reject, waive, or approve with conditions. Bounded rework loops prevent infinite cycles when changes are requested.

## What Gets Delivered

- Real project scaffold (package.json, Vite config, tsconfig, entry points)
- Agent-generated product code (components, tests, application logic)
- Design system (UI library, tokens, layouts)
- Acceptance matrix linking every output file to acceptance criteria
- Security scan (injection detection, PII check, compliance rules)
- Runtime proof (dev server starts, page renders successfully)
- Honest closeout (evidence-based summary; never claims what is not proven)

## Governance

SignalOS enforces its way of working at build time and runtime:

- **Constitution** -- supreme governing document for all agent behavior
- **Trust tiers** -- T1 advisory, T2 review-required, T3 autonomous
- **Gates** -- G0 through G5, each requiring a human signature to proceed
- **Governance library** -- 400+ bundled governance files (425 at time of writing); the orchestrator selects the relevant subset per agent role
- **Runtime enforcement** -- 12 rules, strict mode, fails closed
- **Privacy** -- GDPR Article 15 export and Article 17 erasure support
- **Threat modeling** -- OWASP/STRIDE methodology applied during security gate
- **Audit trail** -- append-only, tamper-evident log of all decisions

## Architecture

```
signalos-app/
|-- src/                                  Preact frontend
|   |-- components/
|   |   |-- views/
|   |   |   |-- DeliverView.tsx           5-step product delivery wizard
|   |   |   |-- BuildView.tsx             Build progress and output
|   |   |   |-- DashboardView.tsx         Project overview
|   |   |   |-- VelocityPanel.tsx         Delivery metrics
|   |   |   |-- VaultView.tsx             Secret management
|   |   |   |-- PreviewView.tsx           Live preview of generated product
|   |   |   |-- SettingsView.tsx          App configuration
|   |   |   `-- TerminalView.tsx          Terminal output
|   |   |-- ProgressDetail.tsx            Gate progress breakdown
|   |   |-- TestDebtPanel.tsx             Test coverage tracking
|   |   |-- GateTimeline.tsx              Gate signing timeline
|   |   `-- Sidebar.tsx / Titlebar.tsx    Shell chrome
|   `-- js/
|       `-- ipc.js                        Tauri IPC wrapper
|-- src-tauri/                            Rust backend (Tauri 2)
|   `-- src/
|       |-- main.rs                       Entry point and command registration
|       |-- ipc.rs                        IPC commands (project, delivery, gates)
|       |-- governance.rs                 Governance rule evaluation
|       |-- enforcement.rs                Runtime enforcement engine
|       |-- keychain.rs                   OS credential storage
|       |-- sandbox.rs                    Agent sandboxing
|       |-- runtime.rs                    Runtime management
|       |-- sidecar.rs                    Python sidecar lifecycle
|       |-- test_automation.rs            Test-automation bridge
|       `-- provider.rs                   AI provider integration
|-- python/
|   |-- signalos_lib/
|   |   |-- product/                      Delivery bridge modules
|   |   |   |-- intent.py                 Client intent extraction
|   |   |   |-- design.py                 Design generation
|   |   |   |-- scaffold.py               Project scaffolding
|   |   |   |-- generation.py             Agent code generation
|   |   |   |-- validation.py             Output validation
|   |   |   |-- security_gate.py          Security scanning
|   |   |   |-- proof.py                  Runtime proof collection
|   |   |   |-- deploy.py                 Deployment packaging
|   |   |   |-- closeout.py               Evidence-based closeout
|   |   |   |-- acceptance.py             Acceptance criteria matrix
|   |   |   |-- agent_dispatch.py         Agent assignment and dispatch
|   |   |   |-- gate_review.py            Gate verdict handling
|   |   |   |-- questions.py              Client clarification questions
|   |   |   |-- repair_loop.py            Bounded rework loops
|   |   |   |-- lifecycle.py              Delivery lifecycle orchestration
|   |   |   `-- blueprints/               Product type blueprints
|   |   `-- _bundle/                      Governance library
|   |       `-- core/
|   |           |-- governance/            Constitution, rules, policies
|   |           |-- execution/             Agent execution framework
|   |           |-- strategy/              Planning and sequencing
|   |           |-- registry/              Agent and skill registry
|   |           |-- observability/         Logging and audit
|   |           `-- tool-adapters/         External tool integrations
|   `-- signalos_ipc_server.py            IPC server entry point
|-- .github/workflows/
|   |-- pages.yml                         GitHub Pages deployment
|   |-- smoke.yml                         Build smoke tests
|   |-- test-automation.yml               L0 through L3 test levels
|   |-- release.yml                       Release pipeline
|   `-- windows-installer.yml             Installer build and validation
|-- distribution/                         Landing page and updater manifests
`-- scripts/                              Build, bundle, and release tooling
```

## Development

### Prerequisites

- Rust stable toolchain
- Node.js 18+
- Python 3.11+
- Tauri CLI: `cargo install tauri-cli`

### Run the App

```bash
cargo tauri dev
```

### Tests

```bash
python -m pytest python/ -q          # Python unit and integration tests
npx vitest run                       # Frontend component tests
npx tsc --noEmit                     # TypeScript type check
```

### Verify Release Readiness

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-release.ps1
```

## CI

| Workflow | Purpose |
|---|---|
| `pages.yml` | Deploy GitHub Pages (landing page, update manifests) |
| `smoke.yml` | Build smoke test on push |
| `test-automation.yml` | L0 through L3 automated test levels |
| `release.yml` | Tagged release pipeline |
| `windows-installer.yml` | Installer build and validation |

## License

Proprietary - Copyright 2026 SignalOS
