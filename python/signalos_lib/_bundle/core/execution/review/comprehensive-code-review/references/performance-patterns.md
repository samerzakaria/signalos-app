<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized: dropped VS Code-extension-specific anti-patterns, kept the General TypeScript section + Memory Leaks (with generic disposal pattern). Added a SignalOS-flavored "Tauri Desktop / Sidecar" section. -->

# Performance Anti-Patterns

## Tauri Desktop / Long-Running Sidecar
| Anti-Pattern | Impact | Fix |
|-------------|--------|-----|
| Sync IPC in render path | UI freeze | Wrap in `tauriInvoke()` async, surface loading state via signal |
| Re-reading workspace file on every render | I/O thrash | Read once on workspace change, cache in signal |
| Signal effect that writes a signal it reads | Render loop | Effect should be a one-way subscriber; mutations go in handlers |
| Unfiltered `workspace:changed` listener | Refetch storms on bulk writes | Debounce 200-400ms, batch updates |
| Spawning a subprocess per call | Process churn | Reuse the long-running sidecar via stdin IPC |
| Holding a Tauri command receiver while awaiting | Other IPCs queue up | Drop the receiver before any long await |

## General TypeScript
| Anti-Pattern | Impact | Fix |
|-------------|--------|-----|
| String concat in loop | O(n²) memory | Array + join |
| Deep clone large objects | Memory spike | Structural sharing |
| `await` in loop | Sequential I/O | `Promise.all` |
| New regex inside a loop | Compilation overhead | Hoist outside the loop |
| `JSON.stringify` for equality comparison | CPU waste | Deep-equality helper |

## Memory Leaks
| Source | Detection | Fix |
|--------|-----------|-----|
| Event listeners not disposed | Growing memory | Track unsubscribers, call them in cleanup |
| Closures capturing large scope | Retained objects | Minimize capture surface |
| Cache without eviction | Unbounded growth | LRU, TTL, or `WeakMap` |
| Timers not cleared | Orphaned callbacks | `clearTimeout` / `clearInterval` in cleanup |
| Signal effects without proper teardown | Multiple registrations on hot-reload | Effects in module scope are singletons; never register inside render |
