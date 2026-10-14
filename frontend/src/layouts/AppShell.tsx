import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  IconClose,
  IconDashboard,
  IconFinding,
  IconMenu,
  IconMoon,
  IconPullRequest,
  IconRepository,
  IconReview,
  IconSettings,
  IconSun,
  IconSystem,
  type IconProps,
} from '@/components/ui/icons'
import { Tooltip } from '@/components/ui/Tooltip'
import { useAuth } from '@/hooks/useAuth'
import { useReducedMotion } from '@/hooks/useMotion'
import { useTheme } from '@/hooks/useTheme'
import { cn } from '@/utils/cn'

/**
 * Application shell: primary navigation plus a routed outlet.
 *
 * **The navigation collapses rather than shrinks.** Below `md` the sidebar
 * becomes a disclosure button; a horizontal strip of seven links either wraps
 * into an unusable block or scrolls sideways where nobody looks for it.
 *
 * Every navigation item points at a page that exists and has data behind it.
 * A link to an empty page is worse than no link, because it teaches the user
 * that parts of the product do not work.
 *
 * **The active indicator is a rail, not a fill (v0.6.2).** A 2px bar on the
 * left edge of the active item, with the label going from muted to full
 * contrast. A filled pill was the previous treatment and it competed with the
 * page's own cards for weight; a rail reads as "you are here" without becoming
 * the loudest thing on screen. It does not slide between items: a shared
 * animated indicator has to measure both positions, and on a route change that
 * measurement lands mid-navigation and judders. Colour crossfades instead,
 * which is honest about being instantaneous.
 */

interface NavItem {
  readonly to: string
  readonly label: string
  readonly end: boolean
  readonly Icon: (props: IconProps) => React.ReactElement
  readonly hint: string
}

const NAV: readonly NavItem[] = [
  {
    to: '/',
    label: 'Dashboard',
    end: true,
    Icon: IconDashboard,
    hint: 'Review activity and finding totals across every installation you can see',
  },
  {
    to: '/repositories',
    label: 'Repositories',
    end: false,
    Icon: IconRepository,
    hint: 'Repositories the GitHub App is installed on',
  },
  {
    to: '/pull-requests',
    label: 'Pull requests',
    end: false,
    Icon: IconPullRequest,
    hint: 'Pull requests CodeGuard has seen',
  },
  {
    to: '/review-runs',
    label: 'Reviews',
    end: false,
    Icon: IconReview,
    hint: 'Every review run, including the ones that failed',
  },
  {
    to: '/findings',
    label: 'Findings',
    end: false,
    Icon: IconFinding,
    hint: 'Validated findings, filterable by severity and repository',
  },
  {
    to: '/settings',
    label: 'Settings',
    end: false,
    Icon: IconSettings,
    hint: "This deployment's non-secret configuration",
  },
  {
    to: '/system',
    label: 'System',
    end: false,
    Icon: IconSystem,
    hint: 'Live health of the backend and its integrations',
  },
]

export function AppShell() {
  const { theme, toggle } = useTheme()
  const { state, signOut } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const [signOutError, setSignOutError] = useState<string | null>(null)
  const location = useLocation()
  const reduced = useReducedMotion()

  // The route transition. `key` on the animated wrapper restarts a short fade
  // on every navigation; there is no exit animation, because waiting for one
  // makes navigation feel slower than it is — the new page should already be
  // arriving. 200ms of opacity, nothing that moves layout.
  const main = useRef<HTMLElement | null>(null)

  // Close the mobile menu on navigation. Leaving it open after a route change
  // covers the page the person just asked for.
  useEffect(() => setMenuOpen(false), [location.pathname])

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <a
        href="#main"
        className="sr-only z-skip-link focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:rounded-md focus:bg-surface-raised focus:px-3 focus:py-2 focus:shadow-overlay"
      >
        Skip to content
      </a>

      <nav
        aria-label="Primary"
        className={cn(
          'z-nav shrink-0 border-b border-border bg-surface px-4 py-3',
          'md:sticky md:top-0 md:h-screen md:w-[220px] md:border-b-0 md:border-r md:px-3 md:py-5',
          'md:flex md:flex-col',
        )}
      >
        <div className="flex items-center justify-between md:mb-6 md:block">
          <span className="flex items-center gap-2 font-mono text-sm font-semibold tracking-tight">
            <svg width={17} height={17} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path
                d="M12 2.8l7 2.6v6.1c0 4.3-2.9 8-7 9.7-4.1-1.7-7-5.4-7-9.7V5.4l7-2.6Z"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.6}
                strokeLinejoin="round"
                className="text-accent"
              />
              <path
                d="m8.6 12.2 2.5 2.5 4.3-4.6"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
                className="text-accent"
              />
            </svg>
            CodeGuard AI
          </span>
          <button
            type="button"
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted',
              'transition-colors duration-instant ease-out hover:text-content md:hidden',
            )}
            aria-expanded={menuOpen}
            aria-controls="primary-nav-items"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <IconClose size={14} /> : <IconMenu size={14} />}
            {menuOpen ? 'Close' : 'Menu'}
          </button>
        </div>

        <ul
          id="primary-nav-items"
          className={cn('mt-3 flex-col gap-0.5 md:mt-0 md:flex', menuOpen ? 'flex' : 'hidden')}
        >
          {NAV.map((item, index) => (
            <li key={item.to} style={menuOpen && !reduced ? { '--stagger': index } : undefined}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    'group relative flex items-center gap-2.5 rounded-md py-1.5 pl-3 pr-2 text-sm',
                    'transition-colors duration-instant ease-out',
                    isActive
                      ? 'font-medium text-content'
                      : 'text-muted hover:bg-surface-raised hover:text-content',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {/* The rail. Scales from the centre on activation, which
                        reads as the indicator arriving rather than blinking. */}
                    <span
                      aria-hidden="true"
                      className={cn(
                        'absolute inset-y-1 left-0 w-[2px] origin-center rounded-full bg-accent',
                        'transition-transform duration-quick ease-out',
                        isActive ? 'scale-y-100' : 'scale-y-0',
                      )}
                    />
                    <item.Icon
                      size={15}
                      className={cn(
                        'transition-colors duration-instant ease-out',
                        isActive ? 'text-accent' : 'text-muted group-hover:text-content',
                      )}
                    />
                    <span className="truncate">{item.label}</span>
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>

        <div
          className={cn(
            'mt-4 space-y-2 md:mt-auto md:pt-6',
            menuOpen ? 'block' : 'hidden md:block',
          )}
        >
          <Tooltip
            content={`Switch to the ${theme === 'dark' ? 'light' : 'dark'} theme`}
            side="top"
          >
            <button
              type="button"
              onClick={toggle}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              className={cn(
                'inline-flex w-full items-center gap-2 rounded-md border border-border px-3 py-1.5 text-xs text-muted',
                'transition-colors duration-instant ease-out hover:bg-surface-raised hover:text-content',
              )}
            >
              {theme === 'dark' ? <IconSun size={14} /> : <IconMoon size={14} />}
              {theme === 'dark' ? 'Light theme' : 'Dark theme'}
            </button>
          </Tooltip>

          {state.status === 'open' && (
            <div className="rounded-md border border-state-warn/40 bg-state-warn/[0.06] px-3 py-2">
              <p className="text-xs font-medium text-state-warn">Not signed in</p>
              <p className="mt-0.5 text-micro leading-snug text-muted">Unauthenticated reads</p>
            </div>
          )}

          {state.status === 'authenticated' && (
            <div className="panel px-3 py-2.5">
              <p className="truncate text-sm font-medium" title={state.me.user.login}>
                {state.me.user.name ?? state.me.user.login}
              </p>
              {/* The number of installations, because it is the one thing that
                  explains an unexpectedly empty dashboard: a signed-in user
                  with no App access sees nothing, and that is correct rather
                  than broken. */}
              <p className="mt-0.5 text-micro text-muted">
                {state.me.installations.length === 0
                  ? 'No installations — nothing to show'
                  : `${state.me.installations.length} installation${
                      state.me.installations.length === 1 ? '' : 's'
                    }`}
              </p>
              <button
                type="button"
                disabled={signingOut}
                onClick={() => {
                  setSigningOut(true)
                  setSignOutError(null)
                  void signOut()
                    .catch(() =>
                      setSignOutError(
                        'Sign-out failed. Your session may still be active. Please try again.',
                      ),
                    )
                    .finally(() => setSigningOut(false))
                }}
                className={cn(
                  'mt-2.5 w-full rounded-md border border-border px-3 py-1 text-xs text-muted',
                  'transition-colors duration-instant ease-out hover:bg-surface hover:text-content',
                  'disabled:cursor-not-allowed disabled:opacity-60',
                )}
              >
                {signingOut ? 'Signing out…' : 'Sign out'}
              </button>
              {signOutError && (
                <p role="alert" className="mt-2 text-xs text-state-bad">
                  {signOutError}
                </p>
              )}
            </div>
          )}
        </div>
      </nav>

      <main id="main" ref={main} tabIndex={-1} className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        {/* Persistent, undismissable, and on every page (v0.6.1). A deployment
            serving review data with no authentication must never look like one
            that checked. A toast on first load is precisely how it would. */}
        {state.status === 'open' ? (
          <div role="status" className="panel mb-6 border-state-warn/40 bg-state-warn/[0.06] p-3">
            <p className="text-sm font-medium text-state-warn">
              No session — unauthenticated reads are enabled
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              You are not signed in. This deployment has{' '}
              <code className="font-mono">DASHBOARD_ALLOW_UNAUTHENTICATED_READS</code> set, so every
              monitored repository&rsquo;s review data is served to anyone who can reach this port.
              It is a development convenience; the backend refuses to start in production with it
              on.
              {state.signInAvailable ? ' Sign-in is configured and available.' : ''}
            </p>
          </div>
        ) : null}

        {/* The route transition. Keyed on the path so React remounts the
            wrapper and the CSS entrance replays. Opacity only — a translate
            here would fight the per-section `.stagger` entrances inside the
            page and read as two competing animations. */}
        <div key={location.pathname} className={reduced ? undefined : 'animate-fade-in'}>
          <Outlet />
        </div>
      </main>
    </div>
  )
}
