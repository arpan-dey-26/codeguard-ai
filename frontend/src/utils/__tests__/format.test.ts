/**
 * Formatting behaviour.
 *
 * These are behaviour tests, not snapshots: each asserts what a user would see
 * and why, so a change in output that breaks a rule fails here rather than
 * being blessed by an updated snapshot file.
 */

import { describe, expect, summary, test } from './harness'
import {
  formatDuration,
  formatNumber,
  formatPercent,
  formatRelative,
  humanise,
  isoDaysAgo,
  shortPath,
  shortSha,
} from '../format'

const NOW = Date.UTC(2026, 8, 16, 12, 0, 0)

describe('durations', () => {
  test('sub-second durations keep millisecond precision', () => {
    expect(formatDuration(450)).toBe('450 ms')
  })

  test('seconds are shown with one decimal, not as raw milliseconds', () => {
    // "61000 ms" asks the reader to do arithmetic to learn it took a minute.
    expect(formatDuration(1500)).toBe('1.5 s')
  })

  test('over a minute reads as minutes and seconds', () => {
    expect(formatDuration(61_000)).toBe('1m 1s')
    expect(formatDuration(120_000)).toBe('2m')
  })

  test('a missing duration is an em dash, never zero', () => {
    // Rendering null as "0 ms" would claim the review was instant.
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(undefined)).toBe('—')
  })
})

describe('relative time', () => {
  test('very recent times read as "just now"', () => {
    expect(formatRelative(new Date(NOW - 5_000).toISOString(), NOW)).toBe('just now')
  })

  test('hours ago is expressed in hours', () => {
    const value = formatRelative(new Date(NOW - 2 * 60 * 60 * 1000).toISOString(), NOW)
    expect(value).toContain('2')
    expect(value).toContain('hour')
  })

  test('days ago is expressed in days', () => {
    const value = formatRelative(new Date(NOW - 3 * 24 * 60 * 60 * 1000).toISOString(), NOW)
    expect(value).toContain('3')
    expect(value).toContain('day')
  })

  test('a missing timestamp is an em dash', () => {
    expect(formatRelative(null, NOW)).toBe('—')
  })

  test('an unparseable timestamp does not throw or render Invalid Date', () => {
    expect(formatRelative('not a date', NOW)).toBe('—')
  })
})

describe('identifiers', () => {
  test('a commit is shortened to seven characters, as git does', () => {
    expect(shortSha('a1b2c3d4e5f6a7b8c9d0')).toBe('a1b2c3d')
  })

  test('a missing commit is an em dash', () => {
    expect(shortSha(null)).toBe('—')
  })

  test('a long path is truncated from the left so the file name survives', () => {
    // The end identifies the file; the start is shared by the whole directory.
    const result = shortPath('a/very/long/directory/structure/with/a/file/session.py', 24)
    expect(result).toContain('session.py')
    expect(result.length <= 24).toBeTruthy()
  })

  test('a short path is untouched', () => {
    expect(shortPath('app/session.py', 48)).toBe('app/session.py')
  })
})

describe('numbers', () => {
  test('a missing number is an em dash, never zero', () => {
    expect(formatNumber(null)).toBe('—')
    expect(formatNumber(undefined)).toBe('—')
  })

  test('zero is rendered as zero', () => {
    expect(formatNumber(0)).toBe('0')
  })

  test('percentages round to whole numbers by default', () => {
    expect(formatPercent(0.6)).toBe('60%')
    expect(formatPercent(null)).toBe('—')
  })
})

describe('token humanising', () => {
  test('a snake_case gate name becomes a sentence', () => {
    expect(humanise('line_not_in_diff')).toBe('Line not in diff')
    expect(humanise('anchor_not_found')).toBe('Anchor not found')
  })

  test('a missing token is an em dash', () => {
    expect(humanise(null)).toBe('—')
  })
})

describe('range helpers', () => {
  test('a range start is UTC midnight, so two clients agree on the window', () => {
    const iso = isoDaysAgo(7, new Date(NOW))
    expect(iso).toContain('T00:00:00.000Z')
  })

  test('the offset is honoured', () => {
    const iso = isoDaysAgo(7, new Date(NOW))
    expect(iso.startsWith('2026-09-09')).toBeTruthy()
  })
})

process.exit(summary())
