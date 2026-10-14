import { createBrowserRouter, Navigate } from 'react-router-dom'
import { RouteErrorBoundary } from '@/components/RouteErrorBoundary'
import { AppShell } from '@/layouts/AppShell'
import { DashboardPage } from '@/pages/DashboardPage'
import { FindingDetailPage } from '@/pages/FindingDetailPage'
import { FindingsPage } from '@/pages/FindingsPage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { PullRequestDetailPage } from '@/pages/PullRequestDetailPage'
import { PullRequestsPage } from '@/pages/PullRequestsPage'
import { RepositoriesPage } from '@/pages/RepositoriesPage'
import { RepositoryDetailPage } from '@/pages/RepositoryDetailPage'
import { ReviewRunPage } from '@/pages/ReviewRunPage'
import { ReviewRunsPage } from '@/pages/ReviewRunsPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { SystemPage } from '@/pages/SystemPage'

/**
 * Route tree.
 *
 * Paths follow architecture.md §18.3 — `/` for the dashboard and
 * `/review-runs/:id` for a review — rather than the `/dashboard` and
 * `/reviews/:id` shapes a request might suggest. `/dashboard` and `/reviews`
 * redirect, so a link built from either spelling still lands correctly.
 *
 * Still eagerly imported. Route-level code splitting is worth introducing when
 * the initial bundle measurably hurts first paint; at eleven small pages
 * sharing one component library, splitting would add request waterfalls to
 * save very little.
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    // **v0.6.1.** Without this, a throw inside a page — or a router-level
    // failure — was handled by React Router's unstyled default screen, which
    // does not look like this application and offers no way back. With it, the
    // failure renders in the shell, so the navigation still works and the user
    // can go somewhere else instead of reaching for the back button.
    errorElement: <RouteErrorBoundary />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'dashboard', element: <Navigate to="/" replace /> },

      { path: 'repositories', element: <RepositoriesPage /> },
      { path: 'repositories/:repositoryId', element: <RepositoryDetailPage /> },

      { path: 'pull-requests', element: <PullRequestsPage /> },
      { path: 'pull-requests/:pullRequestId', element: <PullRequestDetailPage /> },

      { path: 'review-runs', element: <ReviewRunsPage /> },
      { path: 'review-runs/:reviewRunId', element: <ReviewRunPage /> },
      { path: 'reviews', element: <Navigate to="/review-runs" replace /> },
      { path: 'reviews/:reviewRunId', element: <ReviewRunRedirect /> },

      { path: 'findings', element: <FindingsPage /> },
      { path: 'findings/:findingId', element: <FindingDetailPage /> },

      { path: 'settings', element: <SettingsPage /> },
      { path: 'system', element: <SystemPage /> },

      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

/** Keeps `/reviews/:id` working without duplicating the review page. */
function ReviewRunRedirect() {
  const id = window.location.pathname.split('/').pop() ?? ''
  return <Navigate to={`/review-runs/${id}`} replace />
}
