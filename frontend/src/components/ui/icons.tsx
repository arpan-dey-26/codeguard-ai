/**
 * The icon set.
 *
 * Inline SVG rather than an icon package, for one hard reason and one good one.
 * The hard one: the npm registry is unreachable in this project's environment,
 * so no library could be installed. The good one: the application needs
 * fourteen icons, and fourteen hand-drawn paths cost less than a dependency and
 * its tree-shaking configuration.
 *
 * Every icon follows the same construction, which is what makes a set look like
 * a set rather than a collection:
 *
 *   - a 24×24 coordinate space, rendered at 16px by default
 *   - 1.5 stroke width, round caps and joins
 *   - `currentColor`, so colour comes from the text colour of the context
 *   - geometry on the pixel grid at 16px where possible
 *
 * **They are decorative by default.** `aria-hidden` is on unless a `title` is
 * given, because an icon beside a text label that announces itself makes a
 * screen reader say everything twice. An icon that is the *only* content of a
 * control takes a `title`, and then it is exposed with `role="img"`.
 */

import type { ReactNode } from 'react'
import { cn } from '@/utils/cn'

export interface IconProps {
  className?: string
  /** Pixel size for width and height. Default 16. */
  size?: number
  /** Accessible name. Provide it only when the icon carries meaning alone. */
  title?: string
}

function Svg({
  children,
  className,
  size = 16,
  title,
  fill = false,
}: IconProps & { children: ReactNode; fill?: boolean }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={fill ? 'currentColor' : 'none'}
      stroke={fill ? 'none' : 'currentColor'}
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      // `shrink-0` on every icon: inside a flex row a bare <svg> is a flex item
      // and will be squashed by a long label beside it.
      className={cn('shrink-0', className)}
      aria-hidden={title === undefined ? true : undefined}
      role={title === undefined ? undefined : 'img'}
      focusable="false"
    >
      {title === undefined ? null : <title>{title}</title>}
      {children}
    </svg>
  )
}

/* -- navigation ----------------------------------------------------------- */

export function IconDashboard(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="3" width="7.5" height="4.5" rx="1.5" />
      <rect x="13.5" y="10.5" width="7.5" height="10.5" rx="1.5" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
    </Svg>
  )
}

export function IconRepository(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 3h11a2 2 0 0 1 2 2v12H7a2 2 0 0 0-2 2V5a2 2 0 0 1 2-2Z" />
      <path d="M5 19a2 2 0 0 0 2 2h12" />
      <path d="M9.5 7.5h6" />
    </Svg>
  )
}

export function IconPullRequest(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="6.5" cy="5.5" r="2.5" />
      <circle cx="6.5" cy="18.5" r="2.5" />
      <path d="M6.5 8v8" />
      <circle cx="17.5" cy="18.5" r="2.5" />
      <path d="M17.5 16V9.5a3 3 0 0 0-3-3h-2.5" />
      <path d="m14 4 -2 2.5 2 2.5" />
    </Svg>
  )
}

export function IconReview(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H9l-5 4V5.5Z" />
      <path d="m8.5 9.5 2 2 4.5-4" />
    </Svg>
  )
}

export function IconFinding(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3.5 21 19H3l9-15.5Z" />
      <path d="M12 9.5v4" />
      <path d="M12 16.5h.01" />
    </Svg>
  )
}

export function IconSettings(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.5v3M12 18.5v3M4.2 7l2.6 1.5M17.2 15.5l2.6 1.5M4.2 17l2.6-1.5M17.2 8.5 19.8 7" />
    </Svg>
  )
}

export function IconSystem(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="4" width="18" height="7" rx="1.5" />
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M6.5 7.5h.01M6.5 16.5h.01" />
    </Svg>
  )
}

/* -- affordances ---------------------------------------------------------- */

export function IconChevronRight(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="m9.5 5.5 6.5 6.5-6.5 6.5" />
    </Svg>
  )
}

export function IconChevronDown(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="m5.5 9.5 6.5 6.5 6.5-6.5" />
    </Svg>
  )
}

export function IconExternal(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14 4.5h5.5V10" />
      <path d="M19.5 4.5 11 13" />
      <path d="M18 14.5v4a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6h4" />
    </Svg>
  )
}

export function IconRefresh(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M20 12a8 8 0 1 1-2.6-5.9" />
      <path d="M20 4v4h-4" />
    </Svg>
  )
}

export function IconSearch(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m15.5 15.5 4 4" />
    </Svg>
  )
}

export function IconClose(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="m6 6 12 12M18 6 6 18" />
    </Svg>
  )
}

export function IconMenu(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </Svg>
  )
}

/* -- state ---------------------------------------------------------------- */

export function IconCheck(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="m5 12.5 4.5 4.5L19 7" />
    </Svg>
  )
}

export function IconAlert(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 8v4.5" />
      <path d="M12 16h.01" />
    </Svg>
  )
}

export function IconInfo(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5" />
      <path d="M12 8h.01" />
    </Svg>
  )
}

export function IconOffline(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 3l18 18" />
      <path d="M8.5 16.5a5 5 0 0 1 7 0" />
      <path d="M5 13a9.5 9.5 0 0 1 3-2.1" />
      <path d="M16 10.9A9.5 9.5 0 0 1 19 13" />
      <path d="M2 9.5A14 14 0 0 1 9 6.3" />
      <path d="M15.5 6.4A14 14 0 0 1 22 9.5" />
      <path d="M12 20h.01" />
    </Svg>
  )
}

/** The default mark for an empty state: a tray with nothing in it. */
export function IconInbox(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 13.5 5.8 6.2A2 2 0 0 1 7.7 4.8h8.6a2 2 0 0 1 1.9 1.4l2.3 7.3" />
      <path d="M3.5 13.5h4l1.2 2.2h6.6l1.2-2.2h4v3.7a2 2 0 0 1-2 2H5.5a2 2 0 0 1-2-2v-3.7Z" />
    </Svg>
  )
}

export function IconSun(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M5.2 18.8l1.4-1.4M17.4 6.6l1.4-1.4" />
    </Svg>
  )
}

export function IconMoon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
    </Svg>
  )
}

/**
 * A spinner, for the inside of a control that is working.
 *
 * Rotation is the one place a continuous animation is right: it means "still
 * going" and nothing else conveys that without inventing a percentage. Stops
 * under reduced motion, where the arc alone reads as a busy indicator, and the
 * control's `aria-busy` carries the state either way.
 */
export function IconSpinner({ className, size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={cn('shrink-0 motion-safe:animate-spin', className)}
      aria-hidden
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity={0.25} strokeWidth={2.5} />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth={2.5}
        strokeLinecap="round"
      />
    </svg>
  )
}
