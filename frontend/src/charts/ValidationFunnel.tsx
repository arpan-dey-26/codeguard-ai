import { ChartFrame } from '@/charts/ChartFrame'
import { Caveat } from '@/components/ui/primitives'
import type { FunnelCounts } from '@/types/api'

/**
 * The validation funnel — the honest answer to "the model said nine things,
 * why are there five comments?".
 *
 * This is the single most over-readable panel in the product, so the wording
 * is load-bearing:
 *
 * - **"Rejected claims" is never "false positives."** A rejection means the
 *   claim failed a *structural* gate — it could not be anchored to the
 *   reviewed source. Whether the issue it described is real is a question this
 *   system does not answer, and labelling the stage as a false-positive count
 *   would be claiming an evaluation that was never run.
 * - **"Validated" is not "confirmed."** It means checked against the diff and
 *   the file at the reviewed commit.
 * - **The stages sum.** `emitted = dropped + validated + rejected`, which is
 *   only true because the provider reports what it could not parse. A funnel
 *   whose stages do not add up is worse than none, because it looks
 *   authoritative.
 *
 * Drawn as proportional bars rather than a tapering polygon: a classic funnel
 * shape implies each stage is a subset of the one above it, which is true
 * here — but the shape also makes small stages invisible, and the small stages
 * are the interesting ones.
 */

export function ValidationFunnel({ funnel }: { funnel: FunnelCounts }) {
  const stages = [
    {
      key: 'claims_emitted',
      label: 'Claims emitted',
      value: funnel.claims_emitted,
      className: 'bg-series-4',
      note: 'Items the model put in its findings array.',
    },
    {
      key: 'claims_dropped',
      label: 'Unparseable',
      value: funnel.claims_dropped,
      className: 'bg-state-idle',
      note: 'Items that could not be read as a claim at all.',
    },
    {
      key: 'validated_findings',
      label: 'Validated',
      value: funnel.validated_findings,
      className: 'bg-series-1',
      note: 'Claims anchored to the reviewed source at the reviewed commit.',
    },
    {
      key: 'rejected_claims',
      label: 'Rejected claims',
      value: funnel.rejected_claims,
      className: 'bg-state-warn',
      note: 'Claims that failed a structural validation gate.',
    },
    {
      key: 'published_comments',
      label: 'Published comments',
      value: funnel.published_comments,
      className: 'bg-state-ok',
      note: 'Comments GitHub confirmed.',
    },
  ]

  const scale = Math.max(1, funnel.claims_emitted, funnel.validated_findings)
  const balances =
    funnel.claims_emitted ===
    funnel.claims_dropped + funnel.validated_findings + funnel.rejected_claims

  return (
    <div>
      <ChartFrame
        title="Validation funnel"
        summary={
          `The model emitted ${funnel.claims_emitted} claims. ` +
          `${funnel.claims_dropped} could not be parsed, ` +
          `${funnel.validated_findings} were validated against the diff and the file, ` +
          `${funnel.rejected_claims} failed a structural gate, and ` +
          `${funnel.published_comments} comments were published to GitHub.`
        }
        data={stages.map((stage) => ({
          key: stage.key,
          label: stage.label,
          value: stage.value,
        }))}
      >
        <ul className="space-y-2">
          {stages.map((stage) => (
            <li key={stage.key}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="text-content">{stage.label}</span>
                <span className="tabular-nums text-content">{stage.value}</span>
              </div>
              <span className="mt-1 block h-1.5 overflow-hidden rounded-sm bg-border/50">
                <span
                  className={`block h-full rounded-sm ${stage.className}`}
                  style={{ width: `${Math.max(1, (stage.value / scale) * 100)}%` }}
                />
              </span>
              <p className="mt-0.5 text-xs text-muted">{stage.note}</p>
            </li>
          ))}
        </ul>
      </ChartFrame>

      <Caveat>
        A <strong>rejected claim</strong> failed a structural validation gate — it could not be
        anchored to the reviewed source. It is not a confirmed false positive, and a{' '}
        <strong>validated finding</strong> is not an independently proven defect: it means the claim
        matches real code at the reviewed commit.
        {!balances && funnel.claims_emitted > 0 ? (
          <>
            {' '}
            These stages do not sum for this run, which means it predates the funnel accounting fix
            — treat the emitted count as a lower bound.
          </>
        ) : null}
      </Caveat>
    </div>
  )
}
