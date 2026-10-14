import { Link } from 'react-router-dom'
import { getProcessingHealth, getReviewRuns } from '@/api/endpoints'
import { AsyncSection } from '@/components/data/AsyncSection'
import { LimitedCoverageBadge, RunStatusBadge } from '@/components/domain/badges'
import { BarList } from '@/charts/BarList'
import { Card } from '@/components/ui/Card'
import {
  Button,
  Caveat,
  ChipFilter,
  EmptyState,
  Mono,
  PageHeader,
  SectionHeader,
  Select,
  SkeletonStatGrid,
  SkeletonTable,
  StatGrid,
  StatTile,
} from '@/components/ui/primitives'
import { Table, type Column } from '@/components/ui/Table'
import { Pager } from '@/pages/RepositoriesPage'
import { useApiQuery } from '@/hooks/useApiQuery'
import { RANGE_OPTIONS, rangeParamsFrom, useUrlFilters } from '@/hooks/useUrlFilters'
import type { ReviewRunSummary } from '@/types/api'
import { formatDuration, formatNumber, formatRelative, humanise, shortSha } from '@/utils/format'
import { RUN_ERROR } from '@/utils/status'

const RUN_STATES = [
  { value: 'succeeded', label: 'Completed' },
  { value: 'partial_success', label: 'Partial' },
  { value: 'failed', label: 'Failed' },
  { value: 'superseded', label: 'Superseded' },
  { value: 'skipped', label: 'Skipped' },
]

/**
 * Review history: every attempt, in time order.
 *
 * Superseded attempts are included by default and labelled. They are part of
 * the record — hiding them everywhere would erase the fact that a review was
 * started and overtaken — and they are excluded from the reliability figures
 * above the table, which is where their inclusion would be misleading.
 */

export function ReviewRunsPage() {
  const filters = useUrlFilters()
  const days = filters.get('range') ?? '30'
  const range = rangeParamsFrom(days)
  const statuses = filters.getAll('run_status')
  const repositoryId = filters.getNumber('repository_id')
  const cursor = filters.cursor

  const health = useApiQuery(() => getProcessingHealth(range), [days], {
    keepPreviousData: true,
  })
  const runs = useApiQuery(
    () =>
      getReviewRuns({
        ...range,
        run_status: statuses.length ? statuses : undefined,
        repository_id: repositoryId,
        cursor,
        limit: 25,
      }),
    [days, statuses.join(','), repositoryId, cursor],
    { keepPreviousData: true },
  )

  const columns: ReadonlyArray<Column<ReviewRunSummary>> = [
    {
      key: 'pr',
      header: 'Pull request',
      render: (row) => (
        <span className="block min-w-0">
          <span className="text-muted">#{row.pull_request_number}</span>{' '}
          <span className="text-content">{row.title ?? '—'}</span>
          <span className="mt-0.5 block">
            <Mono className="text-muted">{row.repository_full_name ?? '—'}</Mono>
          </span>
        </span>
      ),
    },
    {
      key: 'commit',
      header: 'Commit',
      priority: 'low',
      render: (row) => (
        <span>
          <Mono className="text-muted">{shortSha(row.head_sha)}</Mono>
          <span className="block text-xs text-muted">attempt {row.attempt}</span>
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Outcome',
      render: (row) => (
        <span className="flex flex-wrap items-center gap-1">
          <RunStatusBadge status={row.status} isStale={row.is_stale} />
          {row.limited_coverage ? <LimitedCoverageBadge /> : null}
        </span>
      ),
    },
    {
      key: 'funnel',
      header: 'Claims → published',
      align: 'right',
      priority: 'low',
      render: (row) => (
        <span
          title={`${row.claims_emitted} claims emitted, ${row.claims_dropped} unparseable, ${row.findings_inline + row.findings_summary} validated, ${row.rejected_claims} rejected, ${row.published_comments} published.`}
        >
          {formatNumber(row.claims_emitted)} → {formatNumber(row.published_comments)}
        </span>
      ),
    },
    {
      key: 'duration',
      header: 'Duration',
      align: 'right',
      priority: 'low',
      render: (row) => formatDuration(row.duration_ms),
    },
    {
      key: 'when',
      header: 'Started',
      align: 'right',
      render: (row) => (
        <time dateTime={row.created_at ?? undefined} title={row.created_at ?? undefined}>
          {formatRelative(row.created_at)}
        </time>
      ),
    },
  ]

  return (
    <div className="mx-auto max-w-[1180px]">
      <PageHeader
        title="Review runs"
        subtitle="Every review attempt, including retries and attempts a newer commit overtook."
        actions={
          <Select
            label="Range"
            value={days}
            options={RANGE_OPTIONS.map((option) => ({ ...option }))}
            onChange={(value) => filters.set('range', value)}
          />
        }
      />

      <AsyncSection
        query={health}
        skeleton={
          <SkeletonStatGrid tiles={4} />
        }
      >
        {(data) => (
          <>
            <StatGrid>
              <StatTile
                label="Attempts"
                value={formatNumber(data.runs_total)}
                definition="Review runs started in this range, of every outcome."
              />
              <StatTile
                label="Completed"
                value={formatNumber(data.succeeded)}
                tone="ok"
                definition="Runs that finished and published every validated finding they had."
              />
              <StatTile
                label="Partial"
                value={formatNumber(data.partial_success)}
                tone={data.partial_success > 0 ? 'warn' : 'neutral'}
                definition="Runs that finished and stored their findings, but did not get all of them onto GitHub."
              />
              <StatTile
                label="Failed"
                value={formatNumber(data.failed)}
                tone={data.failed > 0 ? 'bad' : 'neutral'}
                definition="Runs that did not finish."
              />
              <StatTile
                label="Superseded"
                value={formatNumber(data.superseded)}
                definition="Runs overtaken by a newer commit before they finished. Excluded from the completion rate — being pushed over is not a reliability problem."
              />
              <StatTile
                label="Median duration"
                value={formatDuration(data.duration_p50_ms)}
                definition="Median wall-clock duration of runs that recorded one."
                footnote={`p95 ${formatDuration(data.duration_p95_ms)}`}
              />
            </StatGrid>

            {data.error_breakdown.length > 0 ? (
              <Card className="mt-4">
                <SectionHeader title="Why runs did not complete cleanly" />
                <BarList
                  title="Run outcomes by error code"
                  items={data.error_breakdown.map((bucket) => ({
                    key: bucket.key,
                    label: RUN_ERROR[bucket.key] ?? humanise(bucket.key),
                    value: bucket.count,
                    className: 'bg-state-warn',
                  }))}
                />
              </Card>
            ) : null}
          </>
        )}
      </AsyncSection>

      <Card className="mt-4">
        <div className="mb-4 flex flex-wrap items-end gap-4">
          <ChipFilter
            legend="Outcome"
            options={RUN_STATES}
            selected={statuses}
            onToggle={(value) => filters.toggle('run_status', value)}
          />
          {filters.activeCount > 0 ? (
            <Button variant="subtle" onClick={filters.clear}>
              Clear filters
            </Button>
          ) : null}
        </div>

        <AsyncSection
          query={runs}
          skeleton={<SkeletonTable rows={8} columns={5} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title={
                filters.activeCount > 0 ? 'No runs match these filters' : 'No reviews yet'
              }
              hint={
                filters.activeCount > 0
                  ? 'Try a wider range or a different outcome.'
                  : 'Open or update a pull request in a monitored repository to start a review.'
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
                caption="Review runs"
                columns={columns}
                rows={data.items}
                rowKey={(row) => row.id}
                onRowHref={(row) => `/review-runs/${row.id}`}
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

        <Caveat>
          Several attempts can exist for one pull request — a retry, or a new commit. They
          are listed individually here and counted as attempts. The “pull requests
          reviewed” figure on the{' '}
          <Link to="/" className="text-accent">
            dashboard
          </Link>{' '}
          counts distinct pull requests instead, so the two numbers differ on purpose.
        </Caveat>
      </Card>
    </div>
  )
}
