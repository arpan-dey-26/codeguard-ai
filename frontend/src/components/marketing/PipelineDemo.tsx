/**
 * The hero's product visualisation.
 *
 * ============================================================================
 * EVERYTHING IN THIS FILE IS PRESENTATION CONTENT. NONE OF IT IS PRODUCT DATA.
 *
 * The diff below is a hand-written illustration of a resource leak. The finding
 * beside it is hand-written prose. No API is called from this component, it
 * imports nothing from `@/api`, and it renders only on the unauthenticated
 * entry screen — a person who is signed in never sees it. The strings are
 * marked `DEMO_` and the panel is labelled "Illustration" on screen so that
 * nobody, including a future contributor, can mistake this for a real review.
 *
 * This separation is the whole reason the file exists. A hero that reaches into
 * the dashboard's data layer for something to show is how a product ends up
 * displaying one repository's findings to anonymous visitors.
 * ============================================================================
 *
 * **What it communicates.** The real pipeline, in the real order, because that
 * order *is* the product's argument:
 *
 *     pull request → diff → analysis → validation → inline comment
 *
 * The validation step is the one that matters and is given the most room. Any
 * tool can send a diff to a model; what this project does is refuse the model's
 * claims that do not match the diff. A hero that showed "AI reads your code,
 * AI writes a comment" would be selling something else.
 *
 * **Motion budget.** One entrance sequence on mount, then a single slow sweep
 * on the analysis bar. Roughly forty animated nodes, all `opacity` and
 * `transform`, all CSS. No canvas, no WebGL, no requestAnimationFrame, no
 * particle system. Under reduced motion every element renders in its final
 * state immediately and the sweep does not run.
 */

import { useEffect, useState } from 'react'
import { IconCheck, IconFinding, IconPullRequest } from '@/components/ui/icons'
import { TiltCard } from '@/components/ui/Surface'
import { useReducedMotion } from '@/hooks/useMotion'
import { cn } from '@/utils/cn'

/** Illustrative diff. Not from any repository. */
const DEMO_DIFF: ReadonlyArray<{ n: number; kind: 'ctx' | 'add'; text: string; flag?: boolean }> = [
  { n: 11, kind: 'ctx', text: '    def load(self, key):' },
  { n: 12, kind: 'add', text: '        path = os.path.join(self.root, key)' },
  { n: 13, kind: 'add', text: '        handle = open(path)', flag: true },
  { n: 14, kind: 'add', text: '        data = handle.read()' },
  { n: 15, kind: 'add', text: '        return data' },
]

/** Illustrative finding. Not produced by any model. */
const DEMO_FINDING = {
  severity: 'High',
  title: 'File handle is never closed',
  detail: 'open() without a context manager — descriptors leak until the process exits.',
  line: 13,
  anchor: 'handle = open(path)',
} as const

/** The pipeline stages, as labels. No progress value is shown or implied. */
const STAGES = ['Diff', 'Analysis', 'Validation', 'Comment'] as const

export function PipelineDemo({ className }: { className?: string | undefined }) {
  const reduced = useReducedMotion()
  // `step` drives the entrance only. It settles at the last stage and stays
  // there — this is not a loop, and it is not polling anything.
  const [step, setStep] = useState(reduced ? STAGES.length : 0)

  useEffect(() => {
    if (reduced) {
      setStep(STAGES.length)
      return undefined
    }
    // One chain of timeouts rather than an interval, so it cannot run past the
    // end, and every handle is cleared on unmount.
    const timers = STAGES.map((_, index) => setTimeout(() => setStep(index + 1), 520 + index * 380))
    return () => timers.forEach(clearTimeout)
  }, [reduced])

  const analysing = step >= 2
  const validated = step >= 3
  const commented = step >= 4

  return (
    <div
      className={cn('relative', className)}
      // Decorative as a whole. The claims it illustrates are in the hero's
      // prose, which is what a screen reader should read instead of a
      // description of a picture of a diff.
      aria-hidden="true"
    >
      {/* Depth: three stacked planes at slightly different offsets. Static
          transforms — the "3D" is parallax between layers, not animation. */}
      <div className="pointer-events-none absolute inset-x-6 -bottom-3 top-8 rounded-xl border border-border/60 bg-surface-raised/40" />
      <div className="pointer-events-none absolute inset-x-3 -bottom-1.5 top-4 rounded-xl border border-border/80 bg-surface-raised/70" />

      {/* The illustration itself sits on a `TiltCard` (v0.6.2): pointer-tracked
          rotation of about three degrees, plus a sheen that follows the cursor.
          This is the one surface in the product that gets it — it is a picture of
          a thing rather than a thing to read, and the rotation makes the three
          stacked planes behind it resolve as depth instead of as three borders.
          `interactive={false}`: nothing here is clickable, so it must not lift
          like a button. Under reduced motion it degrades to a flat panel. */}
      <TiltCard interactive={false} className="relative overflow-hidden rounded-xl p-0">
        {/* -- header: what this is, and that it is not real ---------------- */}
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <IconPullRequest size={14} className="text-muted" />
            <span className="truncate font-mono text-xs text-muted">
              acme/web <span className="text-muted/60">#128</span>
            </span>
          </div>
          <span className="shrink-0 rounded border border-border px-1.5 py-[1px] text-micro uppercase tracking-wider text-muted">
            Illustration
          </span>
        </div>

        {/* -- the diff ----------------------------------------------------- */}
        <div className="px-1 py-2 font-mono text-[12px] leading-[1.7]">
          {DEMO_DIFF.map((line, index) => (
            <div
              key={line.n}
              className={cn(
                'flex items-start gap-2 px-3',
                line.kind === 'add' && 'bg-state-ok/[0.06]',
                // The flagged line lights up only once validation has passed —
                // the sequence is the argument: nothing is highlighted before
                // it has been checked.
                line.flag && validated && 'bg-severity-high/[0.1]',
                'transition-colors duration-panel ease-out',
                !reduced && 'animate-rise-in',
              )}
              style={reduced ? undefined : { animationDelay: `${140 + index * 60}ms` }}
            >
              <span className="w-6 shrink-0 select-none text-right text-muted/50">{line.n}</span>
              <span
                className={cn(
                  'w-2 shrink-0 select-none',
                  line.kind === 'add' ? 'text-state-ok' : 'text-transparent',
                )}
              >
                +
              </span>
              <span className="min-w-0 truncate text-content/90">{line.text}</span>
              {line.flag && validated ? (
                <span className="relative ml-auto shrink-0 pl-2">
                  <IconFinding size={12} className="text-severity-high" />
                  {/* One ring, once. It draws the eye to the anchored line at
                      the moment the finding appears, then stops. */}
                  <span className="absolute inset-0 rounded-full border border-severity-high/60 motion-safe:animate-pulse-ring" />
                </span>
              ) : null}
            </div>
          ))}
        </div>

        {/* -- the pipeline rail -------------------------------------------- */}
        <div className="border-t border-border px-4 py-3">
          <ol className="flex items-center gap-1.5">
            {STAGES.map((stage, index) => {
              const reached = step >= index + 1
              return (
                <li key={stage} className="flex min-w-0 flex-1 items-center gap-1.5">
                  <span
                    className={cn(
                      'h-1.5 w-1.5 shrink-0 rounded-full transition-colors duration-panel ease-out',
                      reached ? 'bg-accent' : 'bg-border',
                    )}
                  />
                  <span
                    className={cn(
                      'truncate text-micro uppercase tracking-wider transition-colors duration-panel ease-out',
                      reached ? 'text-content' : 'text-muted/50',
                    )}
                  >
                    {stage}
                  </span>
                  {index < STAGES.length - 1 ? (
                    <span className="h-px min-w-2 flex-1 bg-border" />
                  ) : null}
                </li>
              )
            })}
          </ol>

          {/* The analysis bar. A masked sweep, not a progress bar: it carries
              no percentage because there is no percentage to carry. */}
          <div className="relative mt-2.5 h-[3px] overflow-hidden rounded-full bg-border/70">
            {analysing && !commented && !reduced ? (
              <span className="absolute inset-y-0 w-1/3 rounded-full bg-accent/70 animate-sweep" />
            ) : null}
            {commented ? (
              <span className="absolute inset-0 rounded-full bg-accent/50 transition-opacity duration-panel" />
            ) : null}
          </div>
        </div>

        {/* -- the published comment ---------------------------------------- */}
        <div
          className={cn(
            'border-t border-border px-4 py-3 transition-[opacity,transform] duration-panel ease-out',
            commented ? 'opacity-100' : 'translate-y-1 opacity-0',
          )}
        >
          <div className="flex items-start gap-2.5">
            <span className="mt-[3px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-severity-high/40 text-severity-high">
              <IconFinding size={11} />
            </span>
            <div className="min-w-0">
              <p className="flex items-baseline gap-1.5 text-xs">
                <span className="font-medium text-severity-high">{DEMO_FINDING.severity}</span>
                <span className="truncate font-medium text-content">{DEMO_FINDING.title}</span>
              </p>
              <p className="mt-0.5 text-xs leading-snug text-muted">{DEMO_FINDING.detail}</p>
              {/* The anchor is the product's actual guarantee, so the
                  illustration shows it rather than only the prose. */}
              <p className="mt-1.5 flex items-center gap-1.5 text-micro text-muted">
                <IconCheck size={11} className="text-state-ok" />
                <span>
                  Anchored to line {DEMO_FINDING.line}
                  <span className="mx-1 text-muted/50">·</span>
                  <span className="font-mono">{DEMO_FINDING.anchor}</span>
                </span>
              </p>
            </div>
          </div>
        </div>
      </TiltCard>
    </div>
  )
}
