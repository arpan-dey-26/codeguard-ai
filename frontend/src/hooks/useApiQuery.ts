import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '@/api/client'

/**
 * Server state, as a discriminated union.
 *
 * **Why this and not TanStack Query.** Phase 0 planned to adopt it here, and
 * that plan is not being quietly abandoned — it cannot be installed: the npm
 * registry returns 403 in this environment. So this hook covers the four
 * things the dashboard actually needs (cancellation, an explicit error state,
 * refetch, and not tearing state on unmount) and nothing else. It is not a
 * cache, it does not deduplicate across components, and it does not
 * background-refetch. When the registry is reachable, replacing this with
 * TanStack Query is a change to this file and the call sites' `data` access,
 * not to any page's structure.
 *
 * **A union, not three booleans.** `{ loading, error, data }` permits states
 * that cannot happen — loading *and* error, data *and* error — and every
 * component then has to decide which wins. A union makes "render the error"
 * the only reachable branch when there is an error.
 *
 * **`keepPreviousData` exists for one reason**: a filter change on a list
 * should not blank the table. Without it, every keystroke in the search box
 * replaces the results with a skeleton, and the page flickers.
 */

export type QueryState<T> =
  | { status: 'loading'; data: T | null }
  | { status: 'error'; error: ApiError | Error; data: T | null }
  | { status: 'ready'; data: T }

export interface QueryResult<T> {
  state: QueryState<T>
  refetch: () => void
  /** True while a *subsequent* fetch runs and previous data is still shown. */
  isRefreshing: boolean
}

export function useApiQuery<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
  options: { enabled?: boolean; keepPreviousData?: boolean } = {},
): QueryResult<T> {
  const enabled = options.enabled ?? true
  const keepPreviousData = options.keepPreviousData ?? false

  const [state, setState] = useState<QueryState<T>>({ status: 'loading', data: null })
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [nonce, setNonce] = useState(0)

  // The fetcher is a new closure on every render. Holding it in a ref keeps it
  // out of the effect's dependency list, so the request re-runs when `deps`
  // change — not on every parent re-render.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const dataRef = useRef<T | null>(null)

  useEffect(() => {
    if (!enabled) return undefined

    let cancelled = false
    const previous = dataRef.current
    setState({ status: 'loading', data: keepPreviousData ? previous : null })
    setIsRefreshing(keepPreviousData && previous !== null)

    fetcherRef
      .current()
      .then((result) => {
        if (cancelled) return
        dataRef.current = result
        setState({ status: 'ready', data: result })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setState({
          status: 'error',
          error: error instanceof Error ? error : new Error('Request failed'),
          data: keepPreviousData ? previous : null,
        })
      })
      .finally(() => {
        if (!cancelled) setIsRefreshing(false)
      })

    return () => {
      // Guards against the classic two failures: a state update after unmount,
      // and an older slower request overwriting a newer one's result.
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, keepPreviousData, nonce, ...deps])

  const refetch = useCallback(() => setNonce((value) => value + 1), [])

  return { state, refetch, isRefreshing }
}

/** The message to show a user, with the correlation id when there is one. */
export function errorMessage(error: ApiError | Error): string {
  if (error instanceof ApiError) {
    if (error.status === 503) {
      return error.problem?.detail ?? 'The API is not available in this deployment.'
    }
    if (error.status === 404) return 'Not found.'
    if (error.status === 422) return error.problem?.detail ?? 'That request was not valid.'
    if (error.status === 429) return 'Too many requests. Try again shortly.'
    if (error.status >= 500) return 'The server could not complete that request.'
    return error.problem?.title ?? error.message
  }
  return 'Could not reach the API. Check that the backend is running.'
}

export function correlationIdOf(error: ApiError | Error): string | null {
  return error instanceof ApiError ? error.correlationId : null
}

/**
 * Which *kind* of failure this was (v0.6.2).
 *
 * `network` means the request never reached a server — nothing answered. Every
 * other case means a server answered and said no. The two have different
 * remedies (start the backend / check the port, versus retry or read the
 * message), so the error UI shows a different glyph and a different second line
 * for each rather than one generic "something went wrong".
 *
 * Derived entirely from the error object the client already threw. It infers
 * nothing: `ApiError` exists only when a real HTTP response was parsed.
 */
export function errorKind(error: ApiError | Error): 'network' | 'server' {
  return error instanceof ApiError ? 'server' : 'network'
}
