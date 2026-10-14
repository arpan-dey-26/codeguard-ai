import { ChartFrame, type ChartDatum } from '@/charts/ChartFrame'
import { cn } from '@/utils/cn'

/**
 * A horizontal bar list.
 *
 * **Horizontal, not vertical**, because every category here has a text label —
 * severity, category, rejection reason — and vertical bars force those labels
 * to rotate or truncate. Horizontal bars give each label a whole line.
 *
 * **The value is printed next to every bar.** A bar communicates proportion;
 * a reader who needs the number should not have to estimate it against an
 * axis. That also means the chart degrades gracefully: at any width, the
 * numbers are still readable even if the bars are short.
 *
 * Built from `div`s rather than SVG because a bar list is a layout, not a
 * drawing, and flexbox handles the responsive case for free.
 */

export interface BarListItem extends ChartDatum {
  /** A Tailwind text/background colour class from the token set. */
  className?: string
}

export function BarList({
  title,
  items,
  summary,
  emptyLabel = 'No data in this range.',
  footnote,
}: {
  title: string
  items: readonly BarListItem[]
  /** Overrides the generated summary when the caller can say it better. */
  summary?: string
  emptyLabel?: string
  footnote?: React.ReactNode
}) {
  const total = items.reduce((sum, item) => sum + item.value, 0)
  const max = items.reduce((best, item) => Math.max(best, item.value), 0)

  if (total === 0) {
    return <p className="py-6 text-center text-sm text-muted">{emptyLabel}</p>
  }

  const generated =
    `${total} in total across ${items.filter((i) => i.value > 0).length} categories. ` +
    items
      .filter((item) => item.value > 0)
      .map((item) => `${item.label}: ${item.value}`)
      .join(', ') +
    '.'

  return (
    <ChartFrame
      title={title}
      summary={summary ?? generated}
      data={items}
      footnote={footnote}
    >
      <ul className="space-y-1.5">
        {items.map((item) => (
          <li key={item.key} className="flex items-center gap-3 text-sm">
            <span className="w-28 shrink-0 truncate text-muted sm:w-40" title={item.label}>
              {item.label}
            </span>
            <span className="h-2 min-w-0 flex-1 overflow-hidden rounded-sm bg-border/50">
              <span
                className={cn('block h-full rounded-sm', item.className ?? 'bg-series-1')}
                style={{ width: max === 0 ? '0%' : `${Math.max(2, (item.value / max) * 100)}%` }}
              />
            </span>
            <span className="w-10 shrink-0 text-right tabular-nums text-content">
              {item.value}
            </span>
          </li>
        ))}
      </ul>
    </ChartFrame>
  )
}
