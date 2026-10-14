import { useCallback, useEffect, useState } from 'react'
import { getHealth } from '@/api/endpoints'
import { errorKind } from '@/hooks/useApiQuery'
import type { HealthResponse } from '@/types/api'

type State =
  | { status: 'loading' }
  // `kind` is carried alongside the message (v0.6.2) so the error UI can name
  // the right remedy. Losing the error object and keeping only its text meant
  // every failure here was presented as the same failure.
  | { status: 'error'; message: string; kind: 'network' | 'server' }
  | { status: 'ready'; data: HealthResponse }

/**
 * Fetch backend health.
 *
 * Hand-rolled in Phase 0 because it was then the only request in the
 * application.
 *
 * **Corrected in v0.6.1.** This comment used to say that from Phase 4 all
 * server state "moves to TanStack Query". It did not, and could not: the npm
 * registry returns 403 in this project's environment, so Phase 4 wrote
 * `useApiQuery` instead — see that file for what it does and does not cover.
 * A comment describing a migration that never happened sends the next reader
 * looking for a dependency the project does not have.
 *
 * This hook stays separate from `useApiQuery` for one reason: `/health` is the
 * one endpoint that must still answer when the session does not, so it must
 * not sit behind anything that treats a 401 as a reason to stop.
 */
export function useHealth(): State & { refetch: () => void } {
  const [state, setState] = useState<State>({ status: 'loading' })

  const load = useCallback(() => {
    let cancelled = false
    setState({ status: 'loading' })
    getHealth()
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        const message = error instanceof Error ? error.message : 'Unable to reach the backend'
        const kind =
          error instanceof Error ? errorKind(error) : ('network' as const)
        setState({ status: 'error', message, kind })
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => load(), [load])

  return { ...state, refetch: load }
}
