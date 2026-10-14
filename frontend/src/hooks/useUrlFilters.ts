import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

/**
 * Filter state lives in the URL, not in component state.
 *
 * A user who has filtered to "open critical security findings in acme/web"
 * must be able to bookmark it, share it, and get it back with the browser's
 * back button (architecture.md §18.3). Keeping that state in `useState` breaks
 * all three, and it is the single most common omission in dashboards.
 *
 * Changing any filter also resets the keyset cursor. It has to: a cursor is a
 * position inside *a specific ordered result set*, and carrying one across a
 * filter change asks the server to resume from a row that may not be in the
 * new set at all.
 */

export interface UrlFilters {
  get: (key: string) => string | undefined
  getAll: (key: string) => string[]
  getNumber: (key: string) => number | undefined
  set: (key: string, value: string | undefined) => void
  setAll: (key: string, values: readonly string[]) => void
  toggle: (key: string, value: string) => void
  /** Set several keys at once, so the page navigates a single time. */
  patch: (changes: Record<string, string | undefined>) => void
  clear: () => void
  /** The cursor is managed separately: it is position, not a filter. */
  cursor: string | undefined
  setCursor: (cursor: string | undefined) => void
  activeCount: number
}

const CURSOR_KEY = 'cursor'

export function useUrlFilters(): UrlFilters {
  const [params, setParams] = useSearchParams()

  const write = useCallback(
    (
      mutate: (next: URLSearchParams) => void,
      options: { keepCursor?: boolean } = {},
    ) => {
      const keepCursor = options.keepCursor ?? false
      const next = new URLSearchParams(params)
      mutate(next)
      if (!keepCursor) next.delete(CURSOR_KEY)
      setParams(next, { replace: false })
    },
    [params, setParams],
  )

  return useMemo<UrlFilters>(() => {
    const activeCount = [...params.keys()].filter((key) => key !== CURSOR_KEY).length

    return {
      get: (key) => params.get(key) ?? undefined,
      getAll: (key) => params.getAll(key),
      getNumber: (key) => {
        const raw = params.get(key)
        if (raw === null) return undefined
        const value = Number(raw)
        return Number.isFinite(value) ? value : undefined
      },
      set: (key, value) =>
        write((next) => {
          if (value === undefined || value === '') next.delete(key)
          else next.set(key, value)
        }),
      setAll: (key, values) =>
        write((next) => {
          next.delete(key)
          for (const value of values) next.append(key, value)
        }),
      toggle: (key, value) =>
        write((next) => {
          const current = next.getAll(key)
          next.delete(key)
          const updated = current.includes(value)
            ? current.filter((item) => item !== value)
            : [...current, value]
          for (const item of updated) next.append(key, item)
        }),
      patch: (changes) =>
        write((next) => {
          for (const [key, value] of Object.entries(changes)) {
            if (value === undefined || value === '') next.delete(key)
            else next.set(key, value)
          }
        }),
      clear: () => setParams(new URLSearchParams(), { replace: false }),
      cursor: params.get(CURSOR_KEY) ?? undefined,
      setCursor: (cursor) =>
        write(
          (next) => {
            if (cursor) next.set(CURSOR_KEY, cursor)
            else next.delete(CURSOR_KEY)
          },
          { keepCursor: true },
        ),
      activeCount,
    }
  }, [params, setParams, write])
}

/** Ranges the analytics controls offer. `null` days means "everything". */
export const RANGE_OPTIONS = [
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
] as const

export function rangeParamsFrom(days: string | undefined): { from?: string } {
  const parsed = Number(days ?? '30')
  const safe = Number.isFinite(parsed) && parsed > 0 ? parsed : 30
  // The API includes today by ending at tomorrow's UTC midnight.
  const start = new Date(Date.now() - (Math.max(1, Math.floor(safe)) - 1) * 24 * 60 * 60 * 1000)
  const utcMidnight = new Date(
    Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()),
  )
  return { from: utcMidnight.toISOString() }
}
