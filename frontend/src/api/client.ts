/**
 * The single HTTP entry point for the application.
 *
 * Everything goes through here so that credential handling, error parsing and
 * correlation-id surfacing exist in exactly one place. A component that calls
 * `fetch` directly bypasses all three.
 *
 * `credentials: 'include'` is set because dashboard sessions use an HttpOnly
 * cookie (architecture.md §21.5). **No token is ever held in JavaScript.**
 * There is no Authorization header to attach, nothing in `localStorage`,
 * nothing in React state that survives a reload — so there is nothing for an
 * XSS payload to read and exfiltrate. That is the entire reason the session is
 * a cookie rather than a bearer token the SPA keeps.
 *
 * Phase 5 adds two things to this file:
 *
 * **The CSRF header.** Unsafe methods must echo the token the server put in a
 * readable companion cookie. The session cookie stays HttpOnly; the CSRF
 * cookie is deliberately readable, and on its own it authenticates nothing.
 *
 * **401 handling.** A 401 is not an error to render — it means the session
 * ended, and the only useful response is to send the user to sign in. It is
 * surfaced through a single subscriber hook so that every call site gets the
 * behaviour without each one remembering to implement it.
 */

import type { ProblemDetails } from '@/types/api'

// Optional chaining on `env` so this module can be imported outside Vite —
// by the test runner, which has no bundler to inject it. Vite always defines
// it, so nothing changes at runtime in the browser.
const BASE_URL = import.meta.env?.VITE_API_BASE_URL ?? ''

export function apiUrl(path: string, baseUrl = BASE_URL): string {
  return `${baseUrl.replace(/\/+$/, '')}${path}`
}

// /me supplies this non-credential token even when the API cookie belongs to
// another host and cannot be read by the frontend. Never persist it.
let sessionCsrf: string | null = null
export function setSessionCsrf(value: string | null): void {
  sessionCsrf = value
}

/** Must match `app.auth.cookies.CSRF_HEADER` and the CORS allow-list. */
export const CSRF_HEADER = 'X-CSRF-Token'

/** Must match `session_cookie_name` + `CSRF_COOKIE_SUFFIX` on the server. */
const CSRF_COOKIE = 'codeguard_session_csrf'

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

/**
 * Read the CSRF token from its cookie.
 *
 * `document.cookie` rather than storage: the server sets it, the server
 * expires it, and there is exactly one copy. Caching it in a module variable
 * would go stale the moment the session rotated, and the failure would be a
 * 403 on the next write with nothing obvious to point at.
 */
function csrfToken(): string | null {
  if (sessionCsrf) return sessionCsrf
  if (typeof document === 'undefined') return null
  for (const part of document.cookie.split(';')) {
    const [name, ...rest] = part.trim().split('=')
    if (name === CSRF_COOKIE) {
      try {
        return decodeURIComponent(rest.join('='))
      } catch {
        return null
      }
    }
  }
  return null
}

type UnauthorizedHandler = () => void

let onUnauthorized: UnauthorizedHandler | null = null

/**
 * Register what happens when the server says the session is gone.
 *
 * One subscriber, set once by the auth provider. A list of listeners would
 * invite several components to each decide what a 401 means, and they would
 * disagree.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler
}

/**
 * The API could not be reached at all.
 *
 * Distinct from `ApiError`, which means the server answered and said no.
 * `fetch` rejects with a bare `TypeError` when the connection fails, and a
 * `TypeError` is indistinguishable from a programming mistake — so it is
 * wrapped here, once, at the only place that calls `fetch`.
 *
 * **Added in v0.6.1**, because collapsing the two produced a specific lie: with
 * the backend simply not running, the session probe failed, the failure was
 * read as "not signed in", and the sign-in screen told the visitor this
 * deployment had no OAuth credentials configured. That is a claim about
 * someone's configuration, made on no evidence, and the one thing they could
 * usefully have been told — start the API — was the thing it did not say.
 */
export class NetworkError extends Error {
  readonly cause: unknown

  constructor(cause: unknown) {
    super('Could not reach the CodeGuard API.')
    this.name = 'NetworkError'
    this.cause = cause
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly problem: ProblemDetails | null

  constructor(status: number, problem: ProblemDetails | null, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.problem = problem
  }

  /** Shown to the user when support needs to find the server-side logs. */
  get correlationId(): string | null {
    return this.problem?.correlation_id ?? null
  }
}

export class ApiResponseError extends Error {
  constructor() {
    super('The API returned an invalid response. Check the API URL and routing configuration.')
    this.name = 'ApiResponseError'
  }
}

async function parseProblem(response: Response): Promise<ProblemDetails | null> {
  try {
    return (await response.json()) as ProblemDetails
  } catch {
    return null
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const token = SAFE_METHODS.has(method) ? null : csrfToken()

  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { [CSRF_HEADER]: token } : {}),
        ...init.headers,
      },
    })
  } catch (cause) {
    // DNS failure, connection refused, TLS failure, offline, or the dev
    // server's proxy finding nothing on the other end. None of these are
    // answers from the API, so none of them may be interpreted as one.
    throw new NetworkError(cause)
  }

  if (!response.ok) {
    const problem = await parseProblem(response)
    // The session ended — expired, revoked, or signed out in another tab.
    // Handled once, here, rather than in every component that might be the
    // unlucky one to make the first call after it happened.
    if (response.status === 401) {
      setSessionCsrf(null)
      onUnauthorized?.()
    }
    throw new ApiError(response.status, problem, problem?.title ?? response.statusText)
  }

  if (response.status === 204) return undefined as T
  try {
    return (await response.json()) as T
  } catch {
    throw new ApiResponseError()
  }
}
