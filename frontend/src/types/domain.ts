/**
 * Domain vocabulary shared across the UI.
 *
 * These unions mirror the closed enums the backend enforces
 * (architecture.md §12.1). Keeping them as unions rather than strings means
 * a typo in a severity name fails at compile time.
 */

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'

export type FindingCategory =
  | 'bug'
  | 'security'
  | 'performance'
  | 'resource'
  | 'maintainability'

export type FindingStatus = 'open' | 'resolved' | 'dismissed' | 'false_positive'

export type Theme = 'light' | 'dark'
