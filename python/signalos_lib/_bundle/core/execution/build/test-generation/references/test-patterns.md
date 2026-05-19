<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. Replaced .signal/ paths with .signalos/. -->

# Test Patterns Guide

## Factory Pattern
```typescript
// Create typed test data with sensible defaults
function createScopeCard(overrides: Partial<ScopeCard> = {}): ScopeCard {
  return {
    id: `SC-${String(Math.floor(Math.random() * 999)).padStart(3, '0')}`,
    desc: 'Default test scope description',
    status: 'ACTIVE',
    fp: '/test/.signalos/scope-cards/SC-001.md',
    ...overrides,
  }
}

// Create collections
function createScopeCards(count: number): ScopeCard[] {
  return Array.from({ length: count }, (_, i) =>
    createScopeCard({ id: `SC-${String(i + 1).padStart(3, '0')}` })
  )
}
```

## Arrange-Act-Assert (AAA)
```typescript
it('should filter active cards from mixed statuses', () => {
  // Arrange — setup preconditions
  const cards = [
    createScopeCard({ status: 'ACTIVE' }),
    createScopeCard({ status: 'COMPLETE' }),
    createScopeCard({ status: 'ACTIVE' }),
  ]

  // Act — execute the behavior
  const result = filterActive(cards)

  // Assert — verify outcome
  expect(result).toHaveLength(2)
  expect(result.every(c => c.status === 'ACTIVE')).toBe(true)
})
```

## Table-Driven Tests
```typescript
describe('status parsing', () => {
  const cases = [
    { input: '## Status: ACTIVE', expected: 'ACTIVE' },
    { input: '## Status: COMPLETE', expected: 'COMPLETE' },
    { input: '## Status: DEFERRED', expected: 'DEFERRED' },
    { input: '## Status: unknown', expected: 'UNKNOWN' },
    { input: '', expected: 'UNKNOWN' },
  ]

  cases.forEach(({ input, expected }) => {
    it(`should parse "${input}" as ${expected}`, () => {
      expect(parseStatus(input)).toBe(expected)
    })
  })
})
```

## Spy/Mock at Boundaries
```typescript
// Mock file system at the boundary
const mockReadFile = vi.fn()
const service = createService({ readFile: mockReadFile })

it('should handle file not found', async () => {
  mockReadFile.mockRejectedValue(new Error('ENOENT'))
  const result = await service.load('missing.md')
  expect(result.ok).toBe(false)
  expect(result.error.kind).toBe('not-found')
})
```

## Async Test Patterns
```typescript
// Promise resolution
it('should resolve with data', async () => {
  const result = await asyncFunction()
  expect(result).toBeDefined()
})

// Promise rejection
it('should reject on invalid input', async () => {
  await expect(asyncFunction(null)).rejects.toThrow('Invalid')
})

// Timeout handling
it('should timeout after 5s', async () => {
  const result = await withTimeout(slowOperation(), 5000)
  expect(result.ok).toBe(false)
  expect(result.error.message).toContain('Timeout')
})
```
