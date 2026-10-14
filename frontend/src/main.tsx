import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { router } from '@/app/routes'
import { RootErrorBoundary } from '@/components/RootErrorBoundary'
import { AuthGate } from '@/components/auth/AuthGate'
import { AuthProvider } from '@/hooks/useAuth'
import '@/styles/globals.css'

const container = document.getElementById('root')
if (!container) throw new Error('Root element #root is missing from index.html')

// The static fallback in index.html is removed only once React is about to
// take over. Leaving it until now means a bundle that fails to load, or a
// module that throws before this line, leaves the fallback on screen instead
// of an empty page.
document.getElementById('boot-fallback')?.remove()

// The provider wraps the router rather than living inside a route, so the
// session is resolved once for the whole application instead of once per
// navigation — and so `AuthGate` can decide what to render before any page
// mounts and starts fetching data it is not entitled to.
// The boundary is outermost. Inside it, a throw in the auth provider, the
// gate, or any page is an error screen; outside it, the same throw is a blank
// page (v0.6.1).
createRoot(container).render(
  <StrictMode>
    <RootErrorBoundary>
      <AuthProvider>
        <AuthGate>
          <RouterProvider router={router} />
        </AuthGate>
      </AuthProvider>
    </RootErrorBoundary>
  </StrictMode>,
)
