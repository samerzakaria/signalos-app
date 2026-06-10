# Frontend conventions

## State: signals first, hooks where they fit

The UI uses **`@preact/signals` as the default** for shared and cross-component
state (see `src/state.ts`, `src/services/*`). Prefer `signal()` / `useSignal()`
for anything that outlives a single render or is read by more than one
component.

`useState` / `useEffect` from `preact/hooks` **are supported and used** (e.g.
`src/components/views/VelocityPanel.tsx`) — they are the right tool for local,
component-scoped state. There is **no hard ban** on hooks; signals are simply
the convention for app state.

### Why the `@preact/preset-vite` `transform-hook-names` plugin is fine

The preset enables `preact:transform-hook-names` for better devtools labels. It
does not prevent `useState` from working. Earlier reviews flagged this as
"forced to avoid hooks" — that is inaccurate; both patterns compile and run.

### Rule of thumb

| Scope | Use |
| --- | --- |
| App / shared / service state | `signal()` in `state.ts` or a service module |
| Derived values | `computed()` |
| Side effects on signal change | `effect()` |
| Local, ephemeral component state | `useState` / `useSignal` (either is fine) |

## Toolchains

Three toolchains are required for a full build: **Rust/Cargo**, **Python 3.11+**,
and **Node 20**. For a zero-setup environment use the dev container in
`.devcontainer/` (VS Code: "Reopen in Container"), which provisions all three
plus Tauri's system libraries. Build scripts now fail fast with an actionable
message when `cargo` is missing rather than erroring partway through.
