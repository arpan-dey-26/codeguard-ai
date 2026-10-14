import { useState, type ReactNode } from 'react'
import {
  IconAlert,
  IconExternal,
  IconInbox,
  IconInfo,
  IconOffline,
  IconRefresh,
  IconSpinner,
} from '@/components/ui/icons'
import { Tooltip } from '@/components/ui/Tooltip'
import { useCountUp, useInViewOnce } from '@/hooks/useMotion'
import { cn } from '@/utils/cn'

/**
 * The small, repeated pieces: page headers, stat tiles, pills, definition
 * lists, buttons, skeletons.
 *
 * Kept in one file because each is a handful of lines and splitting them into
 * nine modules would cost more to navigate than it saves. They share one rule:
 * **no component here invents meaning**. A `StatTile` renders a number and the
 * definition it was given; it does not compute a rate, and it does not decide
 * that a number is good or bad.
 */

/* -- page scaffolding ----------------------------------------------------- */

export function PageHeader({
  title,
  subtitle,
  actions,
  eyebrow,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  /** A small label above the title — the section this page belongs to. */
  eyebrow?: string
}) {
  return (
    <header className="mb-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          {eyebrow ? (
            <p className="mb-1 text-micro font-medium uppercase tracking-wider text-muted">
              {eyebrow}
            </p>
          ) : null}
          {/* `truncate` only on the single-line case: a wrapped title that is
              also truncated loses its second line entirely. */}
          <h1 className="break-words text-xl font-semibold tracking-tight text-content sm:truncate">
            {title}
          </h1>
          {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
      </div>
      <div className="rule-fade mt-4" />
    </header>
  )
}

export function SectionHeader({
  title,
  hint,
  actions,
}: {
  title: string
  hint?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-content">{title}</h2>
        {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
      </div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  )
}

/* -- statistics ----------------------------------------------------------- */

/**
 * One number, its name, and what it means.
 *
 * `definition` is not optional decoration — it is the tile's contract. Every
 * metric in this dashboard is easy to misread ("findings" could mean claims,
 * validated findings or published comments), so the tile carries its own
 * definition and the page cannot ship a number nobody can interpret.
 */
export function StatTile({
  label,
  value,
  definition,
  tone = 'neutral',
  footnote,
  index,
  format,
}: {
  label: string
  /** A `number` animates towards its value; anything else renders as given. */
  value: ReactNode
  definition: string
  tone?: 'neutral' | 'ok' | 'warn' | 'bad'
  footnote?: ReactNode
  /** Position in a grid. Supplied only when the grid should stagger. */
  index?: number | undefined
  /**
   * How to render the number. Supply it with a numeric `value` so the tile can
   * animate the value and still display it formatted — passing a pre-formatted
   * string works but cannot animate, because the tile will not parse a string
   * back into a number to guess at its value.
   */
  format?: ((value: number) => string) | undefined
}) {
  const toneClass = {
    neutral: 'text-content',
    ok: 'text-state-ok',
    warn: 'text-state-warn',
    bad: 'text-state-bad',
  }[tone]

  const [ref, seen] = useInViewOnce<HTMLDivElement>()

  return (
    <div
      ref={ref}
      className={cn(
        'panel panel-interactive p-4',
        // The entrance is opt-in via `index`, so a grid can stagger and a
        // lone tile does not animate for no reason.
        index === undefined ? undefined : 'stagger',
      )}
      style={index === undefined ? undefined : ({ '--stagger': index } as CSSVars)}
    >
      <dt className="flex items-center gap-1.5 text-xs font-medium text-muted">
        <span className="min-w-0 truncate">{label}</span>
        <InfoDot text={definition} />
      </dt>
      <dd className={cn('mt-1.5 text-2xl font-semibold tabular-nums', toneClass)}>
        {/* A number is animated; anything else (a ratio string, a dash, a
            React node) is rendered as given. The tile never reformats what it
            was handed. */}
        {typeof value === 'number' ? (
          <AnimatedNumber value={value} active={seen} format={format} />
        ) : (
          value
        )}
      </dd>
      {footnote ? <p className="mt-1.5 text-xs leading-snug text-muted">{footnote}</p> : null}
    </div>
  )
}

/** The inline style bag for custom properties, which React types as strings. */
type CSSVars = Record<string, string | number>

/**
 * A number that counts to its value.
 *
 * Separate from `StatTile` so a page can animate a figure that is not in a
 * tile, and so the "only real data" rule lives in one place: this renders
 * `value`, and at rest it always displays exactly `value`. `aria-live` is
 * deliberately absent — announcing every intermediate frame would be a stream
 * of nonsense, and the final value is in the DOM for a reader that arrives
 * after.
 */
export function AnimatedNumber({
  value,
  active = true,
  format,
}: {
  value: number
  active?: boolean | undefined
  format?: ((value: number) => string) | undefined
}) {
  const display = useCountUp(value, active)
  const text = format ? format(display) : String(display)
  // The accessible value is the real one, never the interpolated frame.
  return <span aria-label={format ? format(value) : String(value)}>{text}</span>
}

/**
 * The definition marker beside a metric.
 *
 * A `<button>` with the text in `title` *and* in an `aria-label`: the title
 * covers mouse users, the label covers screen readers, and being focusable
 * means a keyboard user can reach the explanation at all. A bare `<span
 * title>` is invisible to both of the latter.
 */
export function InfoDot({ text }: { text: string }) {
  return (
    <Tooltip content={text} side="top">
      <button
        type="button"
        // The tooltip supplies `aria-describedby`; this supplies the control's
        // own name, which a describedby cannot. Without it the button is
        // announced as "button" and the tooltip text is orphaned.
        aria-label="What this measures"
        className={cn(
          'inline-flex h-4 w-4 items-center justify-center rounded-full text-muted',
          'transition-colors duration-instant ease-out hover:text-content',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
        )}
      >
        <IconInfo size={13} />
      </button>
    </Tooltip>
  )
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">{children}</dl>
}

/* -- status pills --------------------------------------------------------- */

/**
 * A status indicator with a glyph, a label and a colour — always all three.
 *
 * The glyph is `aria-hidden` because the label already carries the meaning;
 * announcing "filled circle Completed" is noise. The glyph exists for readers
 * who cannot distinguish the colour.
 */
export function StatusPill({
  label,
  glyph,
  className,
  title,
}: {
  label: string
  glyph: string
  className?: string
  title?: string
}) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-border px-2 py-[2px] text-[11px] font-medium',
        className,
      )}
    >
      <span aria-hidden="true">{glyph}</span>
      {label}
    </span>
  )
}

/* -- definition list ------------------------------------------------------ */

export function DefinitionList({ children }: { children: ReactNode }) {
  return <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">{children}</dl>
}

export function Definition({
  term,
  children,
  mono = false,
}: {
  term: string
  children: ReactNode
  mono?: boolean
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{term}</dt>
      <dd
        className={cn('mt-0.5 break-words text-sm text-content', mono && 'font-mono text-[13px]')}
      >
        {children}
      </dd>
    </div>
  )
}

/* -- controls ------------------------------------------------------------- */

/**
 * The application's button.
 *
 * Three states, each doing one job:
 *
 *   hover    border and background warm up — 120ms, because a control that
 *            takes longer than that to acknowledge a pointer feels broken
 *   active   a 1% scale down. Small enough to register as a press rather than
 *            as an animation; this is the single most effective
 *            micro-interaction in a UI and the easiest to overdo
 *   loading  a spinner replaces the icon slot, the label stays put, the width
 *            does not change, and `aria-busy` carries the state to a screen
 *            reader. A button that resizes when it starts working moves the
 *            thing the person just aimed at
 *
 * `loading` is *given* by the caller from real state; this component never
 * invents a pending state.
 *
 * **There is deliberately no `done` state (v0.6.2).** One was written — a check
 * that drew itself in — and then removed, because this frontend has exactly one
 * write in it (`POST /auth/logout`) and its success unmounts the tree that would
 * have shown the confirmation. A success affordance with nothing to confirm is
 * an invitation to attach it to something that has not actually succeeded. When
 * a real mutation lands, this is where the state goes.
 */
export function Button({
  children,
  onClick,
  variant = 'default',
  disabled = false,
  type = 'button',
  ariaLabel,
  icon,
  loading = false,
  size = 'md',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'subtle' | 'primary'
  disabled?: boolean
  type?: 'button' | 'submit'
  ariaLabel?: string
  /** Leading icon. Replaced by a spinner while `loading`. */
  icon?: ReactNode
  loading?: boolean
  size?: 'sm' | 'md'
}) {
  const variantClass = {
    primary: cn(
      'border-transparent bg-accent text-white shadow-flat',
      'hover:brightness-110 active:brightness-95',
    ),
    default: 'border-border bg-surface-raised text-content hover:border-accent hover:bg-surface',
    subtle: 'border-transparent text-muted hover:bg-surface-raised hover:text-content',
  }[variant]

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      aria-label={ariaLabel}
      aria-busy={loading || undefined}
      className={cn(
        'inline-flex select-none items-center justify-center gap-1.5 rounded-md border font-medium',
        size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-sm',
        // Only compositor properties and colour. No width, no padding change
        // between states, so a press cannot reflow the row the button sits in.
        'transition-[background-color,border-color,color,transform,box-shadow,filter]',
        'duration-instant ease-out active:scale-[0.99]',
        'disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100',
        variantClass,
      )}
    >
      {/* One icon slot, three occupants, so the label never shifts sideways
          when the state changes. */}
      {loading ? <IconSpinner size={size === 'sm' ? 13 : 14} /> : icon}
      {children}
    </button>
  )
}

export function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: ReadonlyArray<{ value: string; label: string }>
  onChange: (value: string) => void
}) {
  const id = `select-${label.replace(/\s+/g, '-').toLowerCase()}`
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="text-xs text-muted">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-md border border-border bg-surface-raised px-2 py-1 text-sm text-content"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  )
}

export function SearchInput({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string
  value: string
  placeholder?: string
  onChange: (value: string) => void
}) {
  const id = `search-${label.replace(/\s+/g, '-').toLowerCase()}`
  return (
    <div className="flex min-w-[min(100%,14rem)] flex-1 basis-56 items-center gap-2">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input
        id={id}
        type="search"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="w-full min-w-0 rounded-md border border-border bg-surface-raised px-3 py-1 text-sm text-content placeholder:text-muted"
      />
    </div>
  )
}

/**
 * A multi-select rendered as toggle chips.
 *
 * `aria-pressed` rather than a checkbox group: each chip is a toggle whose
 * state must be announced, and a chip that only changes colour when selected
 * is invisible to a screen reader and to anyone who cannot see the colour.
 */
export function ChipFilter({
  legend,
  options,
  selected,
  onToggle,
}: {
  legend: string
  options: ReadonlyArray<{ value: string; label: string }>
  selected: readonly string[]
  onToggle: (value: string) => void
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="mb-1 text-xs text-muted">{legend}</legend>
      <div className="flex flex-wrap gap-1">
        {options.map((option) => {
          const active = selected.includes(option.value)
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={active}
              onClick={() => onToggle(option.value)}
              className={cn(
                'rounded-md border px-2 py-[2px] text-[11px] transition-colors',
                active
                  ? 'border-accent text-accent'
                  : 'border-border text-muted hover:text-content',
              )}
            >
              {option.label}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

/* -- async states --------------------------------------------------------- */

/**
 * A skeleton with the final element's dimensions.
 *
 * Matching the real height is the entire point: a spinner that collapses to a
 * 20px line and then expands to a 400px chart shifts everything below it, and
 * the user loses their place at the exact moment the data arrives.
 */
export function Skeleton({
  className,
  style,
}: {
  className?: string | undefined
  /** For a stagger delay or an explicit height. */
  style?: Record<string, string | number> | undefined
}) {
  return (
    <div
      // No `role="status"` here. A grid of twelve skeleton bars announced
      // itself twelve times; the container that groups them carries the one
      // label, and these are decoration inside it.
      aria-hidden="true"
      className={cn('animate-pulse rounded-md bg-border/60', className)}
      style={style}
    />
  )
}

export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton
          key={index}
          className="h-9 w-full"
          // Each row fades slightly later, so the block reads as arriving
          // rather than as one flashing rectangle. Capped at six steps: past
          // that the last row waits long enough to look broken.
          style={{ animationDelay: `${Math.min(index, 5) * 70}ms` }}
        />
      ))}
    </div>
  )
}

/**
 * The loading shapes, one per real layout.
 *
 * **Why these are not one generic spinner.** A spinner says "something is
 * happening" and nothing else; a skeleton that matches the layout also says
 * *what* is coming and reserves its space, so the content does not shove the
 * page down when it lands. The measurements below are copied from the real
 * components — the tile skeleton is the height of a `StatTile`, the table
 * skeleton has the same header row — because a skeleton whose dimensions are a
 * guess produces exactly the layout shift it exists to prevent.
 */

/** Matches `StatGrid` + `StatTile`. */
export function SkeletonStatGrid({ tiles = 6 }: { tiles?: number }) {
  return (
    <div
      className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6"
      role="status"
      aria-label="Loading metrics"
    >
      {Array.from({ length: tiles }, (_, index) => (
        <div key={index} className="panel p-4" style={{ animationDelay: `${index * 60}ms` }}>
          <Skeleton className="h-3 w-2/3" />
          <Skeleton className="mt-2.5 h-7 w-1/2" />
        </div>
      ))}
    </div>
  )
}

/** Matches `ChartFrame` with a bar or line chart inside it. */
export function SkeletonChart({ className }: { className?: string | undefined }) {
  return (
    <div className={cn('space-y-2', className)} role="status" aria-label="Loading chart">
      {/* Bars of settled, varied heights rather than random ones: a skeleton
          that changes shape on every render is a distraction, and a re-render
          mid-load would reshuffle it. */}
      <div className="flex h-44 items-end gap-1.5">
        {[52, 74, 38, 88, 61, 46, 79, 57, 68, 43, 81, 59].map((height, index) => (
          <Skeleton
            key={index}
            className="flex-1 rounded-sm"
            style={{ height: `${height}%`, animationDelay: `${index * 45}ms` }}
          />
        ))}
      </div>
      <div className="rule-fade" />
      <Skeleton className="h-3 w-1/3" />
    </div>
  )
}

/** Matches `Table`: a header row, then body rows at the real row height. */
export function SkeletonTable({ rows = 6, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div role="status" aria-label="Loading table">
      <div className="flex gap-3 border-b border-border px-3 py-2">
        {Array.from({ length: columns }, (_, index) => (
          <Skeleton key={index} className={cn('h-2.5', index === 0 ? 'w-1/3' : 'flex-1')} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, row) => (
        <div
          key={row}
          className="flex gap-3 border-b border-border/60 px-3 py-2.5 last:border-0"
          style={{ animationDelay: `${Math.min(row, 5) * 70}ms` }}
        >
          {Array.from({ length: columns }, (_, column) => (
            <Skeleton key={column} className={cn('h-3', column === 0 ? 'w-1/3' : 'flex-1')} />
          ))}
        </div>
      ))}
    </div>
  )
}

/** Matches a stack of finding or review cards. */
export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3" role="status" aria-label="Loading">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="panel p-4" style={{ animationDelay: `${index * 80}ms` }}>
          <div className="flex items-start gap-3">
            <Skeleton className="h-5 w-5 rounded-full" />
            <div className="min-w-0 flex-1 space-y-2">
              <Skeleton className="h-3.5 w-2/5" />
              <Skeleton className="h-3 w-4/5" />
              <Skeleton className="h-3 w-1/4" />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * Nothing here — and an explanation of why nothing is here.
 *
 * **An empty state is a sentence, not a shrug (v0.6.2).** In this product empty
 * is usually *correct*: a signed-in user with no App installations genuinely has
 * no repositories, and a repository with no merged pull requests genuinely has
 * no reviews. So the title says what is absent and the hint says which
 * condition produced it, which is the difference between a user thinking the
 * page is broken and a user knowing what to do next.
 *
 * The glyph is a quiet outlined mark, not an illustration. A character or a
 * spot illustration in this slot would set a tone the rest of the interface does
 * not keep, and it would take vertical space away from the sentence that is
 * actually doing the work.
 */
export function EmptyState({
  title,
  hint,
  action,
  icon,
}: {
  title: string
  hint?: ReactNode
  action?: ReactNode
  /** Defaults to a neutral mark. Pass the domain icon where one reads better. */
  icon?: ReactNode
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center rounded-lg border border-dashed border-border px-4 py-10 text-center',
        'animate-fade-in',
      )}
    >
      <span
        aria-hidden="true"
        className="mb-3 flex h-9 w-9 items-center justify-center rounded-full border border-border text-muted"
      >
        {icon ?? <IconInbox size={17} />}
      </span>
      <p className="text-sm font-medium text-content">{title}</p>
      {hint ? <p className="mx-auto mt-1 max-w-md text-sm text-muted">{hint}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}

/**
 * An error state that a user can act on.
 *
 * It shows a readable message, a retry, and the correlation id when the API
 * supplied one — never a stack trace. The correlation id is the single piece
 * of technical detail worth surfacing, because it is how support finds the
 * server-side log for *this* request.
 */
export function ErrorState({
  message,
  correlationId,
  onRetry,
  kind = 'server',
}: {
  message: string
  correlationId?: string | null
  onRetry?: () => void
  /**
   * `network` — nothing answered the request. `server` — something answered and
   * said no (v0.6.2). Supplied by the caller from the error object; never
   * guessed from the message text.
   */
  kind?: 'network' | 'server' | undefined
}) {
  const [retrying, setRetrying] = useState(false)

  return (
    <div className="panel animate-fade-in border-state-bad/35 bg-state-bad/[0.04] p-4" role="alert">
      <div className="flex items-start gap-2.5">
        <span aria-hidden="true" className="mt-[1px] shrink-0 text-state-bad">
          {kind === 'network' ? <IconOffline size={16} /> : <IconAlert size={16} />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-state-bad">{message}</p>
          {/* The second line is the remedy, and it differs by kind because the
              two failures need different actions from the person reading it.
              Neither line asserts anything the error object did not carry. */}
          <p className="mt-1 text-xs leading-relaxed text-muted">
            {kind === 'network'
              ? 'The request did not reach a server. Confirm the backend is running and that VITE_API_BASE_URL points at it.'
              : 'The server answered and refused this request. Retrying is safe; if it keeps failing, the reference below identifies the server-side log.'}
          </p>
          {correlationId ? (
            <p className="mt-1.5 font-mono text-xs text-muted">Reference: {correlationId}</p>
          ) : null}
          {onRetry ? (
            <div className="mt-3">
              <Button
                icon={<IconRefresh size={13} />}
                loading={retrying}
                onClick={() => {
                  // The spinner is a 400ms acknowledgement of the click, not a
                  // measurement of the request: `onRetry` is fire-and-forget,
                  // so the component cannot know when the refetch settled and
                  // must not pretend to.
                  setRetrying(true)
                  window.setTimeout(() => setRetrying(false), 400)
                  onRetry()
                }}
              >
                Retry
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/**
 * A link that leaves the application (v0.6.2).
 *
 * Four pages had hand-rolled copies of this anchor, each ending in a literal
 * `↗` character. The arrow was standing in for an icon: it inherits the text's
 * font metrics, so it sat at a different height and weight in each place, and a
 * screen reader read it out as "north east arrow" in the middle of the label.
 * This renders the real `IconExternal` instead and puts the fact that the link
 * opens a new tab in text only a screen reader hears, which is the part the
 * arrow was trying and failing to convey.
 *
 * `rel="noreferrer noopener"` is not optional here: every one of these targets
 * is a GitHub URL built from API data.
 */
export function ExternalLink({
  href,
  children,
  className,
}: {
  href: string
  children: ReactNode
  className?: string | undefined
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1 text-sm text-muted',
        'transition-colors duration-instant ease-out hover:border-accent hover:text-content',
        className,
      )}
    >
      {children}
      <IconExternal size={12} />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  )
}

/** A short, monospaced inline code reference — a path, a SHA, a fingerprint. */
export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn('font-mono text-[13px]', className)}>{children}</span>
}

/**
 * A caveat attached to a number or a panel.
 *
 * Used wherever a metric could be over-read — the validation funnel, the
 * anchor buckets, the coverage figures. Prominent enough to be seen, quiet
 * enough not to shout.
 */
export function Caveat({ children }: { children: ReactNode }) {
  return (
    <p className="mt-2 border-l-2 border-border pl-3 text-xs leading-relaxed text-muted">
      {children}
    </p>
  )
}
