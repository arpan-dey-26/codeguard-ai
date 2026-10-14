/**
 * Backend response types.
 *
 * Hand-written and kept in one file so the API contract has exactly one
 * definition. Phase 0 planned to generate these from OpenAPI in Phase 4; that
 * has not happened, and the reason is recorded rather than hidden: the
 * generator toolchain cannot be installed in this environment (the npm
 * registry returns 403). The types below therefore mirror
 * `backend/app/schemas/dashboard.py` by hand, and the correspondence is
 * asserted by a test rather than assumed.
 *
 * `any` appears nowhere. A response field whose shape is unknown is a field
 * the UI cannot render correctly, and typing it `any` only moves the failure
 * from compile time to a blank panel in front of a user.
 */

import type { FindingCategory, FindingStatus, Severity } from '@/types/domain'

/* -------------------------------------------------------------------------
 * Shared
 * ---------------------------------------------------------------------- */

/** Keyset page. `next_cursor === null` is the last page. */
export interface Page<T> {
  items: T[]
  next_cursor: string | null
  /** Absent unless the endpoint was asked to count. Never inferred as 0. */
  total?: number | null
}

/** RFC 9457 problem document — the single error shape the API returns. */
export interface ProblemDetails {
  type: string
  title: string
  status: number
  detail?: string
  instance?: string
  correlation_id?: string | null
}

export interface CountBucket {
  key: string
  count: number
}

/* -------------------------------------------------------------------------
 * Health and metadata (Phase 0)
 * ---------------------------------------------------------------------- */

export type HealthStatus = 'ok' | 'degraded' | 'error'

export interface DependencyHealth {
  name: string
  status: HealthStatus
  detail: string | null
}

export interface AIProviderHealth {
  name: string
  configured: boolean
  implemented: boolean
  model: string | null
  available: string[]
}

export interface GitHubIntegrationHealth {
  app_configured: boolean
  app_id_configured: boolean
  private_key_configured: boolean
  webhook_secret_configured: boolean
  webhook_path: string
}

export interface HealthResponse {
  status: HealthStatus
  app: string
  version: string
  environment: string
  uptime_seconds: number
  dependencies: DependencyHealth[]
  ai_provider: AIProviderHealth
  github: GitHubIntegrationHealth
}

export interface ApiMeta {
  api_version: string
  app_version: string
  environment: string
}

/* -------------------------------------------------------------------------
 * Domain vocabulary
 * ---------------------------------------------------------------------- */

/**
 * A run's terminal state.
 *
 * `partial_success` is a first-class outcome and is never rendered as a pass:
 * findings exist and are visible, but they did not all reach GitHub.
 * `superseded` means a newer commit overtook the run — it is history, not a
 * result.
 */
export type RunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'partial_success'
  | 'failed'
  | 'superseded'
  | 'skipped'

export type PullRequestState = 'open' | 'closed' | 'merged'

export type MappingStatus = 'inline' | 'summary' | 'dashboard_only'

/**
 * Whether a model claim passed structural validation.
 *
 * `rejected` means the claim failed a structural gate — it could not be
 * anchored to the reviewed source. It does **not** mean the issue the model
 * described is unreal, and the UI must never label it a false positive.
 */
export type ValidationResult = 'validated' | 'rejected'

export type RejectionReason =
  | 'malformed'
  | 'secret_in_text'
  | 'unsafe_path'
  | 'unknown_file'
  | 'sha_mismatch'
  | 'wrong_side'
  | 'line_out_of_range'
  | 'line_not_in_diff'
  | 'line_not_presented'
  | 'no_source_line'
  | 'anchor_too_short'
  | 'anchor_not_found'
  | 'anchor_ambiguous'
  | 'low_confidence'
  | 'duplicate'
  | 'over_limit'

/* -------------------------------------------------------------------------
 * Overview and analytics
 * ---------------------------------------------------------------------- */

/**
 * The model-output funnel. Every stage is a different quantity; none is a
 * synonym for another.
 */
export interface FunnelCounts {
  /** Items the model put in its findings array. */
  claims_emitted: number
  /** Items the provider could not parse into a claim at all. */
  claims_dropped: number
  /** Claims that passed every structural gate. */
  validated_findings: number
  /** Claims that failed a structural gate. Not "false positives". */
  rejected_claims: number
  /** Comments GitHub confirmed. Read from stored publication state. */
  published_comments: number
  /** Validated findings moved to the review body instead of an inline comment. */
  findings_demoted: number
}

export interface RunCounts {
  total: number
  succeeded: number
  partial_success: number
  failed: number
  superseded: number
  in_flight: number
  /** Completed, but a budget stopped the context build before every file fit. */
  limited_coverage: number
}

/**
 * Claimed line versus resolved line, in counts.
 *
 * Not an accuracy measure. It describes the relationship between what the
 * model claimed and what the validator resolved *under this system's
 * relocation policy*.
 */
export interface AnchorCounts {
  exact: number
  relocated: number
}

export interface OverviewResponse {
  range_from: string
  range_to: string
  repositories_visible: number
  repositories_active: number
  /** Distinct pull requests with a completed review. Not attempt count. */
  pull_requests_reviewed: number
  runs: RunCounts
  funnel: FunnelCounts
  open_findings: number
  open_high_severity: number
  anchors: AnchorCounts
  duration_p50_ms: number | null
  duration_p95_ms: number | null
}

export interface TrendPoint {
  bucket_start: string
  runs_total: number
  runs_succeeded: number
  runs_partial: number
  runs_failed: number
  runs_superseded: number
  pull_requests: number
  validated_findings: number
  rejected_claims: number
}

export interface TrendResponse {
  bucket: 'day' | 'week'
  range_from: string
  range_to: string
  /** Always `UTC`. Stated so the client never has to guess. */
  timezone: string
  points: TrendPoint[]
}

export interface ProcessingHealthResponse {
  range_from: string
  range_to: string
  runs_total: number
  runs_terminal: number
  succeeded: number
  partial_success: number
  failed: number
  superseded: number
  skipped: number
  in_flight: number
  limited_coverage: number
  model_output_truncated: number
  duration_p50_ms: number | null
  duration_p95_ms: number | null
  error_breakdown: CountBucket[]
}

export interface CoverageResponse {
  range_from: string
  range_to: string
  files_changed: number
  files_analysed: number
  files_skipped: number
  runs_over_budget: number
  runs_total: number
  skip_reasons: CountBucket[]
}

export interface RecentActivityEntry {
  id: string
  status: RunStatus
  attempt: number
  head_sha: string
  is_stale: boolean
  limited_coverage: boolean
  error_code: string | null
  findings_inline: number
  rejected_claims: number
  duration_ms: number | null
  finished_at: string | null
  created_at: string | null
  pull_request_id: number
  pull_request_number: number
  title: string
  repository_id: number
  repository_full_name: string
}

/* -------------------------------------------------------------------------
 * Repositories
 * ---------------------------------------------------------------------- */

export interface RepositorySummary {
  id: number
  full_name: string
  owner_login: string
  name: string
  default_branch: string
  is_private: boolean
  is_active: boolean
  primary_language: string | null
  latest_review_at: string | null
  review_runs: number
  pull_requests_reviewed: number
  open_findings: number
  open_high_severity: number
}

export interface RepositoryDetailResponse {
  id: number
  full_name: string
  owner_login: string
  name: string
  default_branch: string
  is_private: boolean
  is_active: boolean
  primary_language: string | null
  created_at: string | null
  installation_account: string
  installation_account_type: string
  installation_suspended: boolean
  pull_requests_total: number
  pull_requests_reviewed: number
  review_runs_total: number
  review_runs_partial: number
  review_runs_failed: number
  latest_review_at: string | null
  validated_findings: number
  rejected_claims: number
  open_findings: number
  published_comments: number
}

/* -------------------------------------------------------------------------
 * Pull requests and review runs
 * ---------------------------------------------------------------------- */

export interface PullRequestSummary {
  id: number
  number: number
  title: string
  author_login: string
  state: PullRequestState
  is_draft: boolean
  head_sha: string
  html_url: string
  updated_at: string | null
  repository_id: number
  repository_full_name: string
  changed_files_count: number
  changed_files_truncated: boolean
  /**
   * The newest non-superseded run **for the current head commit**.
   * `null` means this pull request's current commit has not been reviewed —
   * a real state, and never rendered as a pass.
   */
  current_run_id: string | null
  current_run_status: RunStatus | null
  current_run_attempt: number | null
  current_run_finished_at: string | null
  current_run_duration_ms: number | null
  current_run_limited_coverage: boolean
  /** Attempts across every commit. History, never a "PRs reviewed" total. */
  review_run_count: number
  validated_findings: number
  rejected_claims: number
  published_comments: number
  open_critical_high: number
}

export interface ReviewRunSummary {
  id: string
  status: RunStatus
  trigger: string
  attempt: number
  head_sha: string
  /** True when this run's commit is no longer the pull request's head. */
  is_stale: boolean
  error_code: string | null
  limited_coverage: boolean
  model_output_truncated: boolean
  claims_emitted: number
  claims_dropped: number
  findings_inline: number
  findings_summary: number
  rejected_claims: number
  published_comments: number
  files_changed: number
  files_analysed: number
  files_skipped: number
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  created_at: string | null
  pull_request_id: number | null
  pull_request_number: number | null
  title: string | null
  repository_id: number | null
  repository_full_name: string | null
}

export interface PullRequestDetailResponse {
  id: number
  number: number
  title: string
  author_login: string
  state: PullRequestState
  is_draft: boolean
  head_sha: string
  base_sha: string
  base_ref: string
  head_ref: string
  html_url: string
  changed_files_count: number
  changed_files_truncated: boolean
  opened_at: string | null
  closed_at: string | null
  merged_at: string | null
  updated_at: string | null
  last_synced_at: string | null
  repository_id: number
  repository_full_name: string
  default_branch: string
  repository_active: boolean
  current_run_id: string | null
  runs: ReviewRunSummary[]
}

export interface ChangedFileSummary {
  id: number
  file_path: string
  previous_path: string | null
  change_status: string
  additions: number
  deletions: number
  changes: number
  has_patch: boolean
  language: string | null
  is_analysed: boolean
  skip_reason: string | null
  parse_ok: boolean | null
  declarations_touched: number | null
  validated_findings: number
}

export interface ReviewRunDetailResponse {
  id: string
  status: RunStatus
  trigger: string
  attempt: number
  head_sha: string
  base_sha: string
  is_stale: boolean
  error_code: string | null
  error_detail: string | null
  provider: string | null
  model_name: string | null
  prompt_version: string | null
  input_tokens: number | null
  output_tokens: number | null
  ai_latency_ms: number | null
  model_output_truncated: boolean
  limited_coverage: boolean
  files_changed: number
  files_analysed: number
  files_skipped: number
  funnel: FunnelCounts
  findings_persisted: number
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  created_at: string | null
  github_review_id: number | null
  pull_request_id: number
  pull_request_number: number
  pull_request_title: string
  pull_request_url: string
  repository_id: number
  repository_full_name: string
  changed_files: ChangedFileSummary[]
}

/* -------------------------------------------------------------------------
 * Findings
 * ---------------------------------------------------------------------- */

export interface FindingSummary {
  id: string
  severity: Severity
  category: FindingCategory
  title: string
  status: FindingStatus
  mapping_status: MappingStatus
  validation: ValidationResult
  rejection_reason: RejectionReason | null
  file_path: string
  /** What the model said. Kept separate from `resolved_line` on purpose. */
  claimed_line: number
  /** What the validator determined, or null if it could not. */
  resolved_line: number | null
  anchor_offset: number | null
  anchor_verified: boolean
  side: string
  qualified_name: string
  model_confidence: number | null
  effective_confidence: number | null
  cwe_id: string | null
  /** True iff a stored comment row exists — GitHub confirmed it. */
  is_published: boolean
  is_stale: boolean
  created_at: string | null
  review_run_id: string
  pull_request_id: number
  pull_request_number: number
  repository_id: number
  repository_full_name: string
  head_sha: string
}

export interface FindingEventEntry {
  id: number
  from_status: string
  to_status: string
  source: string
  note: string | null
  created_at: string | null
}

export interface SourceContextLine {
  line: number
  text: string
  is_anchor: boolean
}

export interface FindingDetailResponse {
  id: string
  severity: Severity
  category: FindingCategory
  title: string
  description: string
  impact: string
  suggested_fix: string | null
  rationale: string | null
  cwe_id: string | null
  status: FindingStatus
  mapping_status: MappingStatus
  validation: ValidationResult
  rejection_reason: RejectionReason | null

  file_path: string
  claimed_line: number
  claimed_line_end: number | null
  resolved_line: number | null
  anchor_offset: number | null
  anchor_snippet: string
  anchor_verified: boolean
  side: string
  qualified_name: string
  declaration_id: string | null
  fingerprint: string

  model_confidence: number | null
  effective_confidence: number | null
  model_finding_id: string
  model_name: string | null
  prompt_version: string | null

  head_sha: string
  is_stale: boolean
  review_run_id: string
  review_run_status: RunStatus
  review_run_attempt: number
  repository_id: number
  repository_full_name: string
  pull_request_id: number
  pull_request_number: number
  pull_request_url: string

  is_published: boolean
  published_comment_url: string | null
  published_at: string | null
  published_line: number | null
  published_side: string | null

  created_at: string | null
  updated_at: string | null
  events: FindingEventEntry[]
  /**
   * At most one line: the stored anchor snippet.
   *
   * Phase 3 does not persist repository source, so this is the only source
   * text the API can return. The UI says so rather than implying a window was
   * withheld.
   */
  source_context: SourceContextLine[]
}

/* -------------------------------------------------------------------------
 * Integration and settings
 * ---------------------------------------------------------------------- */

export interface InstallationSummary {
  id: number
  github_installation_id: number
  account_login: string
  account_type: string
  repository_selection: string | null
  suspended: boolean
  uninstalled: boolean
  repository_count: number
  active_repository_count: number
  last_event_at: string | null
  failed_deliveries: number
}

export interface IntegrationStatusResponse {
  github_app_configured: boolean
  webhook_path: string
  installations: InstallationSummary[]
}

/**
 * Non-secret configuration.
 *
 * Every field is a literal or a boolean *about* a secret. No key, token or
 * secret value has a field here, and there is no write endpoint.
 */
export interface SettingsResponse {
  environment: string
  app_version: string
  github_app_configured: boolean
  github_webhook_secret_configured: boolean
  ai_provider: string
  ai_model: string | null
  ai_provider_configured: boolean
  ai_provider_implemented: boolean
  prompt_version: string
  review_publishing_enabled: boolean
  dashboard_authenticated: boolean
  dashboard_unauthenticated_reads_enabled: boolean
  max_inline_comments: number
  min_confidence: number
  min_confidence_high_severity: number
  max_files_per_review: number
  max_findings_per_review: number
}

/* -- Authentication (Phase 5) ---------------------------------------------- */

/**
 * The signed-in user.
 *
 * Note what is absent: any token, any secret, any credential. The session
 * lives in an HttpOnly cookie the browser handles on its own, so there is
 * nothing here for JavaScript to hold and nothing for an XSS payload to read.
 */
export interface AuthUser {
  id: number
  github_user_id: number
  login: string
  name: string | null
  avatar_url: string | null
}

/**
 * One GitHub App installation the user may read.
 *
 * This list is the *authorization boundary*, not a filter widget. The server
 * derives it from `user_installation_access` and scopes every dashboard query
 * to it; the client never sends an installation id to widen what it sees.
 */
export interface InstallationAccess {
  id: number
  account_login: string
  account_type: string
  repository_count: number
  suspended: boolean
}

export interface MeResponse {
  user: AuthUser
  installations: InstallationAccess[]
  session_expires_at: string
  /** When the installation list was last refreshed from GitHub. Access
   *  revocation takes effect within one refresh interval — a documented,
   *  bounded staleness rather than an oversight (architecture.md §16.3). */
  access_synced_at: string | null
  /** This session's CSRF token. Also delivered as a readable cookie; it is not
   *  a credential on its own and authenticates nothing by itself. */
  csrf_token: string
}

/**
 * Always returns 200, signed in or not.
 *
 * Exists so the boot sequence can ask "is there a session?" without treating a
 * 401 from `/me` as an error worth rendering.
 */
export interface SessionStateResponse {
  authenticated: boolean
  login_url: string
  sign_in_available: boolean
  /**
   * The deployment is serving dashboard reads without authentication
   * (`DASHBOARD_ALLOW_UNAUTHENTICATED_READS`). Development only — the backend
   * refuses to start in production with it on.
   *
   * Optional so an older backend, which does not send it, is read as `false`
   * rather than as `undefined` leaking into a boolean check.
   */
  unauthenticated_reads_enabled?: boolean
}
