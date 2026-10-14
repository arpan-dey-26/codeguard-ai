/**
 * A tooltip that a keyboard and a screen reader can both reach.
 *
 * It replaces the native `title` attribute, which looks like the operating
 * system rather than the product, cannot be styled, appears after an
 * uncontrollable delay, and — the reason that actually matters — never appears
 * on focus, so a keyboard user cannot read it at all.
 *
 * **The rules it follows.**
 *
 * *Hover is delayed, focus is not.* A pointer crossing a row of six metric
 * tiles should not fire six tooltips; 380ms is long enough that passing over is
 * free and short enough that stopping feels answered. A keyboard user, by
 * contrast, has deliberately arrived — making them wait is pure latency.
 *
 * *It closes on Escape.* A tooltip that traps the reader is a modal nobody
 * asked for.
 *
 * *It is described, not labelled.* `aria-describedby` on the trigger, so a
 * screen reader announces the control's own name first and the explanation
 * after. `aria-labelledby` would replace the name with the description.
 *
 * *It does not open on touch.* There is no hover on a touch screen, and a
 * tooltip bound to tap steals the tap. On touch the trigger keeps its
 * `aria-describedby`, so the text is still available to a screen reader, and
 * sighted touch users get it on the focus that follows a tap.
 *
 * Positioning is CSS, not measurement: absolute against the trigger with a
 * `max-w` and a translate, so there is no layout read, no resize listener and
 * nothing to recompute on scroll. A tooltip near the viewport edge shifts with
 * `side`, which the caller sets — worth the one prop to avoid a measurement
 * loop for a 200-character hint.
 */

import { cloneElement, useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { cn } from '@/utils/cn'

const OPEN_DELAY_MS = 380

export function Tooltip({
  content,
  children,
  side = 'top',
  className,
}: {
  /** The explanation. Kept short — this is a hint, not documentation. */
  content: ReactNode
  /** The trigger. Must be focusable for the keyboard path to work. */
  children: ReactElement<{ 'aria-describedby'?: string }>
  side?: 'top' | 'bottom' | 'right'
  className?: string
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const tip = useRef<HTMLSpanElement>(null)
  const [offset, setOffset] = useState(0)

  useLayoutEffect(() => {
    if (!open || !tip.current) return
    const fit = () => {
      if (!tip.current) return
      const bounds = tip.current.getBoundingClientRect()
      const applied = Number.parseFloat(tip.current.style.marginLeft) || 0
      const left = bounds.left - applied
      const right = bounds.right - applied
      setOffset(Math.max(8 - left, Math.min(0, document.documentElement.clientWidth - 8 - right)))
    }
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [open, side])

  const cancel = useCallback((): void => {
    if (timer.current !== null) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const openAfterDelay = (): void => {
    cancel()
    timer.current = setTimeout(() => setOpen(true), OPEN_DELAY_MS)
  }

  const close = useCallback((): void => {
    cancel()
    setOpen(false)
  }, [cancel])

  useEffect(() => cancel, [cancel])

  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') close()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, close])

  const position = {
    top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
    bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
    right: 'left-full top-1/2 -translate-y-1/2 ml-2',
  }[side]

  return (
    <span
      className={cn('relative inline-flex', className)}
      onPointerEnter={(event) => {
        // Only a real pointer. A touch "enter" fires on tap, which is the
        // interaction this must not hijack.
        if (event.pointerType === 'mouse') openAfterDelay()
      }}
      onPointerLeave={close}
      onFocusCapture={() => setOpen(true)}
      onBlurCapture={close}
    >
      {cloneElement(children, {
        'aria-describedby': [children.props['aria-describedby'], id].filter(Boolean).join(' '),
      })}

      {/* Keep the description reference valid, but remove closed tooltips from
          layout so an invisible hint cannot widen the scrolling viewport. */}
      <span
        ref={tip}
        id={id}
        role="tooltip"
        hidden={!open}
        aria-hidden={!open}
        style={{ marginLeft: offset }}
        className={cn(
          'pointer-events-none absolute z-tooltip w-max max-w-[min(18rem,70vw)] rounded-md',
          'border border-border bg-surface px-2.5 py-1.5 text-xs leading-snug text-content',
          'shadow-overlay transition-[opacity,transform] duration-instant ease-out',
          position,
          open ? 'opacity-100' : 'opacity-0',
          // The lift direction matches where the tooltip sits, so it reads as
          // coming *from* the trigger.
          !open && side === 'top' && 'translate-y-[2px]',
          !open && side === 'bottom' && '-translate-y-[2px]',
          !open && side === 'right' && '-translate-x-[2px]',
        )}
      >
        {content}
      </span>
    </span>
  )
}
