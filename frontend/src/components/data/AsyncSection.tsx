import type { ReactNode } from 'react'
import { ErrorState, SkeletonRows } from '@/components/ui/primitives'
import { correlationIdOf, errorKind, errorMessage, type QueryResult } from '@/hooks/useApiQuery'

/**
 * Renders one async section: loading, error, empty or populated.
 *
 * **Per-section, not per-page.** The dashboard home calls several independent
 * aggregates. If one is slow or fails, the rest must still render — a single
 * page-level error boundary turns one failed query into a blank screen
 * (architecture.md §18.3). Wrapping each section is what makes partial
 * availability a normal state rather than an outage.
 *
 * `isEmpty` is supplied by the caller because only the caller knows what empty
 * means for its own shape: an empty array, a zero total, a null detail.
 */

export function AsyncSection<T>({
  query,
  children,
  skeleton,
  isEmpty,
  empty,
  placeholderHeading,
}: {
  query: QueryResult<T>
  children: (data: T) => ReactNode
  skeleton?: ReactNode
  isEmpty?: (data: T) => boolean
  empty?: ReactNode
  /**
   * A heading to show while this section has no data yet (v0.6.2).
   *
   * **Why this exists.** The four detail pages build their `PageHeader` from the
   * fetched object — a finding's title, a repository's full name — so the header
   * lives inside `children` and only exists once the request has succeeded. A
   * browser pass over the real pages found the consequence: while loading, on an
   * API error, and on a 404 from a deep link, those pages rendered **no `<h1>`
   * at all**. A screen-reader user navigating by heading landed on nothing, and
   * a sighted user got a bare error box with no indication of which page they
   * were on.
   *
   * So a page whose heading depends on its data passes a heading that does not:
   * the resource's name, which is known from the route. It is rendered for the
   * loading, error and empty states and *not* for the ready state, where the
   * real data-derived header takes over. The page is never headingless.
   */
  placeholderHeading?: ReactNode
}) {
  const { state, refetch } = query

  // An error while previous data is on screen still shows the error: silently
  // leaving stale numbers up is how a user acts on data that is no longer true.
  if (state.status === 'error') {
    return (
      <>
        {placeholderHeading}
        <ErrorState
          message={errorMessage(state.error)}
          correlationId={correlationIdOf(state.error)}
          kind={errorKind(state.error)}
          onRetry={refetch}
        />
      </>
    )
  }

  const pending = <>{placeholderHeading}{skeleton ?? <SkeletonRows rows={4} />}</>

  if (state.status === 'loading' && state.data === null) return pending

  const data = state.data
  if (data === null) return pending

  if (isEmpty?.(data) && empty) {
    return (
      <>
        {placeholderHeading}
        {empty}
      </>
    )
  }

  return <>{children(data)}</>
}
