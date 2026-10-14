import { Link, useParams } from 'react-router-dom'
import { BarList } from '@/charts/BarList'
import { getCoverage, getFindingsBySeverity, getPullRequests, getRepository } from '@/api/endpoints'
import { AsyncSection } from '@/components/data/AsyncSection'
import { RunStatusBadge } from '@/components/domain/badges'
import { Card } from '@/components/ui/Card'
import {
  Caveat,
  Definition,
  DefinitionList,
  EmptyState,
  Mono,
  PageHeader,
  SectionHeader,
  Skeleton,
  SkeletonChart,
  SkeletonTable,
  StatGrid,
  StatTile,
  StatusPill,
} from '@/components/ui/primitives'
import { useApiQuery } from '@/hooks/useApiQuery'
import { rangeParamsFrom } from '@/hooks/useUrlFilters'
import type { Severity } from '@/types/domain'
import { formatDateTime, formatNumber, formatRelative, humanise, shortSha } from '@/utils/format'
import { SEVERITY } from '@/utils/severity'
import { SKIP_REASON } from '@/utils/status'

export function RepositoryDetailPage() {
  const params = useParams<{ repositoryId: string }>()
  const repositoryId = Number(params.repositoryId)
  const valid = Number.isFinite(repositoryId) && repositoryId > 0
  const range = rangeParamsFrom('30')

  const detail = useApiQuery(() => getRepository(repositoryId), [repositoryId], {
    enabled: valid,
  })
  const severity = useApiQuery(
    () => getFindingsBySeverity({ ...range, repository_id: repositoryId }),
    [repositoryId],
    { enabled: valid },
  )
  const coverage = useApiQuery(
    () => getCoverage({ ...range, repository_id: repositoryId }),
    [repositoryId],
    { enabled: valid },
  )
  const pullRequests = useApiQuery(
    () => getPullRequests({ repository_id: repositoryId, limit: 8 }),
    [repositoryId],
    { enabled: valid },
  )

  if (!valid) {
    return (
      <EmptyState
        title="Repository not found"
        hint={
          <Link to="/repositories" className="text-accent">
            Back to repositories
          </Link>
        }
      />
    )
  }

  return (
    <div className="mx-auto max-w-[1180px]">
      <AsyncSection
        query={detail}
        skeleton={<Skeleton className="h-16 w-full" />}
        placeholderHeading={<PageHeader eyebrow="Repository" title="Repository" />}
      >
        {(repository) => (
          <>
            <PageHeader
              title={repository.full_name}
              subtitle={
                <span className="flex flex-wrap items-center gap-2">
                  <span>
                    {repository.installation_account_type} · {repository.installation_account}
                  </span>
                  <Mono className="text-muted">{repository.default_branch}</Mono>
                  {repository.is_private ? (
                    <StatusPill label="Private" glyph="◆" className="text-muted" />
                  ) : null}
                  {!repository.is_active ? (
                    <StatusPill
                      label="Not monitored"
                      glyph="○"
                      className="text-state-idle"
                      title="Connected, but pull requests in this repository are not reviewed."
                    />
                  ) : null}
                  {repository.installation_suspended ? (
                    <StatusPill
                      label="Installation suspended"
                      glyph="▲"
                      className="text-state-bad"
                      title="The GitHub App installation is suspended, so no new events arrive."
                    />
                  ) : null}
                </span>
              }
            />

            <StatGrid>
              <StatTile
                label="Pull requests"
                value={formatNumber(repository.pull_requests_total)}
                definition="Pull requests observed in this repository, whether or not they were reviewed."
              />
              <StatTile
                label="PRs reviewed"
                value={formatNumber(repository.pull_requests_reviewed)}
                definition="Distinct pull requests with at least one completed review. Retries count once."
              />
              <StatTile
                label="Review attempts"
                value={formatNumber(repository.review_runs_total)}
                definition="Every review run started for this repository, including retries and superseded attempts."
                footnote={`${repository.review_runs_partial} partial · ${repository.review_runs_failed} failed`}
              />
              <StatTile
                label="Validated findings"
                value={formatNumber(repository.validated_findings)}
                definition="Model claims that passed every structural gate. Not independently proven defects."
              />
              <StatTile
                label="Rejected claims"
                value={formatNumber(repository.rejected_claims)}
                definition="Model claims that failed a structural validation gate. Not confirmed false positives."
              />
              <StatTile
                label="Published comments"
                value={formatNumber(repository.published_comments)}
                definition="Review comments GitHub confirmed for this repository."
              />
            </StatGrid>

            <Card className="mt-4">
              <SectionHeader title="Details" />
              <DefinitionList>
                <Definition term="Owner">{repository.owner_login}</Definition>
                <Definition term="Default branch" mono>
                  {repository.default_branch}
                </Definition>
                <Definition term="Primary language">
                  {repository.primary_language ?? 'Not detected'}
                </Definition>
                <Definition term="Last review">
                  <time
                    dateTime={repository.latest_review_at ?? undefined}
                    title={repository.latest_review_at ?? undefined}
                  >
                    {formatRelative(repository.latest_review_at)}
                  </time>
                </Definition>
                <Definition term="Connected">{formatDateTime(repository.created_at)}</Definition>
                <Definition term="Open findings">
                  {formatNumber(repository.open_findings)}
                </Definition>
              </DefinitionList>
            </Card>
          </>
        )}
      </AsyncSection>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionHeader title="Validated findings by severity" hint="Last 30 days" />
          <AsyncSection query={severity} skeleton={<SkeletonChart />}>
            {(data) => (
              <BarList
                title="Validated findings by severity"
                items={data.map((bucket) => ({
                  key: bucket.key,
                  label: SEVERITY[bucket.key as Severity]?.label ?? humanise(bucket.key),
                  value: bucket.count,
                }))}
              />
            )}
          </AsyncSection>
        </Card>

        <Card>
          <SectionHeader title="Review coverage" hint="Last 30 days" />
          <AsyncSection query={coverage} skeleton={<SkeletonChart />}>
            {(data) => (
              <>
                <dl className="grid grid-cols-3 gap-3 text-sm">
                  <CoverageFigure label="Files changed" value={data.files_changed} />
                  <CoverageFigure label="Analysed" value={data.files_analysed} />
                  <CoverageFigure label="Not analysed" value={data.files_skipped} />
                </dl>
                {data.skip_reasons.length > 0 ? (
                  <div className="mt-4">
                    <BarList
                      title="Why files were not analysed"
                      items={data.skip_reasons.map((bucket) => ({
                        key: bucket.key,
                        label: SKIP_REASON[bucket.key] ?? humanise(bucket.key),
                        value: bucket.count,
                        className: 'bg-state-idle',
                      }))}
                    />
                  </div>
                ) : null}
                <Caveat>
                  These figures are not reduced to a coverage percentage. A file can be absent
                  because it is binary, because no parser is installed for its language, or because
                  a budget stopped the build — and a single number would hide which.
                  {data.runs_over_budget > 0 ? (
                    <>
                      {' '}
                      {data.runs_over_budget} of {data.runs_total} reviews hit a budget.
                    </>
                  ) : null}
                </Caveat>
              </>
            )}
          </AsyncSection>
        </Card>
      </div>

      <Card className="mt-4">
        <SectionHeader
          title="Recent pull requests"
          actions={
            <Link
              to={`/pull-requests?repository_id=${repositoryId}`}
              className="text-xs text-muted hover:text-content"
            >
              All pull requests →
            </Link>
          }
        />
        <AsyncSection
          query={pullRequests}
          skeleton={<SkeletonTable rows={5} columns={4} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No pull requests yet"
              hint="Open a pull request in this repository to start a review."
            />
          }
        >
          {(data) => (
            <ul className="divide-y divide-border/60">
              {data.items.map((pr) => (
                <li key={pr.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <Link
                    to={`/pull-requests/${pr.id}`}
                    className="min-w-0 flex-1 truncate hover:text-accent"
                  >
                    <span className="text-muted">#{pr.number}</span> {pr.title}
                  </Link>
                  <Mono className="hidden text-muted sm:inline">{shortSha(pr.head_sha)}</Mono>
                  <RunStatusBadge status={pr.current_run_status} />
                  <time
                    dateTime={pr.updated_at ?? undefined}
                    title={pr.updated_at ?? undefined}
                    className="w-24 text-right text-muted"
                  >
                    {formatRelative(pr.updated_at)}
                  </time>
                </li>
              ))}
            </ul>
          )}
        </AsyncSection>
      </Card>
    </div>
  )
}

function CoverageFigure({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold tabular-nums text-content">
        {formatNumber(value)}
      </dd>
    </div>
  )
}
