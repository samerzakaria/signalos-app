<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. -->

// Template: Security Test
// For validating input sanitization, injection prevention, and access control

import { describe, it, expect } from 'vitest'

describe('Security: InputValidation', () => {
  describe('path traversal prevention', () => {
    const maliciousPaths = [
      '../../../etc/passwd',
      '..\\..\\..\\windows\\system32',
      'valid/../../../etc/shadow',
      '%2e%2e%2f%2e%2e%2fetc%2fpasswd',
      '....//....//etc/passwd',
      'workspace/../../secrets.env',
    ]

    maliciousPaths.forEach((malPath) => {
      it(`should reject path traversal: ${malPath}`, () => {
        const result = validatePath(malPath)
        expect(result).toBeUndefined() // rejected
      })
    })

    it('should allow valid relative paths', () => {
      const result = validatePath('scope-cards/SC-001.md')
      expect(result).toBeDefined()
    })
  })

  describe('command injection prevention', () => {
    const injectionPayloads = [
      '; rm -rf /',
      '| cat /etc/passwd',
      '$(whoami)',
      '`id`',
      '& net user hacker /add',
      '\n\rinjected-command',
    ]

    injectionPayloads.forEach((payload) => {
      it(`should sanitize injection attempt: ${payload.slice(0, 20)}...`, () => {
        const result = sanitizeInput(payload)
        expect(result).not.toContain(';')
        expect(result).not.toContain('|')
        expect(result).not.toContain('$')
        expect(result).not.toContain('`')
      })
    })
  })

  describe('XSS prevention in webview content', () => {
    const xssPayloads = [
      '<script>alert(1)</script>',
      '<img onerror=alert(1) src=x>',
      'javascript:alert(1)',
      '<svg onload=alert(1)>',
      '"><script>alert(1)</script>',
    ]

    xssPayloads.forEach((payload) => {
      it(`should escape XSS: ${payload.slice(0, 30)}...`, () => {
        const html = renderContent(payload)
        expect(html).not.toContain('<script')
        expect(html).not.toContain('onerror')
        expect(html).not.toContain('javascript:')
      })
    })
  })

  describe('oversized input handling', () => {
    it('should reject input exceeding max length', () => {
      const huge = 'x'.repeat(1_000_000)
      const result = processInput(huge)
      expect(result.ok).toBe(false)
      expect(result.error.kind).toBe('validation')
    })

    it('should handle deeply nested JSON', () => {
      let nested = '{"a":' .repeat(100) + '1' + '}'.repeat(100)
      expect(() => parseInput(nested)).not.toThrow()
    })
  })

  describe('prototype pollution prevention', () => {
    it('should not allow __proto__ manipulation', () => {
      const malicious = JSON.parse('{"__proto__":{"polluted":true}}')
      const result = safemerge({}, malicious)
      expect(({} as any).polluted).toBeUndefined()
    })

    it('should not allow constructor.prototype manipulation', () => {
      const malicious = { constructor: { prototype: { polluted: true } } }
      const result = safemerge({}, malicious)
      expect(({} as any).polluted).toBeUndefined()
    })
  })
})
