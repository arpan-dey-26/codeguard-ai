import { apiFetch, apiUrl, ApiResponseError, setSessionCsrf } from '../client'
import { describe, expect, summary, test } from '../../utils/__tests__/harness'

describe('deployment API URLs', () => {
  test('OAuth uses the configured backend host', () => {
    expect(
      apiUrl('/api/v1/auth/github/login?next=%2Frepositories', 'https://api.example.com'),
    ).toBe('https://api.example.com/api/v1/auth/github/login?next=%2Frepositories')
  })
  test('a trailing slash does not produce a double slash', () => {
    expect(apiUrl('/health', 'https://api.example.com/')).toBe('https://api.example.com/health')
  })
  test('development requests retain the same-origin proxy path', () => {
    expect(apiUrl('/health', '')).toBe('/health')
  })
})

const originalFetch = globalThis.fetch
const requests: RequestInit[] = []
let invalidResponse: unknown
try {
  globalThis.fetch = async (_input, init) => {
    requests.push(init ?? {})
    return new Response(null, { status: 204 })
  }
  setSessionCsrf('test-only-csrf-value')
  await apiFetch('/api/v1/auth/logout', { method: 'POST' })
  await apiFetch('/health')
  globalThis.fetch = async () => new Response('{}', { status: 401 })
  try {
    await apiFetch('/api/v1/me')
  } catch {
    /* expected unauthorized response */
  }
  globalThis.fetch = async (_input, init) => {
    requests.push(init ?? {})
    return new Response(null, { status: 204 })
  }
  await apiFetch('/api/v1/auth/logout', { method: 'POST' })
  globalThis.fetch = async () => new Response('<!doctype html><html></html>')
  try {
    await apiFetch('/api/v1/auth/session')
  } catch (error) {
    invalidResponse = error
  }
} finally {
  globalThis.fetch = originalFetch
  setSessionCsrf(null)
}

describe('cross-host session protection', () => {
  test('unsafe requests use the /me CSRF token without a readable API cookie', () => {
    expect(new Headers(requests[0]?.headers).get('X-CSRF-Token')).toBe('test-only-csrf-value')
  })
  test('safe requests do not send the CSRF token', () => {
    expect(new Headers(requests[1]?.headers).has('X-CSRF-Token')).toBe(false)
  })
  test('unauthorized responses clear the old session CSRF token', () => {
    expect(new Headers(requests[2]?.headers).has('X-CSRF-Token')).toBe(false)
  })
  test('SPA HTML is identified as an invalid API response', () => {
    expect(invalidResponse instanceof ApiResponseError).toBe(true)
  })
})

process.exit(summary())
