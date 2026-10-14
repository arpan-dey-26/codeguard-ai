/**
 * Session state for the SPA.
 *
 * **What this deliberately does not do: hold a credential.** There is no token
 * in state, in a ref, in `localStorage` or in `sessionStorage`. The session
 * lives in an HttpOnly cookie the browser attaches on its own, and everything
 * here is *derived* from asking the server who we are. If an XSS payload runs
 * on this page it can act within the session for as long as the page is open —
 * which is bad — but it cannot copy a token out and use it later, which is
 * worse. That distinction is the reason for the design.
 *
 * Consequently the only question this module answers is "does the server
 * currently recognise this browser?", and the only way it answers it is by
 * asking. There is no client-side expiry check, because a client-side check is
 * a guess the server may disagree with, and the server's answer is the one that
 * decides what renders.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  NetworkError,
  apiFetch,
  apiUrl,
  setSessionCsrf,
  setUnauthorizedHandler,
} from '@/api/client'
import type { MeResponse, SessionStateResponse } from '@/types/api'

type AuthState =
  | { status: 'loading' }
  | { status: 'anonymous'; signInAvailable: boolean }
  | { status: 'authenticated'; me: MeResponse }
  /**
   * The API did not answer.
   *
   * **Added in v0.6.1.** Every failure used to resolve to `anonymous` with
   * `signInAvailable: false`, so a developer running only the frontend was told
   * "this deployment has no GitHub OAuth credentials set" — a confident,
   * unfounded claim about their configuration, when the true and actionable
   * fact was that nothing was listening on the API port.
   *
   * Kept separate from `anonymous` because the two call for opposite things:
   * anonymous means *you* should sign in, unavailable means *the server* is not
   * there. Neither grants any access; this is a rendering distinction, and the
   * server still authorises every byte of data regardless of what shows here.
   */
  | { status: 'unavailable'; detail: string }
  /**
   * Not signed in, and the server is serving reads anyway.
   *
   * `DASHBOARD_ALLOW_UNAUTHENTICATED_READS` is a development-only opt-in that
   * the backend refuses to start with in production. Before v0.6.1 the SPA had
   * no idea it existed, so the one mode built for running this dashboard
   * without OAuth showed a sign-in page and nothing else.
   *
   * Deliberately **not** folded into `authenticated`: there is no user, nothing
   * here claims there is one, and the shell says so on every page.
   */
  | { status: 'open'; signInAvailable: boolean }

interface AuthContextValue {
  state: AuthState
  /** Send the browser to GitHub, returning here afterwards. */
  signIn: (returnTo?: string) => void
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading' })

  const load = useCallback(async () => {
    try {
      // `/auth/session` first: it always returns 200, so "not signed in" is a
      // normal answer rather than an error the boot sequence has to swallow.
      const session = await apiFetch<SessionStateResponse>('/api/v1/auth/session')
      if (!session.authenticated) {
        setSessionCsrf(null)
        if (session.unauthenticated_reads_enabled === true) {
          // The deployment has deliberately opted out of authentication for
          // reads (development only — the backend refuses to start in
          // production with it on). Showing a sign-in page here would be
          // wrong in both directions: there is nothing to sign in to, and the
          // data is already being served. Render the application and say so,
          // loudly and permanently, in the shell.
          //
          // **This grants nothing.** The client is not claiming a session and
          // holds no credential; `resolve_scope` on the server reads the same
          // setting and decides what any query may return. Removing this
          // branch would not make the deployment more secure — it would only
          // make its own dashboard unusable against it.
          setState({ status: 'open', signInAvailable: session.sign_in_available })
          return
        }
        setState({ status: 'anonymous', signInAvailable: session.sign_in_available })
        return
      }
      const me = await apiFetch<MeResponse>('/api/v1/me')
      setSessionCsrf(me.csrf_token)
      setState({ status: 'authenticated', me })
    } catch (error) {
      // A dashboard that cannot establish who you are must not render as
      // though it had — but *why* it could not is the difference between a
      // screen the visitor can act on and one that misinforms them.
      if (error instanceof NetworkError) {
        setState({
          status: 'unavailable',
          detail: 'The API did not respond. It is probably not running.',
        })
        return
      }
      if (error instanceof ApiError && error.status >= 500) {
        // Includes the dev server's 502/504 when nothing is listening behind
        // the proxy, which is the same situation seen through Vite.
        setState({
          status: 'unavailable',
          detail:
            error.status === 502 || error.status === 504
              ? 'The dev server could not reach the API behind it.'
              : `The API answered ${error.status}.`,
        })
        return
      }
      if (error instanceof ApiError && error.status === 401) {
        setState({ status: 'anonymous', signInAvailable: true })
        return
      }
      setState({
        status: 'unavailable',
        detail:
          'The API response could not be verified. Please try again or contact the deployment administrator.',
      })
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    // A 401 from anywhere means the session ended — it expired, it was
    // revoked, or another tab signed out. Drop to anonymous immediately rather
    // than leaving a stale user's name in the header above empty tables.
    setUnauthorizedHandler(() => setState({ status: 'anonymous', signInAvailable: true }))
    return () => setUnauthorizedHandler(null)
  }, [])

  const signIn = useCallback((returnTo?: string) => {
    const target = returnTo ?? window.location.pathname + window.location.search
    // A full navigation, not fetch: the OAuth flow is a redirect chain the
    // browser has to follow, and it has to set cookies along the way.
    const next = encodeURIComponent(target.startsWith('/') ? target : '/')
    window.location.assign(apiUrl(`/api/v1/auth/github/login?next=${next}`))
  }, [])

  const signOut = useCallback(async () => {
    await apiFetch<void>('/api/v1/auth/logout', { method: 'POST' })
    setSessionCsrf(null)
    setState({ status: 'anonymous', signInAvailable: true })
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ state, signIn, signOut, refresh: load }),
    [state, signIn, signOut, load],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (value === null) {
    throw new Error('useAuth must be used inside an AuthProvider.')
  }
  return value
}
