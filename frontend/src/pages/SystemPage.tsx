import { Card } from '@/components/ui/Card'
import { ErrorState, PageHeader, SkeletonRows } from '@/components/ui/primitives'
import { useHealth } from '@/hooks/useHealth'

/**
 * Live backend health.
 *
 * This is the one page in Phase 0 that talks to the API, which makes it the
 * end-to-end proof that the frontend, the dev-server proxy, the API client
 * and the backend all agree — the frontend half of the Phase 0 exit criterion.
 */
export function SystemPage() {
  const health = useHealth()

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader title="System" subtitle="Live report from the backend health endpoint." />

      {health.status === 'loading' ? <SkeletonRows rows={4} /> : null}

      {health.status === 'error' ? (
        <ErrorState message={health.message} kind={health.kind} onRetry={health.refetch} />
      ) : null}

      {health.status === 'ready' ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Card>
            <h2 className="text-sm font-medium">Service</h2>
            <dl className="mt-2 space-y-1 text-sm">
              <Row label="Status" value={health.data.status} />
              <Row label="Version" value={health.data.version} />
              <Row label="Environment" value={health.data.environment} />
              <Row label="Uptime" value={`${Math.round(health.data.uptime_seconds)}s`} />
            </dl>
          </Card>

          <Card>
            <h2 className="text-sm font-medium">AI provider</h2>
            <dl className="mt-2 space-y-1 text-sm">
              <Row label="Selected" value={health.data.ai_provider.name} />
              <Row label="Configured" value={health.data.ai_provider.configured ? 'yes' : 'no'} />
              <Row
                label="Integration"
                value={health.data.ai_provider.implemented ? 'implemented' : 'deferred'}
              />
              <Row label="Available" value={health.data.ai_provider.available.join(', ')} />
            </dl>
            <p className="mt-3 text-xs text-muted">
              {health.data.ai_provider.implemented
                ? 'The integration is implemented. It has not been exercised against the live API in this build.'
                : 'The provider seam exists; the integration is not built for this provider.'}
            </p>
          </Card>

          <Card>
            <h2 className="text-sm font-medium">GitHub integration</h2>
            <dl className="mt-2 space-y-1 text-sm">
              <Row
                label="App ID"
                value={health.data.github.app_id_configured ? 'set' : 'not set'}
              />
              <Row
                label="Private key"
                value={health.data.github.private_key_configured ? 'set' : 'not set'}
              />
              <Row
                label="Webhook secret"
                value={health.data.github.webhook_secret_configured ? 'set' : 'not set'}
              />
              <Row label="Webhook path" value={health.data.github.webhook_path} />
            </dl>
            <p className="mt-3 text-xs text-muted">
              {health.data.github.app_configured
                ? 'Ready to receive deliveries.'
                : 'Deliveries are rejected until all three are configured.'}
            </p>
          </Card>

          <Card>
            <h2 className="text-sm font-medium">Dependencies</h2>
            <ul className="mt-2 space-y-1 text-sm">
              {health.data.dependencies.map((dep) => (
                <li key={dep.name} className="flex justify-between gap-4">
                  <span className="font-mono text-muted">{dep.name}</span>
                  <span>{dep.status}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      ) : null}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  )
}
