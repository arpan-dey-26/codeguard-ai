/**
 * The unauthenticated entry screen.
 *
 * This is the only page in the application a stranger can reach, so it has two
 * jobs: say what CodeGuard AI does, and get the person to the right next action.
 * It replaces a centred card that did neither — it said "Review history and
 * code-quality metrics for the repositories your GitHub App installation
 * covers", which describes a feature list to someone who does not yet know what
 * the product is.
 *
 * **The claim it makes is the narrow, true one.** Not "AI reviews your code" —
 * plenty of things do that, badly. The claim is that every finding is checked
 * against the real diff before it is published, and that the ones that fail are
 * recorded rather than dropped. That is what this project actually built, and it
 * is the only thing worth leading with.
 *
 * **What it does not do.** No testimonial, no logo wall, no metric. There are no
 * customers and no benchmark, so there is nothing honest to put in those slots,
 * and a hero with a fabricated "10,000 reviews analysed" is worse than a hero
 * with an empty column.
 *
 * The entrance is a coordinated stagger — shell, eyebrow, headline, copy,
 * actions, then the visualisation — built from the `.stagger` utility so the
 * whole sequence is CSS and costs nothing at runtime.
 */

import { PipelineDemo } from '@/components/marketing/PipelineDemo'
import { IconAlert, IconCheck, IconExternal, IconOffline, IconRefresh } from '@/components/ui/icons'
import { Button } from '@/components/ui/primitives'
import { cn } from '@/utils/cn'

/** The three guarantees, in the order they happen in the pipeline. */
const GUARANTEES = [
  {
    title: 'Only the changed code leaves your repository',
    body: 'Touched declarations and a bounded window around them. Nothing is cloned, nothing is executed, and no whole file is ever sent to a model.',
  },
  {
    title: 'Every claim is checked against the real diff',
    body: 'Fourteen gates: the file must be one the model was shown, at the commit under review, on a line in the diff, quoting source that is actually there.',
  },
  {
    title: 'Rejected claims are kept, not discarded',
    body: 'Each refusal is stored with the specific gate that caught it — so "is the model getting better?" is a question with an answer.',
  },
] as const

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main id="main" className="relative min-h-screen overflow-hidden px-4 py-10 sm:px-6 lg:py-16">
      {/* Static backdrop: one radial wash and a masked grid. It sits behind
          everything, animates never, and fades out before it reaches the
          text — an animated field behind a headline is the fastest way to make
          a page feel cheap and unreadable. */}
      <div className="hero-field" />
      <div className="relative mx-auto w-full max-w-6xl">{children}</div>
    </main>
  )
}

function Wordmark() {
  return (
    <div className="flex items-center gap-2">
      {/* The mark: a shield outline with a check, drawn here rather than
          imported, because the project has no icon package and this is the one
          piece of identity it needs. */}
      <svg width={20} height={20} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path
          d="M12 2.8l7 2.6v6.1c0 4.3-2.9 8-7 9.7-4.1-1.7-7-5.4-7-9.7V5.4l7-2.6Z"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinejoin="round"
          className="text-accent"
        />
        <path
          d="m8.6 12.2 2.5 2.5 4.3-4.6"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.7}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-accent"
        />
      </svg>
      <span className="font-mono text-sm font-semibold tracking-tight text-content">
        CodeGuard AI
      </span>
    </div>
  )
}

export function Hero({
  /** What the server said about sign-in. Drives which action is offered. */
  mode,
  onSignIn,
}: {
  mode: 'sign-in' | 'not-configured'
  onSignIn: () => void
}) {
  return (
    <Shell>
      <header className="stagger mb-10 flex items-center justify-between lg:mb-14">
        <Wordmark />
        <a
          href="https://docs.github.com/en/apps"
          target="_blank"
          rel="noreferrer noopener"
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted',
            'transition-colors duration-instant ease-out hover:text-content',
          )}
        >
          GitHub Apps
          <IconExternal size={12} />
        </a>
      </header>

      <div className="grid items-start gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-14">
        {/* -- the argument ------------------------------------------------- */}
        <div className="min-w-0">
          <p
            className="stagger text-micro font-medium uppercase tracking-[0.14em] text-accent"
            style={{ '--stagger': 2 }}
          >
            Pull-request review
          </p>

          <h1
            className="stagger mt-3 text-display font-semibold text-content"
            style={{ '--stagger': 3 }}
          >
            AI code review that{' '}
            {/* The break is a typographic choice for the two-line desktop
                headline, not part of the sentence. Below `sm` the line is
                already narrow enough that forcing a break leaves a short,
                ragged first line, so it is dropped and the text wraps
                naturally. A screen reader reads one sentence either way. */}
            <br className="hidden sm:inline" />
            has to prove itself.
          </h1>

          <p
            className="stagger mt-4 max-w-xl text-base leading-relaxed text-muted"
            style={{ '--stagger': 4 }}
          >
            CodeGuard AI reviews every pull request in your repositories and posts inline comments —
            but only for findings it can anchor to the real diff at the real commit. A model's claim
            is a claim until it is checked.
          </p>

          <div
            className="stagger mt-7 flex flex-wrap items-center gap-3"
            style={{ '--stagger': 5 }}
          >
            {mode === 'sign-in' ? (
              <>
                <Button variant="primary" onClick={onSignIn}>
                  Sign in with GitHub
                </Button>
                <p className="text-xs text-muted">
                  You will only see installations your account can already access.
                </p>
              </>
            ) : (
              <div className="panel w-full max-w-md p-4">
                <p className="flex items-center gap-2 text-sm font-medium text-content">
                  <IconAlert size={15} className="text-state-warn" />
                  Sign-in is not configured
                </p>
                <p className="mt-1.5 text-xs leading-relaxed text-muted">
                  This deployment has no GitHub OAuth credentials, so there is no way to sign in. An
                  operator needs to set{' '}
                  <code className="font-mono text-content">GITHUB_OAUTH_CLIENT_ID</code>,{' '}
                  <code className="font-mono text-content">GITHUB_OAUTH_CLIENT_SECRET</code> and{' '}
                  <code className="font-mono text-content">SESSION_SECRET</code>.
                </p>
              </div>
            )}
          </div>

          <ul className="mt-10 space-y-4">
            {GUARANTEES.map((item, index) => (
              <li
                key={item.title}
                className="stagger flex gap-3"
                style={{ '--stagger': 6 + index }}
              >
                <span className="mt-[2px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-state-ok/40 text-state-ok">
                  <IconCheck size={10} />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-content">{item.title}</p>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted">{item.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* -- the visualisation -------------------------------------------- */}
        {/* Presentation content only. See `PipelineDemo`'s header. */}
        <PipelineDemo className="stagger min-w-0" />
      </div>
    </Shell>
  )
}

/**
 * The API-unreachable screen, in the hero's shell.
 *
 * Shares the shell so that a developer running only the frontend gets a page
 * that looks like the product rather than a bare error box — and so the thing
 * they actually need, the command to start the backend, is the most prominent
 * element on it.
 */
export function ApiUnavailable({ detail, onRetry }: { detail: string; onRetry: () => void }) {
  return (
    <Shell>
      <div className="stagger mb-10 flex items-center justify-between">
        <Wordmark />
      </div>

      <div className="mx-auto max-w-2xl">
        {/* `role="alert"` moved from the message to this panel in v0.6.2, so the
            message itself can be the page's `<h1>`. A browser pass found this
            screen rendering with no heading at all: it is a whole page, reachable
            whenever the backend is down, and a screen-reader user navigating by
            heading had nothing to land on. An element cannot be both an alert and
            a heading, so the alert role belongs to the block and the heading to
            the line inside it. */}
        <div className="stagger panel p-6" role="alert" style={{ '--stagger': 2 }}>
          <h1 className="flex items-center gap-2 text-sm font-medium text-state-bad">
            <IconOffline size={16} />
            Cannot reach the API
          </h1>
          <p className="mt-1.5 text-sm text-muted">{detail}</p>

          <div className="rule-fade my-5" />

          {import.meta.env.DEV ? (
            <>
              <p className="text-xs text-muted">
                The frontend is running. It needs the backend on{' '}
                <code className="font-mono text-content">http://localhost:8000</code>, which the dev
                server proxies to. In another terminal:
              </p>
              <pre className="mt-2.5 overflow-x-auto rounded-md border border-border bg-surface p-3 font-mono text-xs leading-relaxed text-content">
                <code>
                  cd backend{'\n'}
                  uvicorn app.main:app --reload --port 8000
                </code>
              </pre>
              <p className="mt-2 text-xs text-muted">
                <code className="font-mono">docs/local-quickstart.md</code> has the database and
                environment steps that come first.
              </p>
            </>
          ) : (
            <p className="text-sm text-muted">
              The frontend is available, but its API connection is unavailable. Please try again
              later or contact the deployment administrator.
            </p>
          )}

          <div className="mt-5">
            <Button variant="primary" icon={<IconRefresh size={14} />} onClick={onRetry}>
              Try again
            </Button>
          </div>
        </div>
      </div>
    </Shell>
  )
}
