/**
 * "The API refused" and "the API was not there" must stay distinguishable.
 *
 * **v0.6.1.** `apiFetch` used to let `fetch`'s rejection escape as a bare
 * `TypeError`, indistinguishable from a programming mistake. The auth provider
 * caught everything and resolved to *anonymous, sign-in unavailable*, so a
 * developer running only the frontend was shown:
 *
 *     Sign-in is not configured
 *     This deployment has no GitHub OAuth credentials set.
 *
 * Nothing had checked that. The true and actionable fact was that nothing was
 * listening on port 8000. These tests pin the distinction the fix rests on,
 * because it is exactly what a later "simplify the error handling" change would
 * collapse again without noticing.
 *
 * **On the shape of this file.** The harness's `test()` is synchronous: a body
 * that returns a promise would have its assertions run after `test()` had
 * already recorded a pass, so the test would be vacuous. That failure mode cost
 * this project 96 silently-unrunnable tests on the backend, so it is avoided
 * structurally here — every `await` happens at module top level, and every
 * assertion inside `test()` is synchronous over an already-settled result.
 */

import { describe, expect, summary, test } from '../../utils/__tests__/harness'
import { ApiError, NetworkError, apiFetch } from '../client'

const realFetch = globalThis.fetch

interface Outcome {
  error: unknown
  value: unknown
  threw: boolean
}

/** Run `body` with `fetch` stubbed, and return how it settled. */
async function capture(stub: () => Promise<Response>, path: string): Promise<Outcome> {
  ;(globalThis as { fetch: unknown }).fetch = stub
  try {
    const value = await apiFetch(path)
    return { error: null, value, threw: false }
  } catch (error) {
    return { error, value: null, threw: true }
  } finally {
    ;(globalThis as { fetch: unknown }).fetch = realFetch
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const connectionRefused = new TypeError('Failed to fetch')

// -- everything async resolves here, before a single assertion runs ----------
const unreachable = await capture(() => Promise.reject(connectionRefused), '/api/v1/auth/session')
const forbidden = await capture(() => Promise.resolve(json(403, { title: 'Nope' })), '/api/v1/me')
const ok = await capture(() => Promise.resolve(json(200, { authenticated: false })), '/health')

describe('a connection that never happened is not an answer', () => {
  test('a fetch rejection becomes NetworkError, not a bare TypeError', () => {
    expect(unreachable.threw).toBe(true)
    expect(unreachable.error instanceof NetworkError).toBe(true)
    expect(unreachable.error instanceof ApiError).toBe(false)
  })

  test('NetworkError keeps the original cause, so the console still has it', () => {
    expect((unreachable.error as NetworkError).cause).toBe(connectionRefused)
  })

  test('an HTTP refusal stays an ApiError and keeps its status', () => {
    expect(forbidden.threw).toBe(true)
    expect(forbidden.error instanceof ApiError).toBe(true)
    expect(forbidden.error instanceof NetworkError).toBe(false)
    expect((forbidden.error as ApiError).status).toBe(403)
  })

  test('a successful call is unaffected by any of this', () => {
    expect(ok.threw).toBe(false)
    expect((ok.value as { authenticated: boolean }).authenticated).toBe(false)
  })
})

describe('the two failures are not interchangeable', () => {
  test('they are distinct classes with distinct names', () => {
    const network = new NetworkError(new Error('boom'))
    const api = new ApiError(500, null, 'boom')
    expect(network instanceof ApiError).toBe(false)
    expect(api instanceof NetworkError).toBe(false)
    expect(network.name).toBe('NetworkError')
    expect(api.name).toBe('ApiError')
  })

  test('the message names the API, and does not blame configuration', () => {
    // The old behaviour asserted something about the operator's OAuth setup on
    // no evidence whatsoever. Whatever this message becomes, it must not do
    // that again.
    const message = new NetworkError(new Error('x')).message.toLowerCase()
    expect(message.includes('reach')).toBe(true)
    expect(message.includes('oauth')).toBe(false)
    expect(message.includes('configured')).toBe(false)
  })
})

process.exit(summary())
