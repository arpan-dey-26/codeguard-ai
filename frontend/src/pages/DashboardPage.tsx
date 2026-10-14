import { Link } from 'react-router-dom'
import { BarList } from '@/charts/BarList'
import { TrendChart } from '@/charts/TrendChart'
import { ValidationFunnel } from '@/charts/ValidationFunnel'
import { AsyncSection } from '@/components/data/AsyncSection'
import { RunStatusBadge } from '@/components/domain/badges'
import { Card } from '@/components/ui/Card'
import {
  Caveat,
  EmptyState,
  Mono,
  PageHeader,
  SectionHeader,
  Select,
  SkeletonChart,
  SkeletonStatGrid,
  StatGrid,
  StatTile,
} from '@/components/ui/primitives'
import { useApiQuery } from '@/hooks/useApiQuery'
import { RANGE_OPTIONS, rangeParamsFrom, useUrlFilters } from '@/hooks/useUrlFilters'
import {
  getFindingsByCategory,
  getFindingsBySeverity,
  getOverview,
  getRecentActivity,
  getRejectionReasons,
  getTrends,
} from '@/api/endpoints'
import { formatDuration, formatNumber, formatRelative, humanise, shortSha } from '@/utils/format'
import { SEVERITY } from '@/utils/severity'
import type { Severity } from '@/types/domain'

/**
 * The thirty-second answer to "is anything wrong?".
 *
 * Every tile carries its own definition, because the metrics on this page are
 * genuinely easy to confuse: "pull requests reviewed" is a count of *pull
 * requests*, "review attempts" is a count of *runs*, and three attempts at one
 * pull request contribute 1 and 3 respectively. The dashboard states which is
 * which rather than leaving a reader to assume.
 *
 * Each panel is its own async section, so a slow aggregate degrades one card
 * rather than the page.
 */

export function DashboardPage() {
  const filters = useUrlFilters()
  const days = filters.get('range') ?? '30'
  const range = rangeParamsFrom(days)

  const overview = useApiQuery(() => getOverview(range), [days], { keepPreviousData: true })
  const trends = useApiQuery(() => getTrends({ ...range, bucket: 'day' }), [days], {
    keepPreviousData: true,
  })
  const severity = useApiQuery(() => getFindingsBySeverity(range), [days], {
    keepPreviousData: true,
  })
  const category = useApiQuery(() => getFindingsByCategory(range), [days], {
    keepPreviousData: true,
  })
  const rejections = useApiQuery(() => getRejectionReasons(range), [days], {
    keepPreviousData: true,
  })
  const activity = useApiQuery(() => getRecentActivity(8), [], { keepPreviousData: true })

  return (
    <div className="mx-auto max-w-[1180px]">
      <PageHeader
        title="Dashboard"
        subtitle="Review activity across every repository this installation can see."
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
        query={overview}
        skeleton={<SkeletonStatGrid tiles={6} />}
      >
        {(data) => (
          <>
            <StatGrid>
              <StatTile
                label="Repositories"
                value={data.repositories_active}
                format={formatNumber}
                index={0}
                definition="Repositories currently marked active. Inactive repositories are still visible but are not reviewed."
                footnote={`${data.repositories_visible} visible in total`}
              />
              <StatTile
                label="Pull requests reviewed"
                value={data.pull_requests_reviewed}
                format={formatNumber}
                index={1}
                definition="Distinct pull requests with at least one completed review in this range. Several attempts at one pull request count once."
              />
              <StatTile
                label="Review attempts"
                value={data.runs.total}
                format={formatNumber}
                index={2}
                definition="Review runs started in this range, including retries and attempts that were superseded. Deliberately a different number from pull requests reviewed."
                footnote={`${data.runs.superseded} superseded`}
              />
              <StatTile
                label="Validated findings"
                value={data.funnel.validated_findings}
                format={formatNumber}
                index={3}
                definition="Model claims that passed every structural validation gate — anchored to the reviewed source at the reviewed commit. Not independently proven defects."
              />
              <StatTile
                label="Published comments"
                value={data.funnel.published_comments}
                format={formatNumber}
                index={4}
                definition="Review comments GitHub confirmed. Read from stored publication state, never inferred."
              />
              <StatTile
                label="Open critical / high"
                value={data.open_high_severity}
                format={formatNumber}
                index={5}
                tone={data.open_high_severity > 0 ? 'warn' : 'neutral'}
                definition="Open validated findings at critical or high severity, created in this range."
                footnote={`${data.open_findings} open in total`}
              />
            </StatGrid>

            {data.runs.total === 0 ? (
              <div className="mt-4">
                <EmptyState
                  title="No reviews in this range"
                  hint={
                    data.repositories_visible === 0
                      ? 'No repositories are connected yet. Install the GitHub App on a repository to begin.'
                      : 'Open or update a pull request in a monitored repository, or widen the range above.'
                  }
                />
              </div>
            ) : null}

            {data.runs.partial_success > 0 || data.runs.failed > 0 || data.runs.limited_coverage > 0 ? (
              <Card className="mt-4">
                <SectionHeader
                  title="Needs attention"
                  hint="Outcomes that are not a clean pass."
                />
                <ul className="grid gap-2 text-sm sm:grid-cols-3">
                  <AttentionRow
                    count={data.runs.failed}
                    label="failed"
                    detail="did not complete"
                    to="/review-runs?run_status=failed"
                  />
                  <AttentionRow
                    count={data.runs.partial_success}
                    label="partial"
                    detail="findings stored, not all published"
                    to="/review-runs?run_status=partial_success"
                  />
                  <AttentionRow
                    count={data.runs.limited_coverage}
                    label="limited coverage"
                    detail="a budget stopped the context build"
                    to="/review-runs"
                  />
                </ul>
              </Card>
            ) : null}
          </>
        )}
      </AsyncSection>

      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <SectionHeader title="Review activity" hint={`Attempts per day, UTC`} />
          <AsyncSection query={trends} skeleton={<SkeletonChart />}>
            {(data) => (
              <TrendChart
                points={data.points}
                bucket={data.bucket}
                timezone={data.timezone}
              />
            )}
          </AsyncSection>
        </Card>

        <Card>
          <SectionHeader
            title="Validation funnel"
            hint="What the model said, and what survived verification."
          />
          <AsyncSection query={overview} skeleton={<SkeletonChart className="h-52" />}>
            {(data) => <ValidationFunnel funnel={data.funnel} />}
          </AsyncSection>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card>
          <SectionHeader title="Validated findings by severity" />
          <AsyncSection query={severity} skeleton={<SkeletonChart />}>
            {(data) => (
              <BarList
                title="Validated findings by severity"
                items={data.map((bucket) => ({
                  key: bucket.key,
                  label: SEVERITY[bucket.key as Severity]?.label ?? humanise(bucket.key),
                  value: bucket.count,
                  className: severityBarClass(bucket.key),
                }))}
              />
            )}
          </AsyncSection>
        </Card>

        <Card>
          <SectionHeader title="Validated findings by category" />
          <AsyncSection query={category} skeleton={<SkeletonChart />}>
            {(data) => (
              <BarList
                title="Validated findings by category"
                items={data.map((bucket) => ({
                  key: bucket.key,
                  label: humanise(bucket.key),
                  value: bucket.count,
                }))}
              />
            )}
          </AsyncSection>
        </Card>

        <Card>
          <SectionHeader
            title="Rejected claims by gate"
            hint="Why structural validation refused to publish a claim."
          />
          <AsyncSection query={rejections} skeleton={<SkeletonChart />}>
            {(data) => (
              <>
                <BarList
                  title="Rejected claims by validation gate"
                  emptyLabel="No claims were rejected in this range."
                  items={data.map((bucket) => ({
                    key: bucket.key,
                    label: humanise(bucket.key),
                    value: bucket.count,
                    className: 'bg-state-warn',
                  }))}
                />
                <Caveat>
                  These are <strong>structural</strong> rejections: the claim could not be
                  anchored to the reviewed source. They are not confirmed false positives —
                  no semantic evaluation has been run.
                </Caveat>
              </>
            )}
          </AsyncSection>
        </Card>
      </div>

      <Card className="mt-4">
        <SectionHeader
          title="Recent review attempts"
          actions={
            <Link to="/review-runs" className="text-xs text-muted hover:text-content">
              All runs →
            </Link>
          }
        />
        <AsyncSection
          query={activity}
          skeleton={<SkeletonChart />}
          isEmpty={(data) => data.length === 0}
          empty={
            <EmptyState
              title="No reviews yet"
              hint="Open or update a pull request in a monitored repository to start a review."
            />
          }
        >
          {(data) => (
            <ul className="divide-y divide-border/60">
              {data.map((entry) => (
                <li
                  key={entry.id}
                  className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
                >
                  <div className="flex min-w-0 flex-1 basis-full items-center gap-2 sm:basis-0">
                    <Link
                      to={`/review-runs/${entry.id}`}
                      className="min-w-0 break-words hover:text-accent sm:truncate"
                    >
                      <Mono className="text-muted">{entry.repository_full_name}</Mono>{' '}
                      <span className="text-muted">#{entry.pull_request_number}</span>{' '}
                      <span className="text-content">{entry.title}</span>
                    </Link>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <Mono className="hidden text-muted sm:inline">
                      {shortSha(entry.head_sha)}
                    </Mono>
                    <RunStatusBadge status={entry.status} isStale={entry.is_stale} />
                    <span className="hidden w-16 text-right tabular-nums text-muted sm:inline">
                      {formatDuration(entry.duration_ms)}
                    </span>
                    <time
                      dateTime={entry.created_at ?? undefined}
                      title={entry.created_at ?? undefined}
                      className="w-24 text-right text-muted"
                    >
                      {formatRelative(entry.created_at)}
                    </time>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </AsyncSection>
      </Card>
    </div>
  )
}

function AttentionRow({
  count,
  label,
  detail,
  to,
}: {
  count: number
  label: string
  detail: string
  to: string
}) {
  return (
    <li>
      <Link
        to={to}
        className="block rounded-md border border-border px-3 py-2 hover:border-accent"
      >
        <span className="tabular-nums text-content">{count}</span>{' '}
        <span className="text-content">{label}</span>
        <span className="block text-xs text-muted">{detail}</span>
      </Link>
    </li>
  )
}

/**
 * The bar colour for a severity bucket.
 *
 * Reads `SEVERITY` rather than repeating the five class names (v0.6.2): this
 * page's private copy of the map was, by accident, the only reason Tailwind
 * emitted `bg-severity-*` at all, and the finding card's rail was relying on it
 * from another file. One source of truth, spelled out in `utils/severity.ts`.
 *
 * `key` is a string because the endpoint's bucket keys are not typed as
 * `Severity`; an unrecognised key falls back to a neutral series colour rather
 * than rendering an invisible bar.
 */
function severityBarClass(key: string): string {
  return SEVERITY[key as Severity]?.fillClassName ?? 'bg-series-1'
}
