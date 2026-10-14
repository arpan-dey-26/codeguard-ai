/**
 * Severity presentation.
 *
 * Every severity carries a colour token, a label AND a glyph. The UI must
 * never communicate severity by colour alone (architecture.md NFR-U2), so the
 * label and glyph are part of the same record rather than left to each
 * component to remember.
 *
 * **Why `fillClassName` is spelled out instead of derived (v0.6.2).** The
 * finding card's severity rail briefly built its class with
 * `className.replace('text-', 'bg-')`. That renders nothing in production:
 * Tailwind generates utilities by scanning source text, and a class assembled at
 * runtime appears in no file for it to find. It *looked* fine only because
 * `DashboardPage` happens to list all five `bg-severity-*` classes literally for
 * its bar chart — so the rail's colour depended on an unrelated chart helper on
 * another page continuing to exist. Both spellings now live here, literally, and
 * are safe to grep for.
 */

import type { Severity } from '@/types/domain'

interface SeverityPresentation {
  label: string
  glyph: string
  /** Foreground colour: the badge glyph and label. */
  className: string
  /** Background colour: rails, bars, fills. Never derived from `className`. */
  fillClassName: string
  weight: number
}

export const SEVERITY: Record<Severity, SeverityPresentation> = {
  critical: {
    label: 'Critical',
    glyph: '■',
    className: 'text-severity-critical',
    fillClassName: 'bg-severity-critical',
    weight: 100,
  },
  high: {
    label: 'High',
    glyph: '▲',
    className: 'text-severity-high',
    fillClassName: 'bg-severity-high',
    weight: 60,
  },
  medium: {
    label: 'Medium',
    glyph: '◆',
    className: 'text-severity-medium',
    fillClassName: 'bg-severity-medium',
    weight: 30,
  },
  low: {
    label: 'Low',
    glyph: '●',
    className: 'text-severity-low',
    fillClassName: 'bg-severity-low',
    weight: 12,
  },
  info: {
    label: 'Info',
    glyph: '○',
    className: 'text-severity-info',
    fillClassName: 'bg-severity-info',
    weight: 4,
  },
}

export const SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low', 'info']
