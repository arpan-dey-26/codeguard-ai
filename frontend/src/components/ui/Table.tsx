import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/utils/cn'

/**
 * A dense data table that survives a narrow viewport.
 *
 * **The responsive strategy is horizontal scroll with a sticky first column,
 * not shrinking.** A table squeezed to 360px wraps every cell to three lines
 * and becomes unreadable; a table that scrolls stays legible and the anchor
 * column keeps the reader oriented. Columns that do not earn their space on a
 * phone are marked `priority: 'low'` and hidden below `md` — hiding a column
 * is honest, crushing one is not.
 *
 * Semantic `<table>` throughout, because a grid built from `<div>`s loses
 * every row/column relationship a screen reader uses to announce a cell.
 */

export interface Column<T> {
  key: string
  header: ReactNode
  /** `low` columns are hidden below the `md` breakpoint. */
  priority?: 'high' | 'normal' | 'low'
  align?: 'left' | 'right'
  width?: string
  render: (row: T) => ReactNode
}

const PRIORITY_CLASS: Record<string, string> = {
  low: 'hidden md:table-cell',
  normal: '',
  high: '',
}

export function Table<T>({
  columns,
  rows,
  rowKey,
  caption,
  onRowHref,
  emptyLabel = 'Nothing to show.',
  isRowFocused,
}: {
  columns: ReadonlyArray<Column<T>>
  rows: readonly T[]
  rowKey: (row: T) => string
  /** Announced to screen readers; visually hidden. */
  caption: string
  /** When set, the whole row becomes a link target via its first cell. */
  onRowHref?: (row: T) => string
  emptyLabel?: string
  /**
   * Marks a row as related to whatever the pointer is on elsewhere on the page
   * (v0.6.2) — hovering a finding highlights the file it is anchored in.
   *
   * Presentation only: it changes a background, never what the row contains or
   * whether it can be reached, so a keyboard or screen-reader user loses
   * nothing by never triggering it.
   */
  isRowFocused?: ((row: T) => boolean) | undefined
}) {
  if (rows.length === 0) {
    return <p className="py-8 text-center text-sm text-muted">{emptyLabel}</p>
  }

  return (
    <div className="-mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-border text-left">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                style={column.width ? { width: column.width } : undefined}
                className={cn(
                  'whitespace-nowrap px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted',
                  column.align === 'right' && 'text-right',
                  PRIORITY_CLASS[column.priority ?? 'normal'],
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={cn(
                'border-b border-border/60 transition-colors duration-quick ease-out last:border-0',
                'hover:bg-surface-raised',
                isRowFocused?.(row) && 'bg-accent/[0.07]',
              )}
            >
              {columns.map((column, index) => (
                <td
                  key={column.key}
                  className={cn(
                    'px-3 py-2 align-top',
                    column.align === 'right' && 'text-right tabular-nums',
                    PRIORITY_CLASS[column.priority ?? 'normal'],
                  )}
                >
                  {index === 0 && onRowHref ? (
                    // `Link`, not `<a href>` (v0.6.1). Every row target here is
                    // an internal route, and a raw anchor made each one a full
                    // document load: the bundle re-parsed, the session was
                    // re-probed from scratch, and the screen flashed white on
                    // the way to a page the router could have rendered in
                    // place. External links — GitHub, the published comment —
                    // stay as anchors, which is correct for them.
                    <Link
                      to={onRowHref(row)}
                      className="block rounded-sm hover:text-accent focus-visible:text-accent"
                    >
                      {column.render(row)}
                    </Link>
                  ) : (
                    column.render(row)
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
