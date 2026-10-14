/**
 * The surfaces: `Panel`, and `TiltCard` for the few places depth earns itself.
 *
 * **Why depth is rationed.** A dashboard where every card tilts is a toy.
 * `Panel` is the default and it does not move; `TiltCard` is for a surface that
 * is a single large object a person is looking *at* rather than reading through.
 *
 * **Where TiltCard is actually used, and where it is not (v0.6.2).** One place:
 * the hero's pipeline illustration. It was written expecting to go on the
 * repository, review and finding cards, and then it went on none of them,
 * because those turned out to be the wrong surfaces for it — the list pages are
 * dense `Table`s rather than card grids, and `FindingCard` carries twelve fields
 * and an expander, where a rotating substrate makes the text harder to read for
 * no gain. Rather than leave the effect unused or force it onto data, it is
 * applied to the one surface in the product that is genuinely a picture: a
 * single `aria-hidden` illustration with six short lines on it. Dense data
 * tables, charts and metric rows get `Panel` and stay still, because there the
 * content is the point and movement behind a column of numbers is interference.
 *
 * **Why it is cheap.** The rotation is CSS, driven by two custom properties.
 * Pointer movement writes them directly to the node's `style` — no React state,
 * so a pointer crossing a card causes zero re-renders, and the animation runs
 * entirely on the compositor. The listener is attached only when the device has
 * a real pointer and the person has not asked for reduced motion; on a phone
 * nothing is attached at all.
 */

import { useCallback, useRef, type PointerEvent, type ReactNode } from 'react'
import { useReducedMotion } from '@/hooks/useMotion'
import { cn } from '@/utils/cn'

/**
 * The standard card.
 *
 * `interactive` adds the hover lift — set it when the whole card is a link or a
 * button, and leave it off when the card merely contains things, because a lift
 * on something unclickable promises an interaction that is not there.
 */
export function Panel({
  children,
  className,
  interactive = false,
  index,
}: {
  children: ReactNode
  // `| undefined` on every optional prop, because the project runs
  // `exactOptionalPropertyTypes`: without it a caller cannot forward its own
  // `string | undefined` into `className`, which is the common case.
  className?: string | undefined
  interactive?: boolean | undefined
  /** Position in a list, for the entrance stagger. Omit to not animate. */
  index?: number | undefined
}) {
  return (
    <div
      className={cn(
        'panel',
        interactive && 'panel-interactive',
        index !== undefined && 'stagger',
        className,
      )}
      style={index === undefined ? undefined : { '--stagger': index }}
    >
      {children}
    </div>
  )
}

/** Maximum pointer offset fed to CSS, in normalised units. Keeps the tilt small. */
const TILT_CLAMP = 1

export function TiltCard({
  children,
  className,
  index,
  interactive = true,
}: {
  children: ReactNode
  className?: string | undefined
  index?: number | undefined
  /**
   * Adds the hover lift on top of the tilt. Leave it off for a surface that is
   * not clickable: the tilt reads as "a solid object under your pointer", which
   * is true of any object, while the lift reads as "click me", which would be a
   * promise the surface does not keep.
   */
  interactive?: boolean | undefined
}) {
  const plane = useRef<HTMLDivElement | null>(null)
  const reduced = useReducedMotion()

  const onPointerMove = useCallback((event: PointerEvent) => {
    // Mouse only. A touch drag across a card should scroll the page, not
    // rotate it, and a stylus hover is not worth the special case.
    if (event.pointerType !== 'mouse') return
    const node = plane.current
    if (node === null) return

    const box = event.currentTarget.getBoundingClientRect()
    // -1 … 1 from the centre. Writing normalised values rather than degrees
    // keeps the angle a design decision in CSS, where the rest of the
    // motion vocabulary lives.
    const x = Math.max(
      -TILT_CLAMP,
      Math.min(TILT_CLAMP, ((event.clientX - box.left) / box.width - 0.5) * 2),
    )
    const y = Math.max(
      -TILT_CLAMP,
      Math.min(TILT_CLAMP, ((event.clientY - box.top) / box.height - 0.5) * 2),
    )

    // Direct style writes, deliberately outside React. Routing 60 pointer
    // events a second through `setState` would re-render the card's whole
    // subtree for a visual effect that never changes its content.
    node.style.setProperty('--tilt-x', String(x))
    node.style.setProperty('--tilt-y', String(y))
    node.style.setProperty('--pointer-x', `${((event.clientX - box.left) / box.width) * 100}%`)
    node.style.setProperty('--pointer-y', `${((event.clientY - box.top) / box.height) * 100}%`)
  }, [])

  const onPointerLeave = useCallback(() => {
    const node = plane.current
    if (node === null) return
    // Back to flat. The CSS transition on `.tilt-plane` does the easing, so
    // there is no JavaScript animation to cancel on unmount.
    node.style.setProperty('--tilt-x', '0')
    node.style.setProperty('--tilt-y', '0')
  }, [])

  // With reduced motion this is a plain Panel — not a Panel with a disabled
  // handler, an actually simpler tree.
  if (reduced) {
    return (
      <Panel interactive={interactive} className={className} index={index}>
        {children}
      </Panel>
    )
  }

  return (
    <div
      className={cn('tilt-root', index !== undefined && 'stagger')}
      style={index === undefined ? undefined : { '--stagger': index }}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
    >
      <div
        ref={plane}
        className={cn('tilt-plane panel', interactive && 'panel-interactive', className)}
      >
        <span className="tilt-sheen" aria-hidden="true" />
        {/* Content sits above the sheen and is not itself transformed, so text
            stays on the pixel grid and crisp. */}
        <div className="relative">{children}</div>
      </div>
    </div>
  )
}
