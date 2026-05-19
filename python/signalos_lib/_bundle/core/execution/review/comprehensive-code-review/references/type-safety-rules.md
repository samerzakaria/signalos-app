<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. -->

# Type Safety Review Rules

## Must Reject
- `any` type anywhere (use `unknown` + guard)
- Type assertions (`as`) without exhaustive narrowing
- Implicit return types on public functions
- `!` non-null assertion (use proper null checks)
- Untyped function parameters
- `Object`, `Function`, `{}` as types

## Must Verify
- Generic constraints are specific (not `object`)
- Union types are exhaustively handled (switch with `never`)
- Optional properties have fallback handling
- Array methods have proper type narrowing
- JSON.parse results are validated with type guards

## Patterns to Enforce
```typescript
// GOOD: Type guard
function isValid(x: unknown): x is ValidType {
  return typeof x === 'object' && x !== null && 'key' in x
}

// BAD: Type assertion
const data = json as MyType // Never trust unvalidated data

// GOOD: Exhaustive switch
function handle(state: State): string {
  switch (state.kind) {
    case 'active': return 'running'
    case 'idle': return 'waiting'
    default: {
      const _exhaustive: never = state
      return _exhaustive
    }
  }
}
```
