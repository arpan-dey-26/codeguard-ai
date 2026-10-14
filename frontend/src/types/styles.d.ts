import 'react'

declare module 'react' {
  interface CSSProperties {
    /** Index used by the staggered entrance animation. */
    '--stagger'?: number
  }
}
