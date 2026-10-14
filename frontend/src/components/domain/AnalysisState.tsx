/**
 * What a review looks like while it is still running.
 *
 * ============================================================================
 * THERE IS NO PROGRESS BAR HERE, AND THAT IS DELIBERATE.
 *
 * The backend exposes a review run's `status` — `queued`, `running`, and the
 * terminal states — and nothing finer. It does not report which pipeline stage a
 * run is in, and it does not report a percentage, because it does not know one:
 * the work is a single pass whose duration is dominated by two third-party
 * round trips.
 *
 * So this component shows the stages of the pipeline as a **static explanation
 * of what the system does**, with none of them marked complete, plus the one
 * fact the API actually provides: queued, or running. A filling bar or a lit-up
 * stage list would be a fabricated backend state dressed as a status display,
 * and a person watching it would draw false conclusions about where a slow
 * review is stuck.
 *
 * If the backend ever reports a stage, this is where it goes — one prop, and the
 * list below starts marking real progress. Until then it explains and does not
 * claim.
 * ============================================================================
 */

import { IconSpinner } from '@/components/ui/icons'
import { Tooltip } from '@/components/ui/Tooltip'
import { cn } from '@/utils/cn'

/**
 * The pipeline, as documented in architecture.md §8.2. Presented as an
 * explanation of the process — never as a progress indicator.
 */
const PIPELINE = [
  { label: 'Fetch the diff', detail: 'Changed files and their patches, from the GitHub API.' },
  { label: 'Parse and map', detail: 'Unified diff to a LineMap, then AST declarations.' },
  { label: 'Build context', detail: 'Touched declarations only, inside a byte and token budget.' },
  { label: 'Model call', detail: 'One request. The response is a set of claims, not findings.' },
  { label: 'Validate', detail: 'Fourteen gates against the real diff at the real commit.' },
  { label: 'Publish', detail: 'Validated findings become inline comments. Nothing else does.' },
] as const

export function AnalysisState({
  status,
  className,
}: {
  /** The run status, straight from the API. Only these two are "in flight". */
  status: 'queued' | 'running'
  className?: string | undefined
}) {
  return (
    <div className={cn('panel p-4', className)} role="status" aria-live="polite">
      <div className="flex items-center gap-2.5">
        <IconSpinner size={15} className="text-accent" />
        <p className="text-sm font-medium text-content">
          {status === 'queued' ? 'Queued' : 'Analysis in progress'}
        </p>
        <Tooltip
          content="The backend reports queued or running for a review. It does not report a stage or a percentage, so none is shown."
          side="right"
        >
          <span className="cursor-help text-micro uppercase tracking-wider text-muted">
            no progress reported
          </span>
        </Tooltip>
      </div>

      <p className="mt-1.5 text-xs leading-relaxed text-muted">
        {status === 'queued'
          ? 'A worker has not picked this run up yet. Reviews are claimed from a queue, so a run can wait behind others.'
          : 'A worker is executing this run. Most of the elapsed time is spent in two external calls — fetching the diff and the model request — so a long run is usually waiting, not looping.'}
      </p>

      <div className="rule-fade my-3.5" />

      <p className="text-micro font-medium uppercase tracking-wider text-muted">
        What a review does
      </p>
      {/* An ordered list with no completion state. The markers are numbers, not
          ticks — a tick would say "done", which is exactly what is unknown. */}
      <ol className="mt-2 space-y-2">
        {PIPELINE.map((stage, index) => (
          <li key={stage.label} className="flex gap-2.5">
            <span
              aria-hidden="true"
              className="mt-[1px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-border text-micro text-muted"
            >
              {index + 1}
            </span>
            <div className="min-w-0">
              <p className="text-xs font-medium text-content">{stage.label}</p>
              <p className="text-xs leading-snug text-muted">{stage.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
