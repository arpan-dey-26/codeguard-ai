/**
 * The motion primitives: does this person want motion, is this on screen yet,
 * and how do you animate a number without lying about it.
 *
 * All three are here rather than in three files because they are always used
 * together — a count-up that does not check reduced motion, or that runs while
 * off screen, is worse than no count-up.
 *
 * **The CSS is the primary motion layer, not this file.** Entrances, hovers,
 * the hero sweep and the stagger are all CSS in `globals.css` and
 * `tailwind.config.ts`, because the compositor runs them without JavaScript and
 * they keep working if a bundle is slow. JavaScript is used only where CSS
 * genuinely cannot reach: reading a media query, observing intersection, and
 * interpolating a numeric value.
 */

import { useEffect, useRef, useState } from 'react'

/**
 * Whether the person has asked for reduced motion.
 *
 * Read live rather than once: the setting can change while the page is open —
 * on macOS it is a checkbox, not a boot flag — and a component that sampled it
 * at mount keeps animating for the rest of the session.
 *
 * Defaults to `true` when `matchMedia` is unavailable. That direction is
 * deliberate: the safe failure is *less* motion, and the only environments
 * without `matchMedia` are non-browser ones where animating is meaningless.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  return reduced
}

/**
 * True once the element has been on screen, and true from then on.
 *
 * "Once" is the important half. A value that re-animates every time it scrolls
 * back into view is a distraction, and on a dashboard where the same tile is
 * passed repeatedly it is actively annoying. The observer disconnects after the
 * first hit, so nothing keeps watching.
 *
 * Returns `true` immediately when `IntersectionObserver` is unavailable — the
 * content must never be stuck in its pre-animation state because an API is
 * missing.
 */
export function useInViewOnce<T extends Element>(): [(node: T | null) => void, boolean] {
  const [seen, setSeen] = useState(false)
  const observed = useRef<T | null>(null)
  const observer = useRef<IntersectionObserver | null>(null)

  const ref = (node: T | null): void => {
    if (observed.current === node) return
    observer.current?.disconnect()
    observer.current = null
    observed.current = node
    if (node === null || seen) return

    if (typeof IntersectionObserver === 'undefined') {
      setSeen(true)
      return
    }

    observer.current = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true)
          observer.current?.disconnect()
          observer.current = null
        }
      },
      // A small positive margin so a tile just below the fold starts its
      // entrance before it is fully visible, which is what makes the scroll
      // feel like the page was already ready.
      { rootMargin: '0px 0px -8% 0px', threshold: 0.01 },
    )
    observer.current.observe(node)
  }

  useEffect(() => () => observer.current?.disconnect(), [])

  return [ref, seen]
}

/**
 * Interpolate towards a number, for display only.
 *
 * **This animates the presentation of a value the API returned. It never
 * invents one.** The returned number is always the real target at rest, the
 * first render of a value is not animated (a dashboard whose figures all climb
 * from zero on load is theatre, and it delays the moment the numbers are
 * readable), and a change is animated only when there is a previous value to
 * change *from*.
 *
 * `active` is the caller's gate — usually "this tile has been seen". When it is
 * false the hook returns the target unchanged.
 */
export function useCountUp(target: number, active = true): number {
  const reduced = useReducedMotion()
  const [display, setDisplay] = useState(target)
  const previous = useRef(target)
  const frame = useRef(0)

  useEffect(() => {
    const from = previous.current
    previous.current = target

    // No animation for: reduced motion, not yet active, an unchanged value, a
    // non-finite value, or a first paint (from === target because state was
    // seeded with it).
    if (reduced || !active || from === target || !Number.isFinite(target)) {
      setDisplay(target)
      return undefined
    }

    // Duration scales a little with distance so 4 → 5 is not given the same
    // 400ms as 0 → 1,200, but stays inside the "quick" budget either way.
    const distance = Math.abs(target - from)
    const duration = Math.min(520, 180 + distance * 6)
    const start = performance.now()

    const step = (now: number): void => {
      const t = Math.min(1, (now - start) / duration)
      // Matches --ease-out: fast departure, soft settle.
      const eased = 1 - Math.pow(1 - t, 3)
      const value = from + (target - from) * eased
      // Integers stay integers on the way. A counter flickering through
      // 4.7381 is noise, not motion.
      setDisplay(Number.isInteger(from) && Number.isInteger(target) ? Math.round(value) : value)
      if (t < 1) frame.current = requestAnimationFrame(step)
      else setDisplay(target)
    }

    frame.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame.current)
  }, [target, active, reduced])

  return display
}
