import { Link } from 'react-router-dom'
import { getFindings } from '@/api/endpoints'
import { AsyncSection } from '@/components/data/AsyncSection'
import { PublicationBadge, SeverityBadge, ValidationBadge } from '@/components/domain/badges'
import { Card } from '@/components/ui/Card'
import {
  Button,
  Caveat,
  ChipFilter,
  EmptyState,
  Mono,
  PageHeader,
  SearchInput,
  Select,
  SkeletonTable,
} from '@/components/ui/primitives'
import { Table, type Column } from '@/components/ui/Table'
import { Pager } from '@/pages/RepositoriesPage'
import { useApiQuery } from '@/hooks/useApiQuery'
import { useUrlFilters } from '@/hooks/useUrlFilters'
import type { FindingSummary } from '@/types/api'
import { formatNumber, formatRelative, humanise } from '@/utils/format'
import { SEVERITY_ORDER } from '@/utils/severity'

const SEVERITY_OPTIONS = SEVERITY_ORDER.map((value) => ({
  value,
  label: value.charAt(0).toUpperCase() + value.slice(1),
}))

const CATEGORY_OPTIONS = [
  { value: 'security', label: 'Security' },
  { value: 'bug', label: 'Bug' },
  { value: 'resource', label: 'Resource' },
  { value: 'performance', label: 'Performance' },
  { value: 'maintainability', label: 'Maintainability' },
]

const STATUS_OPTIONS = [
  { value: 'open', label: 'Open' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: 'false_positive', label: 'Marked false positive' },
]

const VALIDATION_OPTIONS = [
  { value: '', label: 'All claims' },
  { value: 'validated', label: 'Validated only' },
  { value: 'rejected', label: 'Rejected claims only' },
]

const PUBLICATION_OPTIONS = [
  { value: '', label: 'Any' },
  { value: 'published', label: 'Published to GitHub' },
  { value: 'not_published', label: 'Not published' },
]

/**
 * The findings explorer.
 *
 * Filters live in the URL so a view can be bookmarked and shared, and the
 * server does the filtering, the searching and the paging — the browser never
 * receives the whole table and never computes a total over a page.
 *
 * The vocabulary is chosen to be unmisreadable. The filter is called
 * “Validation”, its values are “Validated” and “Rejected claims”, and the
 * caveat below the table says plainly what each one does and does not mean.
 */

export function FindingsPage() {
  const filters = useUrlFilters()
  const search = filters.get('q') ?? ''
  const severities = filters.getAll('severity')
  const categories = filters.getAll('category')
  const statuses = filters.getAll('status')
  const validation = filters.get('validation') ?? ''
  const publication = filters.get('publication') ?? ''
  const repositoryId = filters.getNumber('repository_id')
  const pullRequestId = filters.getNumber('pull_request_id')
  const cursor = filters.cursor

  const query = useApiQuery(
    () =>
      getFindings({
        q: search || undefined,
        severity: severities.length ? severities : undefined,
        category: categories.length ? categories : undefined,
        status: statuses.length ? statuses : undefined,
        validation: validation || undefined,
        publication: publication || undefined,
        repository_id: repositoryId,
        pull_request_id: pullRequestId,
        include_total: true,
        cursor,
        limit: 25,
      }),
    [
      search,
      severities.join(','),
      categories.join(','),
      statuses.join(','),
      validation,
      publication,
      repositoryId,
      pullRequestId,
      cursor,
    ],
    { keepPreviousData: true },
  )

  const columns: ReadonlyArray<Column<FindingSummary>> = [
    {
      key: 'title',
      header: 'Finding',
      render: (row) => (
        <span className="block min-w-0">
          <span className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={row.severity} />
            <span className="text-content">{row.title}</span>
          </span>
          <span className="mt-1 block text-xs text-muted">
            <Mono className="break-all">{row.file_path}</Mono>
            {row.resolved_line !== null ? <Mono>:{row.resolved_line}</Mono> : null}
            {row.qualified_name ? <> · {row.qualified_name}</> : null}
          </span>
        </span>
      ),
    },
    {
      key: 'repo',
      header: 'Where',
      priority: 'low',
      render: (row) => (
        <span className="block text-xs">
          <Mono className="text-muted">{row.repository_full_name}</Mono>
          <span className="mt-0.5 block text-muted">#{row.pull_request_number}</span>
        </span>
      ),
    },
    {
      key: 'category',
      header: 'Category',
      priority: 'low',
      render: (row) => <span className="text-muted">{humanise(row.category)}</span>,
    },
    {
      key: 'validation',
      header: 'Validation',
      render: (row) => (
        <span className="flex flex-col gap-1">
          <ValidationBadge validation={row.validation} />
          {row.rejection_reason ? (
            <span className="text-[11px] text-muted">
              gate: {humanise(row.rejection_reason)}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: 'published',
      header: 'GitHub',
      render: (row) => <PublicationBadge isPublished={row.is_published} />,
    },
    {
      key: 'created',
      header: 'Found',
      align: 'right',
      render: (row) => (
        <time dateTime={row.created_at ?? undefined} title={row.created_at ?? undefined}>
          {formatRelative(row.created_at)}
        </time>
      ),
    },
  ]

  return (
    <div className="mx-auto max-w-[1180px]">
      <PageHeader
        title="Findings"
        subtitle="Every model claim recorded by a review — the ones that passed structural validation and the ones that did not."
      />

      <Card>
        <div className="mb-4 flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <SearchInput
              label="Search findings"
              value={search}
              placeholder="Filter by title…"
              onChange={(value) => filters.set('q', value)}
            />
            <Select
              label="Validation"
              value={validation}
              options={VALIDATION_OPTIONS}
              onChange={(value) => filters.set('validation', value)}
            />
            <Select
              label="Publication"
              value={publication}
              options={PUBLICATION_OPTIONS}
              onChange={(value) => filters.set('publication', value)}
            />
            {filters.activeCount > 0 ? (
              <Button variant="subtle" onClick={filters.clear}>
                Clear filters
              </Button>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-6">
            <ChipFilter
              legend="Severity"
              options={SEVERITY_OPTIONS}
              selected={severities}
              onToggle={(value) => filters.toggle('severity', value)}
            />
            <ChipFilter
              legend="Category"
              options={CATEGORY_OPTIONS}
              selected={categories}
              onToggle={(value) => filters.toggle('category', value)}
            />
            <ChipFilter
              legend="Status"
              options={STATUS_OPTIONS}
              selected={statuses}
              onToggle={(value) => filters.toggle('status', value)}
            />
          </div>
        </div>

        <AsyncSection
          query={query}
          skeleton={<SkeletonTable rows={8} columns={5} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title={
                filters.activeCount > 0
                  ? 'No findings match these filters'
                  : 'No findings recorded yet'
              }
              hint={
                filters.activeCount > 0
                  ? 'Nothing matches — which is different from nothing existing. Clear the filters to see everything.'
                  : 'Findings appear once a review runs on a monitored pull request. An empty result from a review is also a valid outcome.'
              }
              action={
                filters.activeCount > 0 ? (
                  <Button onClick={filters.clear}>Clear filters</Button>
                ) : null
              }
            />
          }
        >
          {(data) => (
            <>
              {typeof data.total === 'number' ? (
                <p className="mb-2 text-xs text-muted">
                  {formatNumber(data.total)} matching finding
                  {data.total === 1 ? '' : 's'}
                  {query.isRefreshing ? ' · updating…' : null}
                </p>
              ) : null}
              <Table
                caption="Findings"
                columns={columns}
                rows={data.items}
                rowKey={(row) => row.id}
                onRowHref={(row) => `/findings/${row.id}`}
              />
              <Pager
                hasPrevious={cursor !== undefined}
                nextCursor={data.next_cursor}
                onNext={(next) => filters.setCursor(next)}
                onReset={() => filters.setCursor(undefined)}
              />
            </>
          )}
        </AsyncSection>

        <Caveat>
          <strong>Validated</strong> means the claim was checked against the diff and the
          file at the reviewed commit — it is anchored to real code, not independently
          proven. <strong>Rejected claim</strong> means it failed a structural validation
          gate; it is <em>not</em> a confirmed false positive, because no semantic
          evaluation has been run.{' '}
          <Link to="/settings" className="text-accent">
            What the system does and does not measure
          </Link>
          .
        </Caveat>
      </Card>
    </div>
  )
}
