/**
 * The last thing between a render-time exception and a blank white page.
 *
 * **Added in v0.6.1.** React unmounts the entire tree when a render throws and
 * nothing catches it. With no boundary anywhere in the application, any such
 * error — a malformed API shape reaching a component that did not expect it, a
 * null where a page assumed an object — produced an empty `<body>` and a stack
 * trace only in the console. "The page is blank" is the least actionable bug
 * report there is, and it was the default failure mode.
 *
 * Deliberately a class component: `componentDidCatch` and
 * `getDerivedStateFromError` have no hook equivalent. This is the one place in
 * the codebase where that is true.
 *
 * It does **not** report anywhere. There is no error-reporting service
 * configured for this project, and adding one would be a new external
 * dependency, which this release is not for. The error goes to the console, its
 * message goes on screen, and the person reading it can act on both.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Shown above the message. Lets a route-level boundary say where it was. */
  where?: string
}

interface State {
  error: Error | null
}

export class RootErrorBoundary extends Component<Props, State> {
  // Assigned in the constructor rather than as a class field. With
  // `useDefineForClassFields: true` a field initialiser is a `defineProperty`
  // on the instance, which shadows rather than assigns the base class's
  // `state`; the constructor form is the one React's own typings are written
  // for and has no such subtlety.
  constructor(props: Props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The component stack is the genuinely useful part and is not on the error
    // object, so it is logged explicitly rather than relying on React's own
    // console output, which is suppressed in production builds.
    console.error('CodeGuard AI: render failed', error, info.componentStack)
  }

  private readonly reset = (): void => {
    this.setState({ error: null })
  }

  render(): ReactNode {
    const { error } = this.state
    if (error === null) return this.props.children

    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div
          className="w-full max-w-lg rounded-lg border border-border bg-surface-raised p-6"
          role="alert"
        >
          <h1 className="font-mono text-base font-semibold tracking-tight">
            CodeGuard AI
          </h1>
          <p className="mt-2 text-sm font-medium text-state-bad">
            {this.props.where
              ? `Something went wrong in ${this.props.where}.`
              : 'Something went wrong rendering this page.'}
          </p>
          <p className="mt-1 text-sm text-muted">
            This is a bug in the dashboard, not something you did. The full
            details are in the browser console.
          </p>

          {/* The message, not the stack. A stack on screen is noise to almost
              everyone; the message is often enough to recognise the problem,
              and the console has the rest. */}
          <pre className="mt-3 overflow-x-auto rounded-md border border-border bg-surface p-3 font-mono text-xs leading-relaxed text-muted">
            <code>{error.message || String(error)}</code>
          </pre>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={this.reset}
              className="rounded-md border border-border bg-surface px-3 py-2 text-sm font-medium hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              Try again
            </button>
            <button
              type="button"
              onClick={() => window.location.assign('/')}
              className="rounded-md border border-border px-3 py-2 text-sm text-muted hover:text-content focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              Back to the dashboard
            </button>
          </div>
        </div>
      </div>
    )
  }
}
