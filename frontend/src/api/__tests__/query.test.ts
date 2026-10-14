/**
 * Query-string construction.
 *
 * Small surface, high blast radius: a wrong query string produces a 422 from
 * the backend's filter validation, or — worse — silently drops a filter and
 * shows the user unfiltered data they believe is filtered.
 */

import { describe, expect, summary, test } from '../../utils/__tests__/harness'
import { buildQuery } from '../endpoints'

describe('buildQuery', () => {
  test('no parameters produce no query string', () => {
    expect(buildQuery({})).toBe('')
  })

  test('undefined and null are omitted, not serialised', () => {
    // `?severity=undefined` reaches the server and is rejected as an invalid
    // filter value, which presents to the user as a broken page.
    expect(buildQuery({ a: undefined, b: null })).toBe('')
  })

  test('an empty string is omitted', () => {
    // An empty search box means "no filter", not "match the empty string".
    expect(buildQuery({ q: '' })).toBe('')
  })

  test('zero and false are kept, because they are real values', () => {
    expect(buildQuery({ limit: 0 })).toBe('?limit=0')
    expect(buildQuery({ include_total: false })).toBe('?include_total=false')
  })

  test('arrays expand into repeated keys, as the backend expects', () => {
    expect(buildQuery({ severity: ['critical', 'high'] })).toBe(
      '?severity=critical&severity=high',
    )
  })

  test('an empty array contributes nothing', () => {
    expect(buildQuery({ severity: [] })).toBe('')
  })

  test('values are percent-encoded', () => {
    const query = buildQuery({ q: 'a b&c=d' })
    expect(query).notToContain(' ')
    expect(query).toContain('a+b%26c%3Dd')
  })

  test('a cursor survives encoding intact', () => {
    const cursor = 'eyJpZCI6NDJ9'
    expect(buildQuery({ cursor })).toBe(`?cursor=${cursor}`)
  })

  test('an injection payload is encoded, not passed through', () => {
    const query = buildQuery({ q: "'; DROP TABLE findings; --" })
    expect(query).notToContain("'")
    expect(query).notToContain(' ')
  })

  test('mixed parameters keep their insertion order', () => {
    expect(buildQuery({ repository_id: 3, limit: 25 })).toBe('?repository_id=3&limit=25')
  })
})

process.exit(summary())
