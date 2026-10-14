import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getFindings, getReviewRun } from '@/api/endpoints'
import { ValidationFunnel } from '@/charts/ValidationFunnel'
import { AnalysisState } from '@/components/domain/AnalysisState'
import { FindingCard } from '@/components/domain/FindingCard'
import { AsyncSection } from '@/components/data/AsyncSection'
import { LimitedCoverageBadge, RunStatusBadge } from '@/components/domain/badges'
import { Card } from '@/components/ui/Card'
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
  SkeletonCards,
  StatusPill,
} from '@/components/ui/primitives'
import { Table, type Column } from '@/components/ui/Table'
import { useApiQuery } from '@/hooks/useApiQuery'
import type { ChangedFileSummary } from '@/types/api'
import { formatDuration, formatNumber, formatRelative, humanise, shortSha } from '@/utils/format'
import { RUN_ERROR, SKIP_REASON } from '@/utils/status'

/**
 * The page that makes the system explainable.
 *
 * It answers, on one screen: what the model said, what survived verification,
 * what reached GitHub, and which files were never looked at. That is the
 * difference between a review tool and a black box — and it is the screen
 * where careless wording would do the most damage, so the funnel carries its
 * own caveat and every count is named for exactly what it counts.
 */

export function ReviewRunPage() {
  const params = useParams<{ reviewRunId: string }>()
  const reviewRunId = params.reviewRunId ?? ''

  const detail = useApiQuery(() => getReviewRun(reviewRunId), [reviewRunId], {
    enabled: reviewRunId !== '',
  })
  const findings = useApiQuery(
    () => getFindings({ review_run_id: reviewRunId, limit: 100 }),
    [reviewRunId],
    { enabled: reviewRunId !== '' },
  )

  // Which file the pointer is implicitly on, via a hovered finding. Presentation
  // only — it highlights a row in the changed-files table so the relationship
  // between a finding and its file is visible without scrolling between them.
  const [focusedFile, setFocusedFile] = useState<string | null>(null)

  if (!reviewRunId) {
    return (
      <EmptyState
        title="Review not found"
        hint={<Link to="/review-runs" className="text-accent">Back to review runs</Link>}
      />
    )
  }

  const fileColumns: ReadonlyArray<Column<ChangedFileSummary>> = [
    {
      key: 'path',
      header: 'File',
      render: (row) => (
        <span className="block min-w-0">
          <Mono className="break-all">{row.file_path}</Mono>
          {row.previous_path ? (
            <span className="block text-xs text-muted">renamed from {row.previous_path}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Change',
      priority: 'low',
      render: (row) => <span className="text-muted">{humanise(row.change_status)}</span>,
    },
    {
      key: 'lines',
      header: '+ / −',
      align: 'right',
      priority: 'low',
      render: (row) => (
        <span className="text-muted">
          <span className="text-state-ok">+{row.additions}</span>{' '}
          <span className="text-state-bad">−{row.deletions}</span>
        </span>
      ),
    },
    {
      key: 'language',
      header: 'Language',
      priority: 'low',
      render: (row) => <span className="text-muted">{row.language ?? '—'}</span>,
    },
    {
      key: 'analysed',
      header: 'Analysed',
      render: (row) =>
        row.is_analysed ? (
          <StatusPill label="Analysed" glyph="✓" className="text-state-ok" />
        ) : (
          <StatusPill
            label={row.skip_reason ? (SKIP_REASON[row.skip_reason] ?? humanise(row.skip_reason)) : 'Not analysed'}
            glyph="○"
            className="text-state-idle"
            title="This file was not analysed, so no finding could be anchored inside it."
          />
        ),
    },
    {
      key: 'findings',
      header: 'Findings',
      align: 'right',
      render: (row) => formatNumber(row.validated_findings),
    },
  ]

  return (
    <div className="mx-auto max-w-[1180px]">
      <AsyncSection
        query={detail}
        skeleton={<Skeleton className="h-24 w-full" />}
        // The real header is built from the fetched run; this keeps the page
        // from being headingless while it loads or when the id does not
        // resolve (v0.6.2).
        placeholderHeading={<PageHeader eyebrow="Review" title="Review run" />}
      >
        {(run) => (
          <>
            <PageHeader
              title={`Review of #${run.pull_request_number}`}
              subtitle={
                <span className="flex flex-wrap items-center gap-2">
                  <Link to={`/repositories/${run.repository_id}`} className="hover:text-accent">
                    <Mono>{run.repository_full_name}</Mono>
                  </Link>
                  <span>·</span>
                  <Link to={`/pull-requests/${run.pull_request_id}`} className="hover:text-accent">
                    {run.pull_request_title}
                  </Link>
                </span>
              }
              actions={
                <ExternalLink href={run.pull_request_url}>Open on GitHub</ExternalLink>
              }
            />

            <Card>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <RunStatusBadge status={run.status} isStale={run.is_stale} />
                {run.limited_coverage ? <LimitedCoverageBadge /> : null}
                {run.model_output_truncated ? (
                  <StatusPill
                    label="Model output truncated"
                    glyph="⋯"
                    className="text-state-warn"
                    title="The model hit its output token limit. The findings that were parsed are real, but the response was cut off."
                  />
                ) : null}
              </div>

              {run.status === 'queued' || run.status === 'running' ? (
                <AnalysisState
                  status={run.status}
                  className="mb-3 border-accent/30 bg-accent/[0.04]"
                />
              ) : null}

              {run.is_stale ? (
                <Caveat>
                  This review covered commit <Mono>{shortSha(run.head_sha)}</Mono>, which is no
                  longer the pull request’s head. It is <strong>history</strong>, not the
                  current result.
                </Caveat>
              ) : null}

              {run.status === 'partial_success' ? (
                <Caveat>
                  The review <strong>completed</strong> and every finding below is stored, but
                  not all of them were published to GitHub
                  {run.error_code ? <> — {RUN_ERROR[run.error_code] ?? run.error_code}</> : null}.
                  This is not a clean pass and not a failure.
                </Caveat>
              ) : null}

              {run.status === 'failed' && run.error_detail ? (
                <Caveat>
                  {RUN_ERROR[run.error_code ?? ''] ?? 'The review did not complete'} —{' '}
                  {run.error_detail}
                </Caveat>
              ) : null}

              <DefinitionList>
                <Definition term="Commit reviewed" mono>
                  {shortSha(run.head_sha)}
                </Definition>
                <Definition term="Attempt">{run.attempt}</Definition>
                <Definition term="Trigger">{humanise(run.trigger)}</Definition>
                <Definition term="Duration">{formatDuration(run.duration_ms)}</Definition>
                <Definition term="Started">
                  <time dateTime={run.started_at ?? undefined} title={run.started_at ?? undefined}>
                    {formatRelative(run.started_at)}
                  </time>
                </Definition>
                <Definition term="Model">{run.model_name ?? 'Not recorded'}</Definition>
                <Definition term="Prompt version" mono>
                  {run.prompt_version ?? '—'}
                </Definition>
                <Definition term="Tokens">
                  {run.input_tokens !== null || run.output_tokens !== null ? (
                    <>
                      {formatNumber(run.input_tokens)} in / {formatNumber(run.output_tokens)} out
                    </>
                  ) : (
                    'Not recorded'
                  )}
                </Definition>
                <Definition term="Model latency">{formatDuration(run.ai_latency_ms)}</Definition>
              </DefinitionList>
            </Card>

            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <Card>
                <SectionHeader
                  title="Validation funnel"
                  hint="What the model claimed, and what survived verification."
                />
                <ValidationFunnel funnel={run.funnel} />
              </Card>

              <Card>
                <SectionHeader title="Coverage" hint="Which changed files were analysed." />
                <dl className="grid grid-cols-3 gap-3 text-sm">
                  <div>
                    <dt className="text-xs text-muted">Files changed</dt>
                    <dd className="mt-0.5 text-lg font-semibold tabular-nums">
                      {formatNumber(run.files_changed)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted">Analysed</dt>
                    <dd className="mt-0.5 text-lg font-semibold tabular-nums text-state-ok">
                      {formatNumber(run.files_analysed)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted">Not analysed</dt>
                    <dd className="mt-0.5 text-lg font-semibold tabular-nums text-muted">
                      {formatNumber(run.files_skipped)}
                    </dd>
                  </div>
                </dl>
                <Caveat>
                  {run.limited_coverage
                    ? 'A budget stopped the context build before every changed file was included, so this review saw part of the change.'
                    : 'Files that were not analysed are listed below with the reason. No coverage percentage is shown, because the reasons are not comparable.'}
                </Caveat>
              </Card>
            </div>

            <Card className="mt-4">
              <SectionHeader
                title="Changed files"
                hint={`${run.changed_files.length} file${run.changed_files.length === 1 ? '' : 's'} in this commit.`}
              />
              {run.changed_files.length === 0 ? (
                <EmptyState
                  title="No changed-file record"
                  hint="The file list for this commit is not stored, which happens when synchronisation did not complete."
                />
              ) : (
                <Table
                  caption="Changed files in this review"
                  columns={fileColumns}
                  rows={run.changed_files}
                  rowKey={(row) => String(row.id)}
                  isRowFocused={(row) => row.file_path === focusedFile}
                />
              )}
            </Card>
          </>
        )}
      </AsyncSection>

      <Card className="mt-4">
        <SectionHeader
          title="Findings from this review"
          hint="Every claim recorded, whether it was published or rejected."
        />
        <AsyncSection
          query={findings}
          skeleton={<SkeletonCards count={4} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No findings recorded for this review"
              hint="An empty result is a valid outcome — the model returning nothing is not a failure."
            />
          }
        >
          {(data) => (
            <ul className="space-y-2.5">
              {data.items.map((finding, index) => (
                <FindingCard
                  key={finding.id}
                  finding={finding}
                  index={index}
                  href={`/findings/${finding.id}`}
                  // Hovering a finding marks its file in the table above. The
                  // relationship between a finding and the file it is anchored
                  // in is the one thing a flat list could not show.
                  onFocusFile={setFocusedFile}
                />
              ))}
            </ul>
          )}
        </AsyncSection>
      </Card>
    </div>
  )
}
