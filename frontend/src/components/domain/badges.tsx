import { StatusPill } from '@/components/ui/primitives'
import type { RunStatus, ValidationResult } from '@/types/api'
import type { Severity } from '@/types/domain'
import { SEVERITY } from '@/utils/severity'
import { publicationOf, runStatusOf, VALIDATION } from '@/utils/status'

/**
 * Domain badges.
 *
 * Every one renders glyph + label + colour, and every one carries the
 * explanation as its `title`. The explanations are not decoration: "Rejected
 * claim" and "Not published" both invite a wrong reading, and the tooltip is
 * where the correct one lives.
 */

export function SeverityBadge({ severity }: { severity: Severity }) {
  const { label, glyph, className } = SEVERITY[severity]
  return <StatusPill label={label} glyph={glyph} className={className} />
}

/**
 * A run's outcome.
 *
 * `status === null` renders "Not reviewed" — the honest state for a pull
 * request whose current commit no review has covered. It is deliberately not
 * blank and deliberately not a pass.
 */
export function RunStatusBadge({
  status,
  isStale = false,
}: {
  status: RunStatus | null | undefined
  isStale?: boolean
}) {
  const presentation = runStatusOf(status)
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusPill
        label={presentation.label}
        glyph={presentation.glyph}
        className={presentation.className}
        title={presentation.description}
      />
      {isStale ? (
        <StatusPill
          label="Stale"
          glyph="⤳"
          className="text-state-idle"
          title="This run covered a commit that is no longer the pull request's head. It is history, not the current result."
        />
      ) : null}
    </span>
  )
}

export function ValidationBadge({ validation }: { validation: ValidationResult }) {
  const presentation = VALIDATION[validation]
  return (
    <StatusPill
      label={presentation.label}
      glyph={presentation.glyph}
      className={presentation.className}
      title={presentation.description}
    />
  )
}

export function PublicationBadge({ isPublished }: { isPublished: boolean }) {
  const presentation = publicationOf(isPublished)
  return (
    <StatusPill
      label={presentation.label}
      glyph={presentation.glyph}
      className={presentation.className}
      title={presentation.description}
    />
  )
}

/**
 * Shown on a run that completed without seeing every changed file.
 *
 * A separate badge rather than a different status, because the review did
 * complete — the coverage was limited, which is a different fact from failure
 * and needs to be visible without being alarming.
 */
export function LimitedCoverageBadge() {
  return (
    <StatusPill
      label="Limited coverage"
      glyph="◧"
      className="text-state-warn"
      title="This review completed, but a budget stopped it before every changed file was included."
    />
  )
}
