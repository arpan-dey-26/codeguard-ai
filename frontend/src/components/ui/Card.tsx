import type { ReactNode } from 'react'
import { cn } from '@/utils/cn'

/**
 * Surface primitive.
 *
 * **Revised in v0.6.2.** The original rule was "cards do not float — elevation
 * is reserved for overlays, so the eye is drawn to data rather than to chrome".
 * That instinct was right and is kept: a card still has a hairline border and a
 * shadow so slight it reads as a contact edge rather than a lift, and nothing on
 * a dense page moves on its own.
 *
 * What changed is *how* the flat surface is drawn. `.panel` adds a 1px top
 * highlight, which is the only depth cue that works in dark mode — a cast
 * shadow on a near-black page is invisible, so without it every card was a
 * rectangle outlined in one grey against another. The highlight costs nothing,
 * carries no colour, and is what separates "flat" from "unfinished".
 *
 * `interactive` opts into the hover lift, and is for a card that is *itself* a
 * link or a button. Do not set it on a card that merely contains links: a
 * surface that lifts under the pointer promises a click, and a promise the card
 * does not keep is worse than no feedback.
 *
 * `index` opts into the entrance stagger, for a grid or list rendered together.
 * Omit it for a lone card — a single element animating in has nothing to be
 * sequenced against and just looks late.
 */
export function Card({
  children,
  className,
  interactive = false,
  index,
}: {
  children: ReactNode
  // `| undefined` throughout: the project runs `exactOptionalPropertyTypes`,
  // under which `className?: string` rejects a forwarded `string | undefined`.
  className?: string | undefined
  interactive?: boolean | undefined
  index?: number | undefined
}) {
  return (
    <section
      className={cn(
        'panel p-4',
        interactive && 'panel-interactive',
        index !== undefined && 'stagger',
        className,
      )}
      style={index === undefined ? undefined : { '--stagger': index }}
    >
      {children}
    </section>
  )
}
