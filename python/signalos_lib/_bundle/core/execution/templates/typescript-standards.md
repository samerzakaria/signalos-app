<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. -->

# TypeScript Standards Reference

## Strict Mode Rules
- `strict: true` in tsconfig.json
- No implicit `any` — every variable typed
- No unused variables or parameters
- Strict null checks enabled

## Type Patterns

### Discriminated Unions
```typescript
type ParseResult =
  | { kind: 'success'; data: ScopeCard[] }
  | { kind: 'error'; message: string }
  | { kind: 'empty' }
```

### Type Guards
```typescript
function isScopeCard(value: unknown): value is ScopeCard {
  return (
    typeof value === 'object' &&
    value !== null &&
    'id' in value &&
    'desc' in value &&
    typeof (value as Record<string, unknown>).id === 'string'
  )
}
```

### Generic Constraints
```typescript
function findById<T extends { readonly id: string }>(items: readonly T[], id: string): T | undefined {
  return items.find(item => item.id === id)
}
```

## Function Rules
- Max 30 lines
- Max 3 parameters (use options object for more)
- Max 2 nesting levels
- Always return explicit types for public functions
- Use `readonly` parameters and properties

## Naming
- `camelCase` for functions and variables
- `PascalCase` for types, interfaces, classes
- `UPPER_SNAKE` for constants
- Prefix interfaces with descriptive noun (not `I`)
- Boolean variables: `is*`, `has*`, `can*`, `should*`
