declare module 'react/jsx-runtime' {
  export const jsx: unknown
  export const jsxs: unknown
  export const Fragment: unknown
}
declare module 'react-dom/client' {
  export function createRoot(container: Element): { render(node: unknown): void }
}
declare module 'react-router-dom' {
  import type { ReactNode, ReactElement } from 'react'
  export interface RouteObject {
    path?: string
    index?: boolean
    element?: ReactNode
    /** v0.6.1: the router's own error screen for this subtree. */
    errorElement?: ReactNode
    children?: RouteObject[]
  }
  /** Mirrors react-router's `ErrorResponse` for the fields this app reads. */
  export interface ErrorResponse {
    status: number
    statusText: string
    data: unknown
  }
  export function isRouteErrorResponse(error: unknown): error is ErrorResponse
  export function useRouteError(): unknown
  export function createBrowserRouter(routes: RouteObject[]): unknown
  export const RouterProvider: (props: { router: unknown }) => ReactElement
  export const Outlet: () => ReactElement
  export function Link(props: {
    to: string
    className?: string | undefined
    children?: ReactNode
    title?: string | undefined
  }): ReactElement
  export function Navigate(props: { to: string; replace?: boolean }): ReactElement
  export function NavLink(props: {
    to: string
    end?: boolean
    onClick?: (() => void) | undefined
    className?: string | ((state: { isActive: boolean }) => string)
    /* v0.6.2: the render-prop form, which react-router supports alongside a
       plain node. The shell uses it so the active rail and the icon colour can
       both read `isActive` without a second `useMatch`. */
    children?: ReactNode | ((state: { isActive: boolean; isPending: boolean }) => ReactNode)
  }): ReactElement
  export interface Location {
    pathname: string
    search: string
    hash: string
    key: string
    state: unknown
  }
  export function useLocation(): Location
  export function useNavigate(): (to: string, options?: { replace?: boolean }) => void
  export function useParams<T extends Record<string, string | undefined>>(): T
  export function useSearchParams(): [
    URLSearchParams,
    (next: URLSearchParams, options?: { replace?: boolean }) => void,
  ]
}
declare module 'tailwindcss' {
  export type Config = Record<string, unknown>
}

declare module '*.css' {
  const content: string
  export default content
}
interface ImportMetaEnv { readonly VITE_API_BASE_URL?: string }
interface ImportMeta { readonly env: ImportMetaEnv }

/**
 * Node's `process`, used only by the test harness's exit code. In a normal
 * checkout this comes from `@types/node`, which package.json now lists as a
 * dev dependency; it cannot be installed here, so it is stubbed.
 */
declare const process: { exit(code: number): never }
