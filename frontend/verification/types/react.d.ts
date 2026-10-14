/**
 * Minimal ambient React declarations.
 *
 * The real `@types/react` cannot be installed here (the npm registry returns
 * 403), so this stub stands in for it. It is deliberately *strict about the
 * parts this codebase uses* — hook signatures, JSX intrinsic element props,
 * ReactNode — so `tsc` still catches wrong props, wrong hook arguments,
 * missing fields and bad unions in application code. It cannot catch an error
 * in React's own typing, and the verification report says so.
 */
declare namespace React {
  export type Key = string | number
  export type ReactNode =
    | string
    | number
    | boolean
    | null
    | undefined
    | ReactElement
    | JSX.Element
    | Iterable<ReactNode>
  export interface ReactElement {
    readonly __brand?: unknown
  }
  export type FC<P = Record<string, unknown>> = (props: P) => ReactElement | null

  export function useState<S>(initial: S | (() => S)): [S, (value: S | ((prev: S) => S)) => void]
  export function useEffect(effect: () => void | (() => void), deps?: readonly unknown[]): void
  export function useCallback<T extends (...args: never[]) => unknown>(fn: T, deps: readonly unknown[]): T
  export function useMemo<T>(factory: () => T, deps: readonly unknown[]): T
  export function useRef<T>(initial: T): { current: T }
  export function useId(): string
  export interface PointerEvent {
    pointerType: string
    clientX: number
    clientY: number
    currentTarget: { getBoundingClientRect(): { left: number; top: number; width: number; height: number } }
    preventDefault(): void
    stopPropagation(): void
  }
  export interface FocusEvent {
    currentTarget: unknown
    relatedTarget: unknown
  }
  export interface KeyboardEvent {
    key: string
    preventDefault(): void
    stopPropagation(): void
  }

  // Added for the Phase 5 auth provider. Hand-written, like the rest of this
  // stub: the real @types/react cannot be installed here (npm returns 403), so
  // this file approximates the API surface the application actually uses. It
  // catches a wrong argument count or a wrong type at a call site; it is NOT a
  // substitute for typechecking against real React, which CI does.
  export interface Provider<T> {
    (props: { value: T; children?: ReactNode }): ReactElement | null
  }
  export interface Context<T> {
    Provider: Provider<T>
    displayName?: string
  }
  export function createContext<T>(defaultValue: T): Context<T>

  /**
   * Class-component surface, added in v0.6.1 for the error boundaries.
   *
   * Signatures mirror `@types/react` for the members actually used:
   * `props`, `state`, `setState`, `render`, `componentDidCatch` and the static
   * `getDerivedStateFromError`. `state` is `Readonly<S>` exactly as React
   * declares it, so an accidental `this.state.x = …` is still an error here.
   */
  export interface ErrorInfo {
    componentStack?: string | null | undefined
    digest?: string | null | undefined
  }
  export class Component<P = Record<string, unknown>, S = Record<string, unknown>> {
    constructor(props: P)
    readonly props: Readonly<P>
    state: Readonly<S>
    setState<K extends keyof S>(
      state: Pick<S, K> | S | null,
      callback?: () => void,
    ): void
    render(): ReactNode
    componentDidCatch?(error: Error, errorInfo: ErrorInfo): void
    componentDidMount?(): void
    componentWillUnmount?(): void
  }

  export function useContext<T>(context: Context<T>): T

  export interface ChangeEvent<T> { target: T & { value: string } }
  export interface MouseEvent<T = Element> { preventDefault(): void; currentTarget: T }

  export const StrictMode: FC<{ children?: ReactNode }>
}

declare module 'react' {
  export = React
}

declare namespace JSX {
  type Element = React.ReactElement
  interface ElementChildrenAttribute { children: Record<string, never> }
  /** React accepts `key` on any element, including custom components. */
  interface IntrinsicAttributes { key?: React.Key | null | undefined }

  /**
   * SVG presentation attributes shared by every shape element.
   *
   * Widened to `string | number` for the numeric ones because that is what
   * `@types/react` does — SVG accepts `x="50%"` as readily as `x={12}`, and
   * narrowing them to `number` here rejected valid markup that the real types
   * allow. Added in the v0.6.2 frontend pass, when the icon set needed them.
   */
  interface SvgProps {
    fill?: string | undefined
    fillOpacity?: string | number | undefined
    fillRule?: 'nonzero' | 'evenodd' | undefined
    stroke?: string | undefined
    strokeWidth?: string | number | undefined
    strokeOpacity?: string | number | undefined
    strokeLinecap?: 'butt' | 'round' | 'square' | undefined
    strokeLinejoin?: 'miter' | 'round' | 'bevel' | undefined
    strokeDasharray?: string | number | undefined
    strokeDashoffset?: string | number | undefined
    transform?: string | undefined
    opacity?: string | number | undefined
    clipPath?: string | undefined
    mask?: string | undefined
    focusable?: boolean | 'true' | 'false' | undefined
    vectorEffect?: string | undefined
  }

  interface CommonProps {
    key?: React.Key | null | undefined
    className?: string | undefined
    id?: string | undefined
    title?: string | undefined
    style?: Record<string, string | number | undefined> | undefined
    children?: React.ReactNode
    role?: string | undefined
    hidden?: boolean | undefined
    tabIndex?: number | undefined
    onClick?: ((event: React.MouseEvent) => void) | undefined
    'aria-label'?: string | undefined
    'aria-hidden'?: boolean | 'true' | 'false' | undefined
    'aria-live'?: string | undefined
    'aria-pressed'?: boolean | undefined
    'aria-expanded'?: boolean | undefined
    'aria-controls'?: string | undefined
    /* Added in the v0.6.2 frontend pass, for the tooltip, the tilt cards and
       the in-view observer. Matching `@types/react`'s shapes for the members
       the application uses; `ref` is the callback form only, which is all this
       codebase needs and is the form React 19 accepts on every element. */
    'aria-describedby'?: string | undefined
    'aria-busy'?: boolean | undefined
    'aria-current'?: boolean | 'page' | 'step' | 'location' | 'date' | 'time' | undefined
    'aria-atomic'?: boolean | undefined
    'aria-modal'?: boolean | undefined
    'aria-haspopup'?: boolean | 'menu' | 'dialog' | 'listbox' | undefined
    'data-theme'?: string | undefined
    'data-state'?: string | undefined
    /* The callback form takes `never` so any `(node: T | null) => void` is
       assignable by contravariance; the object form takes `unknown` because
       `{ current: never }` rejects every real `useRef`. Both are looser than
       `@types/react`, which parameterises the element type per tag — a real
       `npm run typecheck` is stricter here, not weaker. */
    ref?: ((node: never) => void) | { current: unknown } | null | undefined
    onPointerEnter?: ((event: React.PointerEvent) => void) | undefined
    onPointerLeave?: ((event: React.PointerEvent) => void) | undefined
    onPointerMove?: ((event: React.PointerEvent) => void) | undefined
    onPointerDown?: ((event: React.PointerEvent) => void) | undefined
    onMouseEnter?: ((event: React.MouseEvent) => void) | undefined
    onMouseLeave?: ((event: React.MouseEvent) => void) | undefined
    onFocus?: ((event: React.FocusEvent) => void) | undefined
    onBlur?: ((event: React.FocusEvent) => void) | undefined
    onFocusCapture?: ((event: React.FocusEvent) => void) | undefined
    onBlurCapture?: ((event: React.FocusEvent) => void) | undefined
    onKeyDown?: ((event: React.KeyboardEvent) => void) | undefined
  }

  interface IntrinsicElements {
    a: CommonProps & { href?: string | undefined; target?: string | undefined; rel?: string | undefined }
    button: CommonProps & { type?: 'button' | 'submit' | 'reset' | undefined; disabled?: boolean | undefined }
    input: CommonProps & {
      type?: string | undefined
      value?: string | number | undefined
      placeholder?: string | undefined
      onChange?: ((event: React.ChangeEvent<HTMLInputElement>) => void) | undefined
    }
    select: CommonProps & {
      value?: string | undefined
      onChange?: ((event: React.ChangeEvent<HTMLSelectElement>) => void) | undefined
    }
    option: CommonProps & { value?: string | undefined }
    label: CommonProps & { htmlFor?: string | undefined }
    time: CommonProps & { dateTime?: string | undefined }
    th: CommonProps & { scope?: string | undefined; colSpan?: number | undefined }
    td: CommonProps & { colSpan?: number | undefined }
    svg: CommonProps & SvgProps & { viewBox?: string | undefined; preserveAspectRatio?: string | undefined; width?: number | string | undefined; height?: number | string | undefined; xmlns?: string | undefined }
    rect: CommonProps & SvgProps & { x?: string | number | undefined; y?: string | number | undefined; width?: string | number | undefined; height?: string | number | undefined; rx?: string | number | undefined; ry?: string | number | undefined }
    line: CommonProps & SvgProps & { x1?: string | number | undefined; x2?: string | number | undefined; y1?: string | number | undefined; y2?: string | number | undefined }
    text: CommonProps & SvgProps & { x?: string | number | undefined; y?: string | number | undefined; dx?: string | number | undefined; dy?: string | number | undefined; textAnchor?: string | undefined; dominantBaseline?: string | undefined }
    g: CommonProps & SvgProps
    path: CommonProps & SvgProps & { d?: string | undefined }
    circle: CommonProps & SvgProps & { cx?: string | number | undefined; cy?: string | number | undefined; r?: string | number | undefined }
    ellipse: CommonProps & SvgProps & { cx?: string | number | undefined; cy?: string | number | undefined; rx?: string | number | undefined; ry?: string | number | undefined }
    polyline: CommonProps & SvgProps & { points?: string | undefined }
    polygon: CommonProps & SvgProps & { points?: string | undefined }
    defs: CommonProps
    title: CommonProps
    desc: CommonProps
    linearGradient: CommonProps & { x1?: string | number | undefined; x2?: string | number | undefined; y1?: string | number | undefined; y2?: string | number | undefined; gradientUnits?: string | undefined }
    radialGradient: CommonProps & { cx?: string | number | undefined; cy?: string | number | undefined; r?: string | number | undefined; gradientUnits?: string | undefined }
    stop: CommonProps & { offset?: string | number | undefined; stopColor?: string | undefined; stopOpacity?: string | number | undefined }
    clipPath: CommonProps
    mask: CommonProps & { maskUnits?: string | undefined }
    div: CommonProps
    br: CommonProps
    hr: CommonProps
    article: CommonProps
    section: CommonProps
    figure: CommonProps
    figcaption: CommonProps
    details: CommonProps & { open?: boolean | undefined }
    summary: CommonProps
    span: CommonProps
    p: CommonProps
    h1: CommonProps
    h2: CommonProps
    h3: CommonProps
    ul: CommonProps
    ol: CommonProps
    li: CommonProps
    nav: CommonProps
    main: CommonProps
    header: CommonProps
    section: CommonProps
    figure: CommonProps
    figcaption: CommonProps
    details: CommonProps
    summary: CommonProps
    table: CommonProps
    thead: CommonProps
    tbody: CommonProps
    tr: CommonProps
    caption: CommonProps
    dl: CommonProps
    dt: CommonProps
    dd: CommonProps
    fieldset: CommonProps
    legend: CommonProps
    pre: CommonProps
    code: CommonProps
    em: CommonProps
    strong: CommonProps
  }
}
