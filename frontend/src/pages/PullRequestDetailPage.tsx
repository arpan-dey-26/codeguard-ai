import { Link, useParams } from 'react-router-dom'
import { getFindings, getPullRequest } from '@/api/endpoints'
import { AsyncSection } from '@/components/data/AsyncSection'
import {
  LimitedCoverageBadge,
  PublicationBadge,
  RunStatusBadge,
  SeverityBadge,
  ValidationBadge,
} from '@/components/domain/badges'
import { Card } from '@/components/ui/Card'
import { IconAlert } from '@/components/ui/icons'
import {
  Caveat,
  Definition,
  DefinitionList,
  EmptyState,
  ExternalLink,
  Mono,
  PageHeader,
  SectionHeader,
  Skeleton,
  SkeletonTable,
  StatusPill,
} from '@/components/ui/primitives'
import { useApiQuery } from '@/hooks/useApiQuery'
import type { ReviewRunSummary } from '@/types/api'
import { formatDateTime, formatDuration, formatNumber, formatRelative, shortSha } from '@/utils/format'
import { RUN_ERROR } from '@/utils/status'

/**
 * A pull request, its current review, and every earlier attempt.
 *
 * The run timeline is the point of the page: the most informative thing about
 * a pull request is usually that the first attempt found four issues and the
 * one after the fix found none. Showing only the latest run hides that story.
 * Stale attempts are labelled as such and are never presented as the current
 * result.
 */

export function PullRequestDetailPage() {
  const params = useParams<{ pullRequestId: string }>()
  const pullRequestId = Number(params.pullRequestId)
  const valid = Number.isFinite(pullRequestId) && pullRequestId > 0

  const detail = useApiQuery(() => getPullRequest(pullRequestId), [pullRequestId], {
    enabled: valid,
  })
  const findings = useApiQuery(
    () => getFindings({ pull_request_id: pullRequestId, limit: 50, include_total: true }),
    [pullRequestId],
    { enabled: valid },
  )

  if (!valid) {
    return (
      <EmptyState
        icon={<IconAlert size={17} />}
        title="Pull request not found"
        hint="This pull request id does not exist, or it belongs to an installation your account cannot see."
        action={
          <Link
            to="/pull-requests"
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1 text-sm text-muted transition-colors duration-instant ease-out hover:border-accent hover:text-content"
          >
            Back to pull requests
          </Link>
        }
      />
    )
  }

  return (
    <div className="mx-auto max-w-[1180px]">
      <AsyncSection
        query={detail}
        skeleton={<Skeleton className="h-24 w-full" />}
        placeholderHeading={<PageHeader eyebrow="Pull request" title="Pull request" />}
      >
        {(pr) => {
          const currentRun = pr.runs.find((run) => run.id === pr.current_run_id) ?? null
          const staleRuns = pr.runs.filter((run) => run.is_stale)

          return (
            <>
              <PageHeader
                title={`#${pr.number} ${pr.title}`}
                subtitle={
                  <span className="flex flex-wrap items-center gap-2">
                    <Link to={`/repositories/${pr.repository_id}`} className="hover:text-accent">
                      <Mono>{pr.repository_full_name}</Mono>
                    </Link>
                    <span>·</span>
                    <span>{pr.author_login || 'unknown author'}</span>
                    <StatusPill
                      label={pr.state}
                      glyph={pr.state === 'merged' ? '⬤' : pr.state === 'closed' ? '○' : '◍'}
                      className={pr.state === 'open' ? 'text-state-ok' : 'text-state-idle'}
                    />
                    {pr.is_draft ? (
                      <StatusPill label="Draft" glyph="◌" className="text-state-idle" />
                    ) : null}
                  </span>
                }
                actions={
                  <ExternalLink href={pr.html_url}>Open on GitHub</ExternalLink>
                }
              />

              <Card>
                <SectionHeader
                  title="Current review"
                  hint="The newest non-superseded run for this pull request's current head commit."
                />
                {currentRun ? (
                  <>
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <RunStatusBadge status={currentRun.status} />
                      {currentRun.limited_coverage ? <LimitedCoverageBadge /> : null}
                      <Link
                        to={`/review-runs/${currentRun.id}`}
                        className="text-xs text-accent hover:underline"
                      >
                        Review detail →
                      </Link>
                    </div>
                    <DefinitionList>
                      <Definition term="Head commit" mono>
                        {shortSha(pr.head_sha)}
                      </Definition>
                      <Definition term="Attempt">{currentRun.attempt}</Definition>
                      <Definition term="Duration">
                        {formatDuration(currentRun.duration_ms)}
                      </Definition>
                      <Definition term="Files reviewed">
                        {formatNumber(currentRun.files_analysed)} of{' '}
                        {formatNumber(currentRun.files_changed)}
                        {currentRun.files_skipped > 0 ? (
                          <span className="text-muted">
                            {' '}
                            ({currentRun.files_skipped} not analysed)
                          </span>
                        ) : null}
                      </Definition>
                      <Definition term="Validated findings">
                        {formatNumber(currentRun.findings_inline + currentRun.findings_summary)}
                      </Definition>
                      <Definition term="Published comments">
                        {formatNumber(currentRun.published_comments)}
                      </Definition>
                      <Definition term="Rejected claims">
                        {formatNumber(currentRun.rejected_claims)}
                      </Definition>
                      <Definition term="Finished">
                        <time
                          dateTime={currentRun.finished_at ?? undefined}
                          title={currentRun.finished_at ?? undefined}
                        >
                          {formatRelative(currentRun.finished_at)}
                        </time>
                      </Definition>
                      {currentRun.error_code ? (
                        <Definition term="Outcome detail">
                          <span className="text-state-warn">
                            {RUN_ERROR[currentRun.error_code] ?? currentRun.error_code}
                          </span>
                        </Definition>
                      ) : null}
                    </DefinitionList>
                    {currentRun.status === 'partial_success' ? (
                      <Caveat>
                        This review <strong>completed</strong> and its findings are stored, but
                        not all of them reached GitHub. Everything is listed below regardless
                        of publication state.
                      </Caveat>
                    ) : null}
                  </>
                ) : (
                  <EmptyState
                    title="This commit has not been reviewed"
                    hint={
                      <>
                        The current head is <Mono>{shortSha(pr.head_sha)}</Mono>.{' '}
                        {staleRuns.length > 0
                          ? 'Earlier commits were reviewed — those attempts are listed below and are not the current result.'
                          : 'A review starts when the webhook for this commit is processed.'}
                      </>
                    }
                  />
                )}
              </Card>

              <Card className="mt-4">
                <SectionHeader
                  title="Review history"
                  hint={`${pr.runs.length} attempt${pr.runs.length === 1 ? '' : 's'} across every commit of this pull request.`}
                />
                {pr.runs.length === 0 ? (
                  <EmptyState title="No review attempts yet" />
                ) : (
                  <ol className="space-y-2">
                    {pr.runs.map((run) => (
                      <RunRow key={run.id} run={run} isCurrent={run.id === pr.current_run_id} />
                    ))}
                  </ol>
                )}
                <Caveat>
                  Multiple attempts for one pull request are shown here in full, and are
                  counted as attempts — never as separate pull requests in any total.
                </Caveat>
              </Card>

              <Card className="mt-4">
                <SectionHeader
                  title="Findings"
                  hint="Every claim recorded for this pull request, validated or rejected."
                  actions={
                    <Link
                      to={`/findings?pull_request_id=${pr.id}`}
                      className="text-xs text-muted hover:text-content"
                    >
                      Open in explorer →
                    </Link>
                  }
                />
                <AsyncSection
                  query={findings}
                  skeleton={<SkeletonTable rows={5} columns={4} />}
                  isEmpty={(data) => data.items.length === 0}
                  empty={
                    <EmptyState
                      title="No findings recorded"
                      hint="Either the review found nothing, or it has not run for this pull request yet."
                    />
                  }
                >
                  {(data) => (
                    <ul className="divide-y divide-border/60">
                      {data.items.map((finding) => (
                        <li key={finding.id} className="py-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <SeverityBadge severity={finding.severity} />
                            <Link
                              to={`/findings/${finding.id}`}
                              className="min-w-0 flex-1 basis-full break-words text-sm hover:text-accent sm:basis-0 sm:truncate"
                            >
                              {finding.title}
                            </Link>
                            <ValidationBadge validation={finding.validation} />
                            <PublicationBadge isPublished={finding.is_published} />
                          </div>
                          <div className="mt-1 text-xs text-muted">
                            <Mono>{finding.file_path}</Mono>
                            {finding.resolved_line !== null ? (
                              <>
                                {' '}
                                <Mono>:{finding.resolved_line}</Mono>
                                {finding.anchor_offset !== null && finding.anchor_offset !== 0 ? (
                                  <span
                                    title={`The model claimed line ${finding.claimed_line}; the validator resolved line ${finding.resolved_line}.`}
                                  >
                                    {' '}
                                    (claimed {finding.claimed_line})
                                  </span>
                                ) : null}
                              </>
                            ) : (
                              <span> · claimed line {finding.claimed_line}, not resolved</span>
                            )}
                            {finding.qualified_name ? <> · {finding.qualified_name}</> : null}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </AsyncSection>
              </Card>

              <Card className="mt-4">
                <SectionHeader title="Pull request details" />
                <DefinitionList>
                  <Definition term="Head branch" mono>
                    {pr.head_ref || '—'}
                  </Definition>
                  <Definition term="Base branch" mono>
                    {pr.base_ref || '—'}
                  </Definition>
                  <Definition term="Head commit" mono>
                    {pr.head_sha}
                  </Definition>
                  <Definition term="Changed files">
                    {formatNumber(pr.changed_files_count)}
                    {pr.changed_files_truncated ? (
                      <span
                        className="text-state-warn"
                        title="GitHub capped the file list for this pull request, so some files were never seen."
                      >
                        {' '}
                        (truncated by GitHub)
                      </span>
                    ) : null}
                  </Definition>
                  <Definition term="Opened">{formatDateTime(pr.opened_at)}</Definition>
                  <Definition term="Last synchronised">
                    {formatDateTime(pr.last_synced_at)}
                  </Definition>
                </DefinitionList>
              </Card>
            </>
          )
        }}
      </AsyncSection>
    </div>
  )
}

function RunRow({ run, isCurrent }: { run: ReviewRunSummary; isCurrent: boolean }) {
  return (
    <li className="rounded-md border border-border px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/review-runs/${run.id}`} className="text-sm hover:text-accent">
          Attempt {run.attempt}
        </Link>
        <Mono className="text-muted">{shortSha(run.head_sha)}</Mono>
        <RunStatusBadge status={run.status} isStale={run.is_stale} />
        {run.limited_coverage ? <LimitedCoverageBadge /> : null}
        {isCurrent ? (
          <StatusPill
            label="Current"
            glyph="★"
            className="text-accent"
            title="This run covers the pull request's current head commit."
          />
        ) : null}
        <span className="ml-auto text-xs text-muted">
          <time dateTime={run.created_at ?? undefined} title={run.created_at ?? undefined}>
            {formatRelative(run.created_at)}
          </time>
        </span>
      </div>
      <p className="mt-1 text-xs text-muted">
        {formatNumber(run.claims_emitted)} claims ·{' '}
        {formatNumber(run.findings_inline + run.findings_summary)} validated ·{' '}
        {formatNumber(run.rejected_claims)} rejected ·{' '}
        {formatNumber(run.published_comments)} published · {formatDuration(run.duration_ms)}
        {run.error_code ? (
          <span className="text-state-warn"> · {RUN_ERROR[run.error_code] ?? run.error_code}</span>
        ) : null}
      </p>
    </li>
  )
}
