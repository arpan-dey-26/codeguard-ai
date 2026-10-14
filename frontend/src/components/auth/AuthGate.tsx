/**
 * Renders the application only for a signed-in user.
 *
 * **This is presentation, not security.** Every byte of data still comes from
 * an API call the server authorises on its own; hiding a route here stops a
 * confusing empty page, and stops nothing else. A component that treats a
 * client-side check as the access control is how a dashboard ends up
 * "protected" by a boolean an attacker can flip in a debugger — so the server
 * scopes every query regardless of what this renders.
 *
 * Five states, five different screens, because they call for five different
 * things from the user:
 *
 *   loading     wait — we are asking the server
 *   unavailable the API is not answering; start it (v0.6.1)
 *   anonymous   sign in, or be told sign-in is not configured here
 *   open        no session, and the server serves reads anyway (v0.6.1)
 *   ready       the application
 *
 * `unavailable` and `anonymous` are kept apart deliberately. Collapsing them
 * is what made the frontend, run on its own, announce that the deployment had
 * no OAuth credentials — a statement about configuration that nothing had
 * checked.
 *
 * The `anonymous` and `unavailable` screens moved into `components/marketing/`
 * in v0.6.2. This file keeps the state machine; those own the presentation.
 */

import { ApiUnavailable, Hero } from '@/components/marketing/Hero'
import { useAuth } from '@/hooks/useAuth'

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { state, signIn, refresh } = useAuth()

  if (state.status === 'unavailable') {
    // **v0.6.1.** Previously this case fell through to "sign-in is not
    // configured", which told a developer running `npm run dev` on its own
    // something false about their configuration. The API being absent is a
    // different fact with a different remedy, so it gets its own screen and
    // names the remedy.
    return <ApiUnavailable detail={state.detail} onRetry={() => void refresh()} />
  }

  if (state.status === 'loading') {
    // Deliberately quiet, and deliberately not a spinner. This resolves in one
    // round trip; a spinner that flashes for 80ms is worse than a line of text,
    // and the hero's entrance animation starting from a spinner reads as a jump.
    return (
      <div
        className="flex min-h-screen items-center justify-center px-4"
        role="status"
        aria-live="polite"
      >
        <h1 className="animate-fade-in text-sm text-muted">Checking your session…</h1>
      </div>
    )
  }

  if (state.status === 'anonymous') {
    return (
      <Hero mode={state.signInAvailable ? 'sign-in' : 'not-configured'} onSignIn={() => signIn()} />
    )
  }

  // `open` and `authenticated` both render the application. The difference is
  // announced by the banner in `AppShell`, which is on every page and cannot be
  // dismissed — an unauthenticated deployment should never be mistakable for an
  // authenticated one, and a one-off toast on the first page is exactly how it
  // would be.
  return <>{children}</>
}
