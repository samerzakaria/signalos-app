<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. -->

# Quality Metrics

## Function Complexity
| Metric | Target | Action if Exceeded |
|--------|--------|-------------------|
| Lines | ≤ 30 | Extract function |
| Cyclomatic complexity | ≤ 5 | Simplify conditionals |
| Nesting depth | ≤ 2 | Early returns |
| Parameters | ≤ 3 | Options object |
| Return points | ≤ 4 | Restructure flow |

## Code Smells
| Smell | Detection | Remedy |
|-------|-----------|--------|
| God function | >50 lines, multiple responsibilities | Decompose |
| Feature envy | Accesses other module's data extensively | Move logic |
| Shotgun surgery | Change requires edits in many places | Consolidate |
| Primitive obsession | String/number where type should exist | Create type |
| Duplicate code | Same logic in 2+ places | Extract shared |

## Clean Code Rules
- Functions do one thing
- Names reveal intent
- No side effects in query functions
- Commands don't return values
- DRY applies to knowledge, not code structure
- Prefer composition over inheritance
