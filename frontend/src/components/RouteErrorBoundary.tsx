/**
 * What renders when a route throws.
 *
 * Wired as the router's `errorElement` (v0.6.1). React Router catches errors
 * raised while rendering a route and, with no `errorElement`, shows its own
 * unstyled page — recognisably not this application, with no navigation and no
 * way back. This keeps the failure inside the product.
 *
 * Distinct from `RootErrorBoundary`, which sits above the router and catches
 * what the router cannot: a throw in the auth provider, the gate, or the router
 * itself. Two boundaries because they cover two different regions, not because
 * one was insufficient.
 */

import { Link, isRouteErrorResponse, useRouteError } from 'react-router-dom'

export function RouteErrorBoundary() {
  const error = useRouteError()

  const { heading, detail } = describe(error)

  return (
    <div className="mx-auto max-w-lg py-12" role="alert">
      <h1 className="text-lg font-semibold tracking-tight">{heading}</h1>
      <p className="mt-2 text-sm text-muted">{detail}</p>
      <div className="mt-6">
        <Link
          to="/"
          className="inline-block rounded-md border border-border bg-surface px-3 py-2 text-sm font-medium hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          Back to the dashboard
        </Link>
      </div>
    </div>
  )
}

function describe(error: unknown): { heading: string; detail: string } {
  if (isRouteErrorResponse(error)) {
    if (error.status === 404) {
      return {
        heading: 'Page not found',
        detail: 'That address does not match any page in this dashboard.',
      }
    }
    return {
      heading: `${error.status} ${error.statusText}`,
      detail: 'The page could not be loaded.',
    }
  }
  if (error instanceof Error) {
    // The message, not the stack — the console has the stack, and a stack on
    // screen is noise to almost everyone who will read this.
    return { heading: 'This page could not be displayed', detail: error.message }
  }
  return {
    heading: 'This page could not be displayed',
    detail: 'An unexpected error occurred while rendering it.',
  }
}
