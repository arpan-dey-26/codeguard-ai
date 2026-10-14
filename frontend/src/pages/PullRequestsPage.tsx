import { Link } from 'react-router-dom'
import { getPullRequests } from '@/api/endpoints'
import { AsyncSection } from '@/components/data/AsyncSection'
import { LimitedCoverageBadge, RunStatusBadge } from '@/components/domain/badges'
import { Card } from '@/components/ui/Card'
import {
  Button,
  ChipFilter,
  EmptyState,
  Mono,
  PageHeader,
  SearchInput,
  SkeletonTable,
} from '@/components/ui/primitives'
import { Table, type Column } from '@/components/ui/Table'
import { Pager } from '@/pages/RepositoriesPage'
import { useApiQuery } from '@/hooks/useApiQuery'
import { useUrlFilters } from '@/hooks/useUrlFilters'
import type { PullRequestSummary } from '@/types/api'
import { formatNumber, formatRelative, shortSha } from '@/utils/format'

const PR_STATES = [
  { value: 'open', label: 'Open' },
  { value: 'closed', label: 'Closed' },
  { value: 'merged', label: 'Merged' },
]

const RUN_STATES = [
  { value: 'succeeded', label: 'Completed' },
  { value: 'partial_success', label: 'Partial' },
  { value: 'failed', label: 'Failed' },
  { value: 'superseded', label: 'Superseded' },
]

export function PullRequestsPage() {
  const filters = useUrlFilters()
  const search = filters.get('q') ?? ''
  const states = filters.getAll('state')
  const runStatuses = filters.getAll('run_status')
  const repositoryId = filters.getNumber('repository_id')
  const cursor = filters.cursor

  const query = useApiQuery(
    () =>
      getPullRequests({
        q: search || undefined,
        state: states.length ? states : undefined,
        run_status: runStatuses.length ? runStatuses : undefined,
        repository_id: repositoryId,
        cursor,
        limit: 25,
      }),
    [search, states.join(','), runStatuses.join(','), repositoryId, cursor],
    { keepPreviousData: true },
  )

  const columns: ReadonlyArray<Column<PullRequestSummary>> = [
    {
      key: 'title',
      header: 'Pull request',
      render: (row) => (
        <span className="block min-w-0">
          <span className="text-muted">#{row.number}</span>{' '}
          <span className="text-content">{row.title}</span>
          <span className="mt-0.5 block">
            <Mono className="text-muted">{row.repository_full_name}</Mono>
          </span>
        </span>
      ),
    },
    {
      key: 'author',
      header: 'Author',
      priority: 'low',
      render: (row) => <span className="text-muted">{row.author_login || '—'}</span>,
    },
    {
      key: 'head',
      header: 'Head',
      priority: 'low',
      render: (row) => <Mono className="text-muted">{shortSha(row.head_sha)}</Mono>,
    },
    {
      key: 'review',
      header: 'Current review',
      render: (row) => (
        <span className="flex flex-wrap items-center gap-1">
          <RunStatusBadge status={row.current_run_status} />
          {row.current_run_limited_coverage ? <LimitedCoverageBadge /> : null}
          {row.review_run_count > 1 ? (
            <span
              className="text-[11px] text-muted"
              title="Total review attempts across every commit of this pull request."
            >
              {row.review_run_count} attempts
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: 'findings',
      header: 'Findings',
      align: 'right',
      render: (row) => (
        <span
          title={`${row.validated_findings} validated, ${row.rejected_claims} rejected claims, ${row.published_comments} published comments — for the current commit's review.`}
        >
          <span className={row.open_critical_high > 0 ? 'text-state-warn' : undefined}>
            {formatNumber(row.validated_findings)}
          </span>
          <span className="text-xs text-muted">
            {' '}
            / {formatNumber(row.published_comments)} posted
          </span>
        </span>
      ),
    },
    {
      key: 'updated',
      header: 'Updated',
      align: 'right',
      render: (row) => (
        <time dateTime={row.updated_at ?? undefined} title={row.updated_at ?? undefined}>
          {formatRelative(row.updated_at)}
        </time>
      ),
    },
  ]

  return (
    <div className="mx-auto max-w-[1180px]">
      <PageHeader
        title="Pull requests"
        subtitle="Each pull request with the review of its current commit. Earlier attempts are on the detail page."
      />

      <Card>
        <div className="mb-4 flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <SearchInput
              label="Search pull requests"
              value={search}
              placeholder="Filter by title…"
              onChange={(value) => filters.set('q', value)}
            />
            {filters.activeCount > 0 ? (
              <Button variant="subtle" onClick={filters.clear}>
                Clear filters
              </Button>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-6">
            <ChipFilter
              legend="Pull request state"
              options={PR_STATES}
              selected={states}
              onToggle={(value) => filters.toggle('state', value)}
            />
            <ChipFilter
              legend="Review outcome"
              options={RUN_STATES}
              selected={runStatuses}
              onToggle={(value) => filters.toggle('run_status', value)}
            />
          </div>
        </div>

        <AsyncSection
          query={query}
          skeleton={<SkeletonTable rows={8} columns={5} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title={
                filters.activeCount > 0
                  ? 'No pull requests match these filters'
                  : 'No pull requests yet'
              }
              hint={
                filters.activeCount > 0
                  ? 'Try removing a filter.'
                  : 'Open a pull request in a monitored repository and it will appear here once the webhook arrives.'
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
                caption="Pull requests"
                columns={columns}
                rows={data.items}
                rowKey={(row) => String(row.id)}
                onRowHref={(row) => `/pull-requests/${row.id}`}
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

      <p className="mt-3 text-xs text-muted">
        “Current review” is the newest non-superseded run for the pull request’s current head
        commit. A pull request whose head has not been reviewed shows <em>Not reviewed</em> — which
        is not a pass.{' '}
        <Link to="/review-runs" className="text-accent">
          Every attempt is listed under review runs.
        </Link>
      </p>
    </div>
  )
}
