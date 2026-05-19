<!-- Adapted from SignalGuard (MIT) — github.com/shaabancis/SignalGuard -->
<!-- Generalized for the SignalOS Python protocol bundle. -->

// Template: Integration Test
// For testing component interactions with real or fake dependencies

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'

describe('FeatureName Integration', () => {
  let tempDir: string

  beforeEach(() => {
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'signal-test-'))
  })

  afterEach(() => {
    fs.rmSync(tempDir, { recursive: true, force: true })
  })

  describe('when workspace has scope cards', () => {
    beforeEach(() => {
      // Setup test workspace structure
      const signalDir = path.join(tempDir, '.signalos', 'scope-cards')
      fs.mkdirSync(signalDir, { recursive: true })
      fs.writeFileSync(
        path.join(signalDir, 'SC-001.md'),
        '# SC-001: Test Scope\n\n## Status: ACTIVE\n\nTest description'
      )
    })

    it('should parse scope cards from directory', () => {
      const cards = parseScopeCards(tempDir)
      expect(cards).toHaveLength(1)
      expect(cards[0]).toMatchObject({
        id: 'SC-001',
        status: 'ACTIVE',
      })
    })

    it('should handle missing directory gracefully', () => {
      const cards = parseScopeCards('/nonexistent/path')
      expect(cards).toEqual([])
    })
  })

  describe('when files change during operation', () => {
    it('should invalidate cache after file modification', () => {
      const filePath = path.join(tempDir, 'test.md')
      fs.writeFileSync(filePath, 'version 1')

      const first = readCached(filePath)
      fs.writeFileSync(filePath, 'version 2')
      invalidateCache(filePath)
      const second = readCached(filePath)

      expect(first).not.toEqual(second)
    })
  })
})
