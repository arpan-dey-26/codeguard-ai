import { useEffect, useRef, useState } from 'react'
import { ChartFrame } from '@/charts/ChartFrame'
import type { TrendPoint } from '@/types/api'
import { formatDate } from '@/utils/format'

/**
 * Review activity over time.
 *
 * **The series are stacked bars, not lines.** Runs decompose into completed,
 * partial and failed; stacking shows both the total and the mix in one mark,
 * where three separate lines would ask the reader to add them mentally. The
 * bars are also honest about zero: the backend gap-fills, so a quiet day is a
 * gap in the bars rather than a straight line drawn across it.
 *
 * **Superseded runs are excluded from the stack** and reported in the summary
 * instead. A superseded run is work that was overtaken, not work that
 * happened, and including it would make a burst of pushes look like a busy
 * review day.
 *
 * Match the viewBox to the rendered width so narrow screens do not compress
 * the axis text. Reduce the number of date labels as the container narrows.
 */

const HEIGHT = 180
const PAD_LEFT = 28
const PAD_BOTTOM = 18
const PAD_TOP = 8

export function TrendChart({
  points,
  bucket,
  timezone,
}: {
  points: readonly TrendPoint[]
  bucket: string
  timezone: string
}) {
  const svg = useRef<SVGSVGElement>(null)
  const [width, setWidth] = useState(720)

  useEffect(() => {
    const node = svg.current
    if (!node) return
    const measure = () => setWidth(Math.max(1, node.getBoundingClientRect().width))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [points.length])

  if (points.length === 0) {
    return <p className="py-8 text-center text-sm text-muted">No activity in this range.</p>
  }

  const stacked = points.map((point) => ({
    point,
    total: point.runs_succeeded + point.runs_partial + point.runs_failed,
  }))
  // Three integer ticks: rounding a half-count would duplicate the top label.
  const max = Math.max(2, Math.ceil(Math.max(0, ...stacked.map((entry) => entry.total)) / 2) * 2)

  const plotWidth = Math.max(1, width - PAD_LEFT)
  const plotHeight = HEIGHT - PAD_BOTTOM - PAD_TOP
  const slot = plotWidth / points.length
  const barWidth = Math.max(1, Math.min(18, slot * 0.7))

  const totals = stacked.reduce(
    (acc, entry) => ({
      completed: acc.completed + entry.point.runs_succeeded,
      partial: acc.partial + entry.point.runs_partial,
      failed: acc.failed + entry.point.runs_failed,
      superseded: acc.superseded + entry.point.runs_superseded,
    }),
    { completed: 0, partial: 0, failed: 0, superseded: 0 },
  )

  const summary =
    `Review attempts per ${bucket} in ${timezone}. ` +
    `${totals.completed} completed, ${totals.partial} completed with partial publication, ` +
    `${totals.failed} failed across ${points.length} ${bucket} buckets. ` +
    `${totals.superseded} further attempts were superseded and are not plotted.`

  const y = (value: number) => PAD_TOP + plotHeight - (value / max) * plotHeight

  // At most six labels, whatever the range: a daily axis over ninety days
  // would otherwise draw ninety overlapping dates.
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(plotWidth / 110))))

  return (
    <ChartFrame
      title="Review activity"
      summary={summary}
      valueHeader="Attempts"
      data={points.map((point) => ({
        key: point.bucket_start,
        label: formatDate(point.bucket_start),
        value: point.runs_succeeded + point.runs_partial + point.runs_failed,
      }))}
      footnote={
        <>
          Buckets are {bucket}s in {timezone}. Superseded attempts are excluded —
          they were overtaken by a newer commit rather than completed.
        </>
      }
    >
      <svg
        ref={svg}
        viewBox={`0 0 ${width} ${HEIGHT}`}
        className="h-44 w-full"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={PAD_LEFT}
              x2={width}
              y1={y(max * fraction)}
              y2={y(max * fraction)}
              className="stroke-grid"
              strokeWidth={1}
            />
            <text
              x={PAD_LEFT - 6}
              y={y(max * fraction) + 3}
              textAnchor="end"
              className="fill-muted text-[11px]"
            >
              {Math.round(max * fraction)}
            </text>
          </g>
        ))}

        {stacked.map((entry, index) => {
          const x = PAD_LEFT + index * slot + (slot - barWidth) / 2
          const { runs_succeeded, runs_partial, runs_failed } = entry.point
          const segments: Array<{ value: number; className: string }> = [
            { value: runs_succeeded, className: 'fill-state-ok' },
            { value: runs_partial, className: 'fill-state-warn' },
            { value: runs_failed, className: 'fill-state-bad' },
          ]
          let cursor = 0
          return (
            <g key={entry.point.bucket_start}>
              {segments.map((segment, segmentIndex) => {
                if (segment.value === 0) return null
                const top = y(cursor + segment.value)
                const height = y(cursor) - top
                cursor += segment.value
                return (
                  <rect
                    key={segmentIndex}
                    x={x}
                    y={top}
                    width={barWidth}
                    height={Math.max(1, height)}
                    className={segment.className}
                  />
                )
              })}
            </g>
          )
        })}

        {points.map((point, index) =>
          index % labelEvery === 0 ? (
            <text
              key={point.bucket_start}
              x={Math.max(PAD_LEFT + 45, Math.min(width - 45, PAD_LEFT + index * slot + slot / 2))}
              y={HEIGHT - 4}
              textAnchor="middle"
              className="fill-muted text-[11px]"
            >
              {formatDate(point.bucket_start)}
            </text>
          ) : null,
        )}
      </svg>

      <ul className="mt-2 flex flex-wrap gap-4 text-xs text-muted">
        <LegendSwatch className="bg-state-ok" label={`Completed (${totals.completed})`} />
        <LegendSwatch className="bg-state-warn" label={`Partial (${totals.partial})`} />
        <LegendSwatch className="bg-state-bad" label={`Failed (${totals.failed})`} />
      </ul>
    </ChartFrame>
  )
}

function LegendSwatch({ className, label }: { className: string; label: string }) {
  return (
    <li className="flex items-center gap-1.5">
      <span className={`inline-block h-2 w-2 rounded-sm ${className}`} aria-hidden="true" />
      {label}
    </li>
  )
}
