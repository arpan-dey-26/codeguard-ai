/**
 * Status presentation, and the wording rules that go with it.
 *
 * **Every status carries a glyph and a label, not only a colour** — the same
 * rule as severity (architecture.md NFR-U2). A status pill that is green or
 * red and nothing else is unreadable for roughly one in twelve men and
 * meaningless in a greyscale printout.
 *
 * **`partial_success` is never "Passed".** A partial review produced findings
 * and persisted them, but did not get all of them onto GitHub. Collapsing it
 * into a pass would hide exactly the failure the user needs to act on, and
 * collapsing it into a failure would throw away work that succeeded.
 *
 * **`superseded` is never a result.** It means a newer commit overtook the
 * run. It belongs in history and must never be shown as a pull request's
 * current state.
 */

import type { RunStatus, ValidationResult } from '@/types/api'

export interface StatusPresentation {
  label: string
  glyph: string
  className: string
  /** One sentence the UI can show on hover or beneath the pill. */
  description: string
}

export const RUN_STATUS: Record<RunStatus, StatusPresentation> = {
  succeeded: {
    label: 'Completed',
    glyph: '●',
    className: 'text-state-ok',
    description: 'The review ran and every validated finding reached GitHub.',
  },
  partial_success: {
    label: 'Completed, partial',
    glyph: '◐',
    className: 'text-state-warn',
    description:
      'The review ran and its findings are stored, but not all of them were published to GitHub.',
  },
  failed: {
    label: 'Failed',
    glyph: '▲',
    className: 'text-state-bad',
    description: 'The review did not complete. Any findings already stored are still listed.',
  },
  superseded: {
    label: 'Superseded',
    glyph: '⤳',
    className: 'text-state-idle',
    description: 'A newer commit replaced this one before the review finished.',
  },
  skipped: {
    label: 'Skipped',
    glyph: '○',
    className: 'text-state-idle',
    description: 'Nothing in this pull request needed review.',
  },
  queued: {
    label: 'Queued',
    glyph: '◌',
    className: 'text-state-idle',
    description: 'Waiting for a worker.',
  },
  running: {
    label: 'Running',
    glyph: '◍',
    className: 'text-accent',
    description: 'In progress.',
  },
}

/** The state of a pull request whose current commit has no review at all. */
export const NOT_REVIEWED: StatusPresentation = {
  label: 'Not reviewed',
  glyph: '–',
  className: 'text-muted',
  description:
    'The current commit of this pull request has not been reviewed. This is not a pass.',
}

export function runStatusOf(status: RunStatus | null | undefined): StatusPresentation {
  return status ? RUN_STATUS[status] : NOT_REVIEWED
}

export const VALIDATION: Record<ValidationResult, StatusPresentation> = {
  validated: {
    label: 'Validated',
    glyph: '✓',
    className: 'text-state-ok',
    description:
      'The claim was checked against the diff and the file at the reviewed commit. ' +
      'That means it is anchored to real code — not that the issue has been independently proven.',
  },
  rejected: {
    label: 'Rejected claim',
    glyph: '⊘',
    className: 'text-state-idle',
    description:
      'The claim failed a structural validation gate and was not published. ' +
      'This is not a judgement about whether the issue is real.',
  },
}

export const PUBLICATION_PUBLISHED: StatusPresentation = {
  label: 'Published',
  glyph: '↗',
  className: 'text-state-ok',
  description: 'A review comment for this finding exists on GitHub.',
}

export const PUBLICATION_NOT_PUBLISHED: StatusPresentation = {
  label: 'Not published',
  glyph: '·',
  className: 'text-muted',
  description:
    'No GitHub comment is recorded for this finding. It may have been demoted to the ' +
    'review summary, suppressed as already reported, or publication may have failed.',
}

export function publicationOf(isPublished: boolean): StatusPresentation {
  return isPublished ? PUBLICATION_PUBLISHED : PUBLICATION_NOT_PUBLISHED
}

/**
 * Anchor buckets, described in terms of what was measured.
 *
 * Never "accuracy". The offset is the distance between the line the model
 * claimed and the line this system's relocation policy resolved (ADR-0011).
 */
export const ANCHOR_BUCKET: Record<string, string> = {
  exact: 'Exact line',
  off_by_1: 'Resolved 1 line away',
  off_by_2: 'Resolved 2 lines away',
  off_by_3_plus: 'Resolved 3+ lines away',
  unresolved: 'No resolved line',
}

/** Why a file was not analysed, in words a developer can act on. */
export const SKIP_REASON: Record<string, string> = {
  binary: 'Binary file',
  file_too_large: 'Over the size limit',
  no_patch: 'GitHub returned no diff',
  unsupported_language: 'No parser for this language',
  generated: 'Generated file',
  vendored: 'Vendored dependency',
  deleted: 'Deleted by this pull request',
  pr_budget_exceeded: 'Over the per-review budget',
  unspecified: 'Not recorded',
}

/** Why a run failed, in words a developer can act on. */
export const RUN_ERROR: Record<string, string> = {
  github_access_lost: 'Lost access to the repository on GitHub',
  ai_unavailable: 'The AI provider could not be reached or is not configured',
  ai_schema_invalid: 'The model returned a response that could not be used',
  ai_truncated: 'The model hit its output limit',
  diff_unavailable: 'The diff could not be retrieved',
  publish_failed: 'Findings were stored but could not all be published to GitHub',
  stale_head: 'A newer commit arrived before the review could be published',
  internal: 'An internal error',
}
