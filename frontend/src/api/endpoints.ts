/**
 * Typed wrappers, one function per endpoint.
 *
 * Two rules keep this file useful rather than decorative.
 *
 * **Query strings are built here, not by callers.** `buildQuery` skips
 * `undefined` and `null` and expands arrays into repeated keys, which is what
 * the backend's repeated-parameter filters expect. A page assembling its own
 * URL is how `?severity=undefined` reaches the server and gets rejected as an
 * invalid filter.
 *
 * **Every response is typed.** There is no `any` and no `unknown` cast: if the
 * shape is not known well enough to name it, the UI cannot render it.
 */

import { apiFetch } from '@/api/client'
import type {
  ApiMeta,
  CountBucket,
  CoverageResponse,
  FindingDetailResponse,
  FindingSummary,
  HealthResponse,
  IntegrationStatusResponse,
  MeResponse,
  OverviewResponse,
  Page,
  ProcessingHealthResponse,
  PullRequestDetailResponse,
  PullRequestSummary,
  RecentActivityEntry,
  RepositoryDetailResponse,
  RepositorySummary,
  ReviewRunDetailResponse,
  ReviewRunSummary,
  SessionStateResponse,
  SettingsResponse,
  TrendResponse,
} from '@/types/api'

type QueryValue = string | number | boolean | null | undefined | readonly string[]

export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, item)
    } else {
      search.append(key, String(value))
    }
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

const V1 = '/api/v1'

/* -- Phase 0 ------------------------------------------------------------- */

export const getHealth = (): Promise<HealthResponse> => apiFetch<HealthResponse>('/health')

export const getApiMeta = (): Promise<ApiMeta> => apiFetch<ApiMeta>(`${V1}/meta`)

/* -- Overview and analytics ---------------------------------------------- */

export interface RangeParams {
  from?: string | undefined
  to?: string | undefined
  repository_id?: number | undefined
}

export const getOverview = (params: RangeParams = {}): Promise<OverviewResponse> =>
  apiFetch<OverviewResponse>(`${V1}/overview${buildQuery({ ...params })}`)

export const getFindingsBySeverity = (params: RangeParams = {}): Promise<CountBucket[]> =>
  apiFetch<CountBucket[]>(`${V1}/analytics/findings-by-severity${buildQuery({ ...params })}`)

export const getFindingsByCategory = (params: RangeParams = {}): Promise<CountBucket[]> =>
  apiFetch<CountBucket[]>(`${V1}/analytics/findings-by-category${buildQuery({ ...params })}`)

export const getRejectionReasons = (params: RangeParams = {}): Promise<CountBucket[]> =>
  apiFetch<CountBucket[]>(`${V1}/analytics/rejection-reasons${buildQuery({ ...params })}`)

export const getAnchorDistribution = (params: RangeParams = {}): Promise<CountBucket[]> =>
  apiFetch<CountBucket[]>(`${V1}/analytics/anchor-distribution${buildQuery({ ...params })}`)

export const getTrends = (
  params: RangeParams & { bucket?: 'day' | 'week' } = {},
): Promise<TrendResponse> =>
  apiFetch<TrendResponse>(`${V1}/analytics/trends${buildQuery({ ...params })}`)

export const getProcessingHealth = (
  params: RangeParams = {},
): Promise<ProcessingHealthResponse> =>
  apiFetch<ProcessingHealthResponse>(`${V1}/analytics/processing-health${buildQuery({ ...params })}`)

export const getCoverage = (params: RangeParams = {}): Promise<CoverageResponse> =>
  apiFetch<CoverageResponse>(`${V1}/analytics/coverage${buildQuery({ ...params })}`)

export const getRecentActivity = (limit = 10): Promise<RecentActivityEntry[]> =>
  apiFetch<RecentActivityEntry[]>(`${V1}/analytics/recent-activity${buildQuery({ limit })}`)

/* -- Repositories --------------------------------------------------------- */

export interface RepositoryListParams {
  q?: string | undefined
  is_active?: boolean | undefined
  limit?: number | undefined
  cursor?: string | undefined
}

export const getRepositories = (
  params: RepositoryListParams = {},
): Promise<Page<RepositorySummary>> =>
  apiFetch<Page<RepositorySummary>>(`${V1}/repositories${buildQuery({ ...params })}`)

export const getRepository = (id: number): Promise<RepositoryDetailResponse> =>
  apiFetch<RepositoryDetailResponse>(`${V1}/repositories/${id}`)

/* -- Pull requests -------------------------------------------------------- */

export interface PullRequestListParams {
  repository_id?: number | undefined
  state?: readonly string[] | undefined
  run_status?: readonly string[] | undefined
  q?: string | undefined
  from?: string | undefined
  to?: string | undefined
  limit?: number | undefined
  cursor?: string | undefined
}

export const getPullRequests = (
  params: PullRequestListParams = {},
): Promise<Page<PullRequestSummary>> =>
  apiFetch<Page<PullRequestSummary>>(`${V1}/pull-requests${buildQuery({ ...params })}`)

export const getPullRequest = (id: number): Promise<PullRequestDetailResponse> =>
  apiFetch<PullRequestDetailResponse>(`${V1}/pull-requests/${id}`)

/* -- Review runs ---------------------------------------------------------- */

export interface ReviewRunListParams {
  repository_id?: number | undefined
  pull_request_id?: number | undefined
  run_status?: readonly string[] | undefined
  include_superseded?: boolean | undefined
  from?: string | undefined
  to?: string | undefined
  limit?: number | undefined
  cursor?: string | undefined
}

export const getReviewRuns = (
  params: ReviewRunListParams = {},
): Promise<Page<ReviewRunSummary>> =>
  apiFetch<Page<ReviewRunSummary>>(`${V1}/review-runs${buildQuery({ ...params })}`)

export const getReviewRun = (id: string): Promise<ReviewRunDetailResponse> =>
  apiFetch<ReviewRunDetailResponse>(`${V1}/review-runs/${encodeURIComponent(id)}`)

/* -- Findings ------------------------------------------------------------- */

export interface FindingListParams {
  repository_id?: number | undefined
  pull_request_id?: number | undefined
  review_run_id?: string | undefined
  severity?: readonly string[] | undefined
  category?: readonly string[] | undefined
  status?: readonly string[] | undefined
  mapping_status?: readonly string[] | undefined
  validation?: string | undefined
  publication?: string | undefined
  rejection_reason?: string | undefined
  q?: string | undefined
  from?: string | undefined
  to?: string | undefined
  include_total?: boolean | undefined
  limit?: number | undefined
  cursor?: string | undefined
}

export const getFindings = (params: FindingListParams = {}): Promise<Page<FindingSummary>> =>
  apiFetch<Page<FindingSummary>>(`${V1}/findings${buildQuery({ ...params })}`)

export const getFinding = (id: string): Promise<FindingDetailResponse> =>
  apiFetch<FindingDetailResponse>(`${V1}/findings/${encodeURIComponent(id)}`)

/* -- Integration and settings --------------------------------------------- */

export const getIntegrationStatus = (): Promise<IntegrationStatusResponse> =>
  apiFetch<IntegrationStatusResponse>(`${V1}/integration/status`)

export const getSettings = (): Promise<SettingsResponse> =>
  apiFetch<SettingsResponse>(`${V1}/settings`)

/* -- Authentication -------------------------------------------------------- */

export const getSessionState = (): Promise<SessionStateResponse> =>
  apiFetch<SessionStateResponse>(`${V1}/auth/session`)

export const getMe = (): Promise<MeResponse> => apiFetch<MeResponse>(`${V1}/me`)

/**
 * Ends the session server-side.
 *
 * A POST, not a GET, and that is not pedantry: a GET logout can be triggered by
 * an `<img src>` on any page on the internet, which signs the user out
 * whenever they visit it. Being a POST also means `apiFetch` attaches the CSRF
 * header, so the request has to come from this application.
 */
export const logout = (): Promise<void> =>
  apiFetch<void>(`${V1}/auth/logout`, { method: 'POST' })

export const logoutEverywhere = (): Promise<void> =>
  apiFetch<void>(`${V1}/auth/logout-all`, { method: 'POST' })
