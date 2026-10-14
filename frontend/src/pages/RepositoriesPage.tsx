import { getRepositories } from '@/api/endpoints'
import { AsyncSection } from '@/components/data/AsyncSection'
import { Card } from '@/components/ui/Card'
import {
  Button,
  EmptyState,
  Mono,
  PageHeader,
  SearchInput,
  Select,
  SkeletonTable,
  StatusPill,
} from '@/components/ui/primitives'
import { Table, type Column } from '@/components/ui/Table'
import { useApiQuery } from '@/hooks/useApiQuery'
import { useUrlFilters } from '@/hooks/useUrlFilters'
import type { RepositorySummary } from '@/types/api'
import { formatNumber, formatRelative } from '@/utils/format'

const ACTIVE_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'true', label: 'Active' },
  { value: 'false', label: 'Inactive' },
]

export function RepositoriesPage() {
  const filters = useUrlFilters()
  const search = filters.get('q') ?? ''
  const isActive = filters.get('is_active') ?? ''
  const cursor = filters.cursor

  const query = useApiQuery(
    () =>
      getRepositories({
        q: search || undefined,
        is_active: isActive === '' ? undefined : isActive === 'true',
        cursor,
        limit: 25,
      }),
    [search, isActive, cursor],
    { keepPreviousData: true },
  )

  const columns: ReadonlyArray<Column<RepositorySummary>> = [
    {
      key: 'name',
      header: 'Repository',
      render: (row) => (
        <span className="flex items-center gap-2">
          <Mono>{row.full_name}</Mono>
          {row.is_private ? (
            <StatusPill label="Private" glyph="◆" className="text-muted" />
          ) : null}
          {!row.is_active ? (
            <StatusPill
              label="Inactive"
              glyph="○"
              className="text-state-idle"
              title="This repository is connected but is not being reviewed."
            />
          ) : null}
        </span>
      ),
    },
    {
      key: 'language',
      header: 'Language',
      priority: 'low',
      render: (row) => <span className="text-muted">{row.primary_language ?? '—'}</span>,
    },
    {
      key: 'prs',
      header: 'PRs reviewed',
      align: 'right',
      priority: 'low',
      render: (row) => formatNumber(row.pull_requests_reviewed),
    },
    {
      key: 'runs',
      header: 'Attempts',
      align: 'right',
      priority: 'low',
      render: (row) => formatNumber(row.review_runs),
    },
    {
      key: 'open',
      header: 'Open findings',
      align: 'right',
      render: (row) => (
        <span className={row.open_high_severity > 0 ? 'text-state-warn' : undefined}>
          {formatNumber(row.open_findings)}
          {row.open_high_severity > 0 ? (
            <span className="ml-1 text-xs">({row.open_high_severity} high+)</span>
          ) : null}
        </span>
      ),
    },
    {
      key: 'latest',
      header: 'Last review',
      align: 'right',
      render: (row) => (
        <time dateTime={row.latest_review_at ?? undefined} title={row.latest_review_at ?? undefined}>
          {formatRelative(row.latest_review_at)}
        </time>
      ),
    },
  ]

  return (
    <div className="mx-auto max-w-[1180px]">
      <PageHeader
        title="Repositories"
        subtitle="Every repository this installation can see, with its review history."
      />

      <Card>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <SearchInput
            label="Search repositories"
            value={search}
            placeholder="Filter by name…"
            onChange={(value) => filters.set('q', value)}
          />
          <Select
            label="State"
            value={isActive}
            options={ACTIVE_OPTIONS}
            onChange={(value) => filters.set('is_active', value)}
          />
          {filters.activeCount > 0 ? (
            <Button variant="subtle" onClick={filters.clear}>
              Clear filters
            </Button>
          ) : null}
        </div>

        <AsyncSection
          query={query}
          skeleton={<SkeletonTable rows={6} columns={4} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title={filters.activeCount > 0 ? 'No repositories match these filters' : 'No repositories yet'}
              hint={
                filters.activeCount > 0
                  ? 'Try a broader search, or clear the filters.'
                  : 'Install the GitHub App on a repository and its pull requests will appear here.'
              }
              action={
                filters.activeCount > 0 ? (
                  <Button onClick={filters.clear}>Clear filters</Button>
                ) : null
              }
            />
          }
        >
          {(data) => (
            <>
              <Table
                caption="Repositories"
                columns={columns}
                rows={data.items}
                rowKey={(row) => String(row.id)}
                onRowHref={(row) => `/repositories/${row.id}`}
              />
              <Pager
                hasPrevious={cursor !== undefined}
                nextCursor={data.next_cursor}
                onNext={(next) => filters.setCursor(next)}
                onReset={() => filters.setCursor(undefined)}
              />
            </>
          )}
        </AsyncSection>
      </Card>
    </div>
  )
}

/**
 * Keyset paging controls.
 *
 * Forward-only, and "Start over" rather than "Previous", because a keyset
 * cursor points one way. Offering a Previous button would mean either keeping
 * a client-side stack of cursors — which breaks on a page reload and on a
 * shared link — or falling back to offsets, which is what keyset pagination
 * exists to avoid. Saying what the control actually does is better than
 * implying a capability that is not there.
 */
export function Pager({
  hasPrevious,
  nextCursor,
  onNext,
  onReset,
}: {
  hasPrevious: boolean
  nextCursor: string | null
  onNext: (cursor: string) => void
  onReset: () => void
}) {
  if (!hasPrevious && !nextCursor) return null
  return (
    <nav className="mt-4 flex items-center justify-between" aria-label="Pagination">
      <Button variant="subtle" onClick={onReset} disabled={!hasPrevious}>
        ← Start over
      </Button>
      <Button onClick={() => nextCursor && onNext(nextCursor)} disabled={!nextCursor}>
        Next page →
      </Button>
    </nav>
  )
}
