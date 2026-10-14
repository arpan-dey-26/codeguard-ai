import { getIntegrationStatus, getSettings } from '@/api/endpoints'
import { AsyncSection } from '@/components/data/AsyncSection'
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
  StatusPill,
} from '@/components/ui/primitives'
import { useApiQuery } from '@/hooks/useApiQuery'
import { formatPercent, formatRelative } from '@/utils/format'

/**
 * Settings — read-only, and secret-free.
 *
 * Every value here is a literal or a boolean *about* a credential. There is no
 * field, and no endpoint, that carries a key, a token or a secret. There is
 * also no form: editing production credentials from a browser needs secure
 * secret management, which this build does not have, so offering the form
 * would be worse than not having it.
 *
 * The page also carries the honest statement of what the platform measures.
 * Putting it here rather than burying it in documentation means the person
 * reading the numbers can find the caveats in the same product.
 */

export function SettingsPage() {
  const settings = useApiQuery(() => getSettings(), [])
  const integration = useApiQuery(() => getIntegrationStatus(), [])

  return (
    <div className="mx-auto max-w-[900px]">
      <PageHeader
        title="Settings"
        subtitle="What this deployment is configured to do. Read-only."
      />

      <Card>
        <SectionHeader title="Configuration" />
        <AsyncSection query={settings} skeleton={<Skeleton className="h-48 w-full" />}>
          {(data) => (
            <>
              <DefinitionList>
                <Definition term="Environment">{data.environment}</Definition>
                <Definition term="Version" mono>
                  {data.app_version}
                </Definition>
                <Definition term="GitHub App">
                  <Configured
                    ok={data.github_app_configured}
                    yes="Configured"
                    no="Not configured"
                  />
                </Definition>
                <Definition term="Webhook secret">
                  <Configured
                    ok={data.github_webhook_secret_configured}
                    yes="Configured"
                    no="Not configured"
                  />
                </Definition>
                <Definition term="AI provider">
                  {data.ai_provider}{' '}
                  <Configured
                    ok={data.ai_provider_configured}
                    yes="credentials present"
                    no="no credentials"
                  />
                </Definition>
                <Definition term="Model" mono>
                  {data.ai_model ?? 'Not set'}
                </Definition>
                <Definition term="Prompt version" mono>
                  {data.prompt_version}
                </Definition>
                <Definition term="Review publishing">
                  <Configured
                    ok={data.review_publishing_enabled}
                    yes="Enabled"
                    no="Disabled — reviews run and are stored, nothing is posted"
                  />
                </Definition>
                <Definition term="Max inline comments">{data.max_inline_comments}</Definition>
                <Definition term="Confidence threshold">
                  {formatPercent(data.min_confidence)}{' '}
                  <span className="text-xs text-muted">
                    ({formatPercent(data.min_confidence_high_severity)} for critical and high)
                  </span>
                </Definition>
                <Definition term="Files per review">{data.max_files_per_review}</Definition>
                <Definition term="Findings per review">{data.max_findings_per_review}</Definition>
              </DefinitionList>

              <Caveat>
                No credential value is shown on this page, and the API has no field that
                could carry one. These are statements <em>about</em> configuration, not the
                configuration itself. Changing any of it is a deployment action.
              </Caveat>

              {data.dashboard_unauthenticated_reads_enabled ? (
                <div className="mt-4 rounded-md border border-state-warn/40 bg-surface p-3">
                  <p className="text-sm font-medium text-state-warn">
                    Development read-only access is enabled
                  </p>
                  <p className="mt-1 text-sm text-muted">
                    Anyone who can reach this local server can read its dashboard data
                    without signing in. GitHub sign-in and per-user repository access
                    are implemented. The backend refuses to start in production with
                    unauthenticated reads enabled.
                  </p>
                </div>
              ) : null}
            </>
          )}
        </AsyncSection>
      </Card>

      <Card className="mt-4">
        <SectionHeader
          title="GitHub installations"
          hint="Accounts where the App is installed, and whether events are arriving."
        />
        <AsyncSection
          query={integration}
          skeleton={<Skeleton className="h-32 w-full" />}
          isEmpty={(data) => data.installations.length === 0}
          empty={
            <EmptyState
              title="No installations"
              hint="Install the GitHub App on an account or organisation to begin receiving pull-request events."
            />
          }
        >
          {(data) => (
            <>
              <ul className="space-y-2">
                {data.installations.map((installation) => (
                  <li
                    key={installation.id}
                    className="rounded-md border border-border px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm text-content">{installation.account_login}</span>
                      <span className="text-xs text-muted">{installation.account_type}</span>
                      {installation.suspended ? (
                        <StatusPill
                          label="Suspended"
                          glyph="▲"
                          className="text-state-bad"
                          title="GitHub has suspended this installation, so no events arrive."
                        />
                      ) : null}
                      {installation.uninstalled ? (
                        <StatusPill label="Uninstalled" glyph="○" className="text-state-idle" />
                      ) : null}
                      <span className="ml-auto text-xs text-muted">
                        last event{' '}
                        <time
                          dateTime={installation.last_event_at ?? undefined}
                          title={installation.last_event_at ?? undefined}
                        >
                          {formatRelative(installation.last_event_at)}
                        </time>
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-muted">
                      {installation.active_repository_count} of{' '}
                      {installation.repository_count} repositories monitored
                      {installation.failed_deliveries > 0 ? (
                        <span className="text-state-warn">
                          {' '}
                          · {installation.failed_deliveries} failed webhook deliveries
                        </span>
                      ) : null}
                    </p>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted">
                Webhook endpoint: <Mono>{data.webhook_path}</Mono>
              </p>
            </>
          )}
        </AsyncSection>
      </Card>

      <Card className="mt-4">
        <SectionHeader title="What these numbers mean" />
        <dl className="space-y-3 text-sm">
          <MetricDefinition term="Claims emitted">
            Items the model put in its findings array for a review. The raw output, before
            anything was checked.
          </MetricDefinition>
          <MetricDefinition term="Validated findings">
            Claims that passed every structural validation gate — the file was one we
            showed the model, the line is in this pull request’s diff, and the source the
            model quoted is genuinely at that line in the reviewed commit.{' '}
            <strong>This does not mean the issue has been proven.</strong> It means the
            claim points at real code.
          </MetricDefinition>
          <MetricDefinition term="Rejected claims">
            Claims that failed a structural gate and were never published.{' '}
            <strong>These are not confirmed false positives.</strong> The gate describes a
            problem with the claim’s location or shape. Whether the underlying issue is
            real is a semantic question, and no semantic evaluation has been run.
          </MetricDefinition>
          <MetricDefinition term="Published comments">
            Review comments GitHub confirmed. Read from stored publication state — never
            inferred from which lines the platform considers commentable.
          </MetricDefinition>
          <MetricDefinition term="Pull requests reviewed">
            Distinct pull requests with at least one completed review. Several attempts at
            one pull request count once here and separately under review attempts.
          </MetricDefinition>
          <MetricDefinition term="Anchor offset">
            The distance between the line the model claimed and the line the validator
            resolved by matching the quoted source. It describes the claimed-versus-resolved
            relationship <em>under this system’s relocation policy</em>.{' '}
            <strong>It is not an accuracy score</strong>, and this dashboard does not
            publish one — that would need a labelled benchmark, which does not exist.
          </MetricDefinition>
          <MetricDefinition term="Coverage">
            Files changed, analysed and not analysed, reported separately. Not reduced to a
            percentage, because a file can be absent for reasons that are not comparable —
            binary content, no parser for the language, or a review budget.
          </MetricDefinition>
        </dl>
      </Card>
    </div>
  )
}

function MetricDefinition({
  term,
  children,
}: {
  term: string
  children: React.ReactNode
}) {
  return (
    <div>
      <dt className="text-sm font-medium text-content">{term}</dt>
      <dd className="mt-0.5 text-sm leading-relaxed text-muted">{children}</dd>
    </div>
  )
}

function Configured({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return (
    <StatusPill
      label={ok ? yes : no}
      glyph={ok ? '✓' : '○'}
      className={ok ? 'text-state-ok' : 'text-state-idle'}
    />
  )
}
