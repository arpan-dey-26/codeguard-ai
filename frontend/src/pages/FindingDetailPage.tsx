import { Link, useParams } from 'react-router-dom'
import { getFinding } from '@/api/endpoints'
import { AsyncSection } from '@/components/data/AsyncSection'
import { PublicationBadge, SeverityBadge, ValidationBadge } from '@/components/domain/badges'
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
  StatusPill,
} from '@/components/ui/primitives'
import { useApiQuery } from '@/hooks/useApiQuery'
import type { FindingDetailResponse } from '@/types/api'
import { formatDateTime, formatPercent, formatRelative, humanise, shortSha } from '@/utils/format'
import { RUN_ERROR } from '@/utils/status'

/**
 * One finding, in full.
 *
 * The page is built around a distinction the rest of the product depends on:
 * **what the model claimed** and **what this system verified** are shown side
 * by side and never merged. `claimed_line` stays visible even when it was
 * wrong, because the difference between the two is the only honest record of
 * how far off the model was.
 */

export function FindingDetailPage() {
  const params = useParams<{ findingId: string }>()
  const findingId = params.findingId ?? ''
  const query = useApiQuery(() => getFinding(findingId), [findingId], {
    enabled: findingId !== '',
  })

  if (!findingId) {
    return (
      <EmptyState
        // An alert mark, not the empty tray: nothing is missing from a list
        // here — a specific id did not resolve, which is a different fact.
        icon={<IconAlert size={17} />}
        title="Finding not found"
        hint="This finding id does not exist, or it belongs to an installation your account cannot see."
        action={
          <Link
            to="/findings"
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1 text-sm text-muted transition-colors duration-instant ease-out hover:border-accent hover:text-content"
          >
            Back to findings
          </Link>
        }
      />
    )
  }

  return (
    <div className="mx-auto max-w-[900px]">
      <AsyncSection
        query={query}
        skeleton={<Skeleton className="h-64 w-full" />}
        placeholderHeading={<PageHeader eyebrow="Finding" title="Finding" />}
      >
        {(finding) => (
          <>
            <PageHeader
              title={finding.title}
              subtitle={
                <span className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={finding.severity} />
                  <StatusPill
                    label={humanise(finding.category)}
                    glyph="◇"
                    className="text-muted"
                  />
                  <ValidationBadge validation={finding.validation} />
                  <PublicationBadge isPublished={finding.is_published} />
                  {finding.is_stale ? (
                    <StatusPill
                      label="From a superseded commit"
                      glyph="⤳"
                      className="text-state-idle"
                      title="This finding came from a review of a commit that is no longer the pull request's head."
                    />
                  ) : null}
                </span>
              }
              actions={
                finding.published_comment_url ? (
                  <ExternalLink href={finding.published_comment_url}>View comment</ExternalLink>
                ) : (
                  <ExternalLink href={finding.pull_request_url}>Open pull request</ExternalLink>
                )
              }
            />

            {finding.validation === 'rejected' ? (
              <Card className="mb-4">
                <SectionHeader
                  title="This claim was not published"
                  hint={`It failed the ${humanise(finding.rejection_reason)} gate.`}
                />
                <p className="text-sm text-muted">
                  Structural validation could not anchor this claim to the reviewed source,
                  so it was never posted to GitHub. It is recorded here because the
                  rejections are how the platform measures its own behaviour over time.
                </p>
                <Caveat>
                  A rejected claim is <strong>not</strong> a confirmed false positive. The
                  gate that stopped it describes a problem with the claim’s{' '}
                  <em>location or shape</em>, not with whether the issue it describes is
                  real. Deciding that would take a semantic evaluation, which this system
                  does not perform.
                </Caveat>
              </Card>
            ) : null}

            <Card>
              <SectionHeader title="What the model reported" />
              <p className="whitespace-pre-wrap text-sm text-content">{finding.description}</p>
              {finding.impact ? (
                <>
                  <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">
                    Impact
                  </h3>
                  <p className="mt-1 whitespace-pre-wrap text-sm text-content">
                    {finding.impact}
                  </p>
                </>
              ) : null}
              {finding.suggested_fix ? (
                <>
                  <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">
                    Suggested fix
                  </h3>
                  <p className="mt-1 whitespace-pre-wrap text-sm text-content">
                    {finding.suggested_fix}
                  </p>
                </>
              ) : null}
              {finding.rationale ? (
                <>
                  <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">
                    Reasoning given
                  </h3>
                  <p className="mt-1 whitespace-pre-wrap text-sm text-muted">
                    {finding.rationale}
                  </p>
                </>
              ) : null}
              <Caveat>
                This text was written by a language model. It has been checked for{' '}
                <em>where</em> it points, not for whether it is right.
              </Caveat>
            </Card>

            <Card className="mt-4">
              <SectionHeader
                title="Location"
                hint="What the model claimed, and what this system resolved."
              />
              <LocationPanel finding={finding} />
            </Card>

            {finding.source_context.length > 0 ? (
              <Card className="mt-4">
                <SectionHeader
                  title="Anchored source"
                  hint="The exact line the claim was verified against."
                />
                <pre className="overflow-x-auto rounded-md border border-border bg-surface p-3 text-[13px]">
                  {finding.source_context.map((line) => (
                    <code key={line.line} className="block whitespace-pre">
                      <span className="mr-4 select-none text-muted">
                        {String(line.line).padStart(5, ' ')}
                      </span>
                      <span className="text-content">{line.text}</span>
                    </code>
                  ))}
                </pre>
                <Caveat>
                  One line, not a file window. Repository source is deliberately not stored
                  by this platform, so the only source text available here is the snippet
                  the validator matched. Open the pull request on GitHub for surrounding
                  context.
                </Caveat>
              </Card>
            ) : null}

            <Card className="mt-4">
              <SectionHeader title="Provenance" />
              <DefinitionList>
                <Definition term="Repository">
                  <Link
                    to={`/repositories/${finding.repository_id}`}
                    className="hover:text-accent"
                  >
                    <Mono>{finding.repository_full_name}</Mono>
                  </Link>
                </Definition>
                <Definition term="Pull request">
                  <Link
                    to={`/pull-requests/${finding.pull_request_id}`}
                    className="hover:text-accent"
                  >
                    #{finding.pull_request_number}
                  </Link>
                </Definition>
                <Definition term="Review">
                  <Link to={`/review-runs/${finding.review_run_id}`} className="hover:text-accent">
                    Attempt {finding.review_run_attempt} ·{' '}
                    {RUN_ERROR[finding.review_run_status] ?? humanise(finding.review_run_status)}
                  </Link>
                </Definition>
                <Definition term="Commit reviewed" mono>
                  {shortSha(finding.head_sha)}
                </Definition>
                <Definition term="Model">{finding.model_name ?? 'Not recorded'}</Definition>
                <Definition term="Prompt version" mono>
                  {finding.prompt_version ?? '—'}
                </Definition>
                <Definition term="Model confidence">
                  {formatPercent(finding.model_confidence, 0)}
                  <span className="ml-1 text-xs text-muted">(self-reported)</span>
                </Definition>
                <Definition term="Fingerprint" mono>
                  {finding.fingerprint}
                </Definition>
                <Definition term="Recorded">
                  <time dateTime={finding.created_at ?? undefined} title={finding.created_at ?? undefined}>
                    {formatRelative(finding.created_at)}
                  </time>
                </Definition>
                {finding.cwe_id ? (
                  <Definition term="CWE">{finding.cwe_id}</Definition>
                ) : null}
                {finding.is_published ? (
                  <Definition term="Published">
                    {formatDateTime(finding.published_at)}
                    {finding.published_line !== null ? (
                      <span className="text-muted">
                        {' '}
                        at line {finding.published_line} ({finding.published_side})
                      </span>
                    ) : null}
                  </Definition>
                ) : null}
              </DefinitionList>
            </Card>

            {finding.events.length > 0 ? (
              <Card className="mt-4">
                <SectionHeader title="History" />
                <ol className="space-y-2 text-sm">
                  {finding.events.map((event) => (
                    <li key={event.id} className="flex flex-wrap items-baseline gap-2">
                      <span className="text-content">
                        {humanise(event.from_status)} → {humanise(event.to_status)}
                      </span>
                      <span className="text-xs text-muted">
                        {event.source === 'user' ? 'by a user' : 'by the system'}
                      </span>
                      <time
                        dateTime={event.created_at ?? undefined}
                        title={event.created_at ?? undefined}
                        className="ml-auto text-xs text-muted"
                      >
                        {formatRelative(event.created_at)}
                      </time>
                      {event.note ? (
                        <p className="w-full text-xs text-muted">{event.note}</p>
                      ) : null}
                    </li>
                  ))}
                </ol>
              </Card>
            ) : null}
          </>
        )}
      </AsyncSection>
    </div>
  )
}

/**
 * Claimed versus resolved, side by side.
 *
 * The offset is described in words ("resolved 2 lines from the claim") rather
 * than shown as a percentage or an accuracy score. It measures the distance
 * between the model's claim and the line this system's relocation policy
 * settled on — a property of the validator as much as of the model.
 */
function LocationPanel({ finding }: { finding: FindingDetailResponse }) {
  const offset = finding.anchor_offset

  return (
    <>
      <DefinitionList>
        <Definition term="File" mono>
          {finding.file_path}
        </Definition>
        <Definition term="Line the model claimed" mono>
          {finding.claimed_line}
          {finding.claimed_line_end !== null ? `–${finding.claimed_line_end}` : ''}
        </Definition>
        <Definition term="Line this system resolved" mono>
          {finding.resolved_line ?? 'Not resolved'}
        </Definition>
        <Definition term="Anchor verified">
          {finding.anchor_verified ? (
            <span className="text-state-ok">
              Yes — the quoted source was found at the resolved line
            </span>
          ) : (
            <span className="text-muted">No</span>
          )}
        </Definition>
        <Definition term="Side">{finding.side}</Definition>
        <Definition term="Enclosing declaration">
          {finding.qualified_name || 'Module level'}
        </Definition>
      </DefinitionList>

      {finding.anchor_snippet ? (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
            Quoted by the model
          </h3>
          <pre className="mt-1 overflow-x-auto rounded-md border border-border bg-surface p-2 text-[13px] text-content">
            <code>{finding.anchor_snippet}</code>
          </pre>
        </div>
      ) : null}

      <Caveat>
        {offset === null
          ? 'No line was resolved for this claim, so it was never anchored to source.'
          : offset === 0
            ? 'The model’s claimed line matched the line the validator resolved exactly.'
            : `The validator resolved a line ${Math.abs(offset)} away from the model’s claim by matching the quoted source. ` +
              'This measures the claimed-versus-resolved relationship under this system’s relocation policy — it is not an accuracy score for the model.'}
      </Caveat>
    </>
  )
}
