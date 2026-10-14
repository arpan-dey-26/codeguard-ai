import type { Config } from 'tailwindcss'

/**
 * Tailwind consumes CSS custom properties rather than literal colours.
 *
 * Why: the same utility class (`bg-surface`) must resolve to a different value
 * in light and dark mode. Binding the token to a variable means theming is a
 * single class swap on <html> with no duplicated class lists and no runtime
 * cost. Literal hex values in this file would make dark mode a second set of
 * `dark:` classes on every element.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: 'rgb(var(--surface) / <alpha-value>)',
        'surface-raised': 'rgb(var(--surface-raised) / <alpha-value>)',
        border: 'rgb(var(--border) / <alpha-value>)',
        content: 'rgb(var(--text) / <alpha-value>)',
        muted: 'rgb(var(--text-muted) / <alpha-value>)',
        accent: 'rgb(var(--accent) / <alpha-value>)',
        'severity-critical': 'rgb(var(--severity-critical) / <alpha-value>)',
        'severity-high': 'rgb(var(--severity-high) / <alpha-value>)',
        'severity-medium': 'rgb(var(--severity-medium) / <alpha-value>)',
        'severity-low': 'rgb(var(--severity-low) / <alpha-value>)',
        'severity-info': 'rgb(var(--severity-info) / <alpha-value>)',
        'state-ok': 'rgb(var(--state-ok) / <alpha-value>)',
        'state-warn': 'rgb(var(--state-warn) / <alpha-value>)',
        'state-bad': 'rgb(var(--state-bad) / <alpha-value>)',
        'state-idle': 'rgb(var(--state-idle) / <alpha-value>)',
        'series-1': 'rgb(var(--series-1) / <alpha-value>)',
        'series-2': 'rgb(var(--series-2) / <alpha-value>)',
        'series-3': 'rgb(var(--series-3) / <alpha-value>)',
        'series-4': 'rgb(var(--series-4) / <alpha-value>)',
        grid: 'rgb(var(--grid) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      // A 4px base scale. Arbitrary spacing values are how a layout stops
      // looking designed; restricting the vocabulary is the cheapest way to
      // keep rhythm consistent across contributors.
      spacing: {
        1: '4px',
        2: '8px',
        3: '12px',
        4: '16px',
        5: '20px',
        6: '24px',
        8: '32px',
        10: '40px',
        12: '48px',
        16: '64px',
      },
      borderRadius: { md: '6px', lg: '10px', xl: '14px' },
      boxShadow: {
        // Bound to the variables in tokens.css so dark mode gets deeper
        // shadows without a second set of `dark:` classes. `raise` is the
        // hover state of `flat`; the readable difference between them is the
        // entire effect.
        flat: 'var(--shadow-flat)',
        raise: 'var(--shadow-raise)',
        overlay: 'var(--shadow-overlay)',
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
        soft: 'var(--ease-soft)',
        in: 'var(--ease-in)',
      },
      transitionDuration: {
        instant: 'var(--dur-instant)',
        quick: 'var(--dur-quick)',
        panel: 'var(--dur-panel)',
        entrance: 'var(--dur-entrance)',
      },
      zIndex: {
        sticky: 'var(--z-sticky)',
        nav: 'var(--z-nav)',
        tooltip: 'var(--z-tooltip)',
        overlay: 'var(--z-overlay)',
        'skip-link': 'var(--z-skip-link)',
      },
      // Typographic scale. Tailwind's defaults jump 14 → 16 → 18, which is too
      // coarse for a dense product surface; these are the six sizes the UI
      // actually uses, with line heights and tracking set per size rather than
      // left to a global default.
      fontSize: {
        micro: ['11px', { lineHeight: '15px', letterSpacing: '0.02em' }],
        xs: ['12px', { lineHeight: '17px' }],
        sm: ['13px', { lineHeight: '19px' }],
        base: ['14px', { lineHeight: '21px' }],
        lg: ['16px', { lineHeight: '23px', letterSpacing: '-0.005em' }],
        xl: ['19px', { lineHeight: '26px', letterSpacing: '-0.014em' }],
        '2xl': ['24px', { lineHeight: '30px', letterSpacing: '-0.02em' }],
        '3xl': ['30px', { lineHeight: '36px', letterSpacing: '-0.024em' }],
        // Hero only. Tighter tracking the larger the type gets, which is what
        // keeps a big headline from looking like stretched body copy.
        display: ['clamp(30px, 4.2vw, 46px)', { lineHeight: '1.08', letterSpacing: '-0.03em' }],
      },
      keyframes: {
        // One entrance, used by the stagger helper. Transform + opacity only:
        // both are compositor properties, so a dozen of these run on the GPU
        // and never touch layout.
        'rise-in': {
          from: { opacity: '0', transform: 'translate3d(0, 10px, 0)' },
          to: { opacity: '1', transform: 'translate3d(0, 0, 0)' },
        },
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        // The diff/analysis sweep in the hero. Moves a masked highlight, not
        // the content.
        sweep: {
          from: { transform: 'translate3d(-100%, 0, 0)' },
          to: { transform: 'translate3d(220%, 0, 0)' },
        },
        'pulse-ring': {
          '0%': { opacity: '0.5', transform: 'scale(0.82)' },
          '70%, 100%': { opacity: '0', transform: 'scale(1.5)' },
        },
      },
      animation: {
        'rise-in': 'rise-in var(--dur-panel) var(--ease-out) both',
        'fade-in': 'fade-in var(--dur-quick) var(--ease-out) both',
        sweep: 'sweep 2.4s var(--ease-soft) infinite',
        // Three iterations, not `infinite`: it marks the moment the finding
        // anchors and then stops. A ring pulsing forever on a landing page is
        // a distraction with nothing left to announce.
        'pulse-ring': 'pulse-ring 1.6s var(--ease-out) 3',
      },
    },
  },
  plugins: [],
} satisfies Config
