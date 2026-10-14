import type { ReactNode } from 'react'

/**
 * The accessibility wrapper every chart in this application sits inside.
 *
 * **A chart that only exists as pixels excludes everyone who cannot see it.**
 * Three things make that not true here, and they are mandatory rather than
 * optional:
 *
 * 1. `role="img"` plus an `aria-label` summary — a screen reader announces
 *    what the chart shows, not "graphic".
 * 2. A `<details>` data table containing every plotted value. It is the same
 *    numbers, reachable by keyboard, and it is also what a sighted user opens
 *    when they need the exact figure the bar only approximates.
 * 3. The summary text is written by the caller from the real data, so it
 *    cannot drift from what is drawn.
 *
 * Charts are drawn with inline SVG rather than a charting library, because no
 * library can be installed here (the npm registry returns 403). The trade is
 * recorded honestly: these are simple, deliberate charts — bars, a line, a
 * funnel — not a general plotting layer.
 */

export interface ChartDatum {
  key: string
  label: string
  value: number
}

export function ChartFrame({
  title,
  summary,
  data,
  valueHeader = 'Count',
  children,
  footnote,
}: {
  title: string
  /** One sentence describing what the chart shows, built from the real data. */
  summary: string
  /** The plotted values, rendered as the keyboard-accessible fallback table. */
  data: readonly ChartDatum[]
  valueHeader?: string
  children: ReactNode
  footnote?: ReactNode
}) {
  return (
    <figure className="m-0">
      <div role="img" aria-label={`${title}. ${summary}`}>
        {children}
      </div>
      {footnote ? <figcaption className="mt-2 text-xs text-muted">{footnote}</figcaption> : null}
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-muted hover:text-content">
          View as table
        </summary>
        <table className="mt-2 w-full border-collapse text-xs">
          <caption className="sr-only">{title}</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-1 pr-3 font-medium text-muted">
                Category
              </th>
              <th scope="col" className="py-1 text-right font-medium text-muted">
                {valueHeader}
              </th>
            </tr>
          </thead>
          <tbody>
            {data.map((datum) => (
              <tr key={datum.key} className="border-b border-border/50 last:border-0">
                <th scope="row" className="py-1 pr-3 text-left font-normal text-content">
                  {datum.label}
                </th>
                <td className="py-1 text-right tabular-nums text-content">{datum.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  )
}

/** Series colours, bound to the same CSS variables as the rest of the UI. */
export const SERIES_CLASS = [
  'fill-series-1',
  'fill-series-2',
  'fill-series-3',
  'fill-series-4',
] as const

export const SERIES_STROKE = [
  'stroke-series-1',
  'stroke-series-2',
  'stroke-series-3',
  'stroke-series-4',
] as const
