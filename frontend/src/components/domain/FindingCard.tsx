/**
 * One finding, as a card that can be opened.
 *
 * This replaces a flat row in a divided list. The row was readable but it made
 * every finding look identical, and the thing that distinguishes findings in
 * this product is not their title — it is **how well each one is anchored**. A
 * validated finding sitting on a verified anchor and a rejected claim that never
 * resolved to a line are different objects, and the card is built so that
 * difference is visible before anything is read.
 *
 * **Every field shown comes from `FindingSummary`.** Nothing is computed,
 * inferred or filled in. In particular:
 *
 *   - `claimed_line` and `resolved_line` are shown *separately* whenever they
 *     differ, because the gap between what the model said and what the
 *     validator confirmed is the product's central claim, not a detail to
 *     normalise away.
 *   - `rejection_reason` is shown as the gate that caught it.
 *   - A rejected claim is never labelled a false positive. The validator
 *     rejects claims it cannot *verify*, which is a different thing from claims
 *     that are *wrong*, and the wording tests in `utils/__tests__` enforce that
 *     distinction.
 *
 * **Interaction.** The card lifts on hover and expands on click, in place, with
 * no layout jump above it. The severity rail on the left edge is the only
 * always-visible colour, and it is paired with the severity glyph and label
 * inside — colour never carries meaning alone.
 */

import { useId, useState } from 'react'
import { PublicationBadge, SeverityBadge, ValidationBadge } from '@/components/domain/badges'
import { IconChevronDown, IconExternal } from '@/components/ui/icons'
import { Mono } from '@/components/ui/primitives'
import { Tooltip } from '@/components/ui/Tooltip'
import type { FindingSummary } from '@/types/api'
import { cn } from '@/utils/cn'
import { humanise } from '@/utils/format'
import { SEVERITY } from '@/utils/severity'

export function FindingCard({
  finding,
  index,
  href,
  /** Called on hover, so a parent can highlight the matching file row. */
  onFocusFile,
}: {
  finding: FindingSummary
  index?: number | undefined
  /** Where the detail page lives. A link, so middle-click and Cmd-click work. */
  href: string
  onFocusFile?: ((filePath: string | null) => void) | undefined
}) {
  const [open, setOpen] = useState(false)
  // The expander needs to name the region it controls, or a screen-reader user
  // is told a button is expanded with no way to know what expanded.
  const detailId = useId()
  const severity = SEVERITY[finding.severity]

  // Shown only when the validator moved the finding. `anchor_offset` is the
  // API's own answer to "did this move", so the card does not recompute it
  // from the two line numbers.
  const relocated =
    finding.anchor_offset !== null &&
    finding.anchor_offset !== 0 &&
    finding.resolved_line !== null

  return (
    <li
      className={cn('panel panel-interactive overflow-hidden', index !== undefined && 'stagger')}
      style={index === undefined ? undefined : { '--stagger': index }}
      onPointerEnter={() => onFocusFile?.(finding.file_path)}
      onPointerLeave={() => onFocusFile?.(null)}
    >
      {/* The severity rail. Decorative — the badge below carries the same
          information as a glyph and a word. */}
      <span
        aria-hidden="true"
        className={cn('absolute inset-y-0 left-0 w-[3px]', severity.fillClassName)}
      />

      <div className="pl-3">
        <div className="flex flex-col items-start gap-3 p-3 sm:flex-row">
          <div className="w-full min-w-0 flex-1 sm:w-auto">
            <div className="flex flex-wrap items-center gap-2">
              <SeverityBadge severity={finding.severity} />
              <a
                href={href}
                className={cn(
                  'min-w-0 flex-1 break-words text-sm font-medium text-content sm:truncate',
                  'transition-colors duration-instant ease-out hover:text-accent',
                )}
              >
                {finding.title}
              </a>
            </div>

            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
              <Mono className="text-content/80">{finding.file_path}</Mono>
              {finding.resolved_line === null ? (
                <Tooltip content="The validator could not resolve this claim to a line in the diff, so it was never anchored.">
                  <span tabIndex={0} className="cursor-help text-state-warn">no resolved line</span>
                </Tooltip>
              ) : (
                <Mono className="text-content/80">:{finding.resolved_line}</Mono>
              )}
              {finding.qualified_name ? (
                <>
                  <span className="text-muted/50">in</span>
                  <Mono>{finding.qualified_name}</Mono>
                </>
              ) : null}
            </div>
          </div>

          <div className="flex w-full shrink-0 flex-wrap items-center justify-between gap-1.5 sm:w-auto sm:flex-col sm:items-end">
            <div className="flex items-center gap-1.5">
              <ValidationBadge validation={finding.validation} />
              <PublicationBadge isPublished={finding.is_published} />
            </div>
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              aria-expanded={open}
              aria-controls={detailId}
              className={cn(
                'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-micro text-muted',
                'transition-colors duration-instant ease-out hover:text-content',
              )}
            >
              {open ? 'Less' : 'Anchor detail'}
              <IconChevronDown
                size={12}
                className={cn(
                  'transition-transform duration-quick ease-out',
                  open && 'rotate-180',
                )}
              />
            </button>
          </div>
        </div>

        {/* The expansion. `grid-template-rows` from 0fr to 1fr animates a
            height the content defines, without measuring it and without
            animating `height` itself — so the card opens smoothly and nothing
            below it jumps.
            `invisible` when collapsed, and that is not decoration: a region
            kept at zero height with `overflow-hidden` is still in the
            accessibility tree and its "Full finding" link is still in the tab
            order, so a keyboard user tabbing down a list of twenty findings
            would land on twenty invisible links. `visibility` removes it from
            both, and unlike `display` it can sit in the transition. */}
        <div
          id={detailId}
          inert={!open}
          aria-hidden={!open}
          className={cn(
            'grid transition-[grid-template-rows,opacity,visibility] duration-panel ease-out',
            open ? 'grid-rows-[1fr] opacity-100' : 'invisible grid-rows-[0fr] opacity-0',
          )}
        >
          <div className="overflow-hidden">
            <div className="border-t border-border/70 px-3 py-2.5">
              <dl className="grid gap-x-5 gap-y-2 text-xs sm:grid-cols-2">
                <Field term="Claimed line">
                  <Mono>{finding.claimed_line}</Mono>
                </Field>
                <Field term="Resolved line">
                  {finding.resolved_line === null ? (
                    <span className="text-state-warn">not resolved</span>
                  ) : (
                    <Mono>{finding.resolved_line}</Mono>
                  )}
                </Field>
                <Field term="Anchor">
                  {finding.anchor_verified ? (
                    <span className="text-state-ok">verified against the file</span>
                  ) : (
                    <span className="text-state-warn">not verified</span>
                  )}
                </Field>
                <Field term="Side">
                  <Mono>{finding.side}</Mono>
                </Field>
                {finding.model_confidence !== null ? (
                  <Field term="Model confidence">
                    <Mono>{finding.model_confidence.toFixed(2)}</Mono>
                  </Field>
                ) : null}
                {finding.cwe_id ? (
                  <Field term="Reference">
                    <Mono>{finding.cwe_id}</Mono>
                  </Field>
                ) : null}
              </dl>

              {relocated ? (
                <p className="mt-2.5 border-l-2 border-border pl-2.5 text-xs leading-relaxed text-muted">
                  The model cited line <Mono>{finding.claimed_line}</Mono>; the validator
                  matched the quoted source at <Mono>{finding.resolved_line}</Mono> and moved
                  the comment there. Relocation is bounded and disclosed — it is not a
                  correction of the model&rsquo;s reasoning.
                </p>
              ) : null}

              {finding.rejection_reason ? (
                <p className="mt-2.5 text-xs leading-relaxed text-muted">
                  <span className="font-medium text-state-warn">
                    Gate: {humanise(finding.rejection_reason)}
                  </span>{' '}
                  — this claim was not published because it could not be verified against the
                  diff. That is different from the claim being wrong.
                </p>
              ) : null}

              <div className="mt-3">
                <a
                  href={href}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-muted',
                    'transition-colors duration-instant ease-out hover:border-accent hover:text-content',
                  )}
                >
                  Full finding
                  <IconExternal size={11} />
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </li>
  )
}

function Field({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted">{term}</dt>
      <dd className="mt-0.5 text-content">{children}</dd>
    </div>
  )
}
