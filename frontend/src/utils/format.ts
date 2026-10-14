/**
 * Display formatting.
 *
 * **The canonical timestamp is always preserved.** Every relative time is
 * rendered inside an element carrying the exact ISO value in `dateTime` and a
 * full absolute time in `title`, so "2 hours ago" is a convenience and never
 * the only thing the page knows. A dashboard that shows only relative times is
 * useless the moment someone needs to correlate an event with a log line.
 *
 * The backend sends UTC with an explicit offset; the browser renders local
 * time. Those are the only two zones in play, and neither is ever implied.
 */

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

const RELATIVE_UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ['year', 365 * 24 * 60 * 60 * 1000],
  ['month', 30 * 24 * 60 * 60 * 1000],
  ['day', 24 * 60 * 60 * 1000],
  ['hour', 60 * 60 * 1000],
  ['minute', 60 * 1000],
  ['second', 1000],
]

export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'

  const delta = date.getTime() - now
  const absolute = Math.abs(delta)
  if (absolute < 45_000) return 'just now'

  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  for (const [unit, ms] of RELATIVE_UNITS) {
    if (absolute >= ms) return formatter.format(Math.round(delta / ms), unit)
  }
  return 'just now'
}

/**
 * Durations, at the precision the number deserves.
 *
 * Milliseconds below a second, seconds below a minute, then minutes. Rendering
 * "61000 ms" asks the reader to do arithmetic to learn that a review took a
 * minute.
 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat().format(value)
}

/** A short commit reference. Seven characters is git's own default. */
export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : '—'
}

/**
 * A path shortened from the left, keeping the file name.
 *
 * The end of a path identifies the file; the start is shared by everything in
 * the directory. Truncating from the right hides the only part that varies.
 */
export function shortPath(path: string, max = 48): string {
  if (path.length <= max) return path
  return `…${path.slice(path.length - max + 1)}`
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/** Turn `line_not_in_diff` into `Line not in diff`. */
export function humanise(token: string | null | undefined): string {
  if (!token) return '—'
  const spaced = token.replace(/[_-]+/g, ' ').trim()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** The ISO date N days before today, for a default filter range. */
export function isoDaysAgo(days: number, now = new Date()): string {
  const date = new Date(now.getTime() - days * 24 * 60 * 60 * 1000)
  return new Date(
    Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()),
  ).toISOString()
}
