import { useQuery } from '@tanstack/react-query'
import { account } from '../lib/endpoints'
import { useAuth } from '../lib/auth'

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <p className="text-sm text-text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-text">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-text-muted">{sub}</p>}
    </div>
  )
}

export function DashboardPage() {
  const { token, activeOrgId, user } = useAuth()
  const activeMembership = user?.memberships.find((m) => m.org.id === activeOrgId)

  const usageQuery = useQuery({
    queryKey: ['usage', activeOrgId],
    queryFn: () => account.usage(token!, activeOrgId!),
    enabled: Boolean(token && activeOrgId),
  })

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold text-text">
        {activeMembership?.org.name ?? 'Dashboard'}
      </h1>
      <p className="mt-1 text-text-muted">
        {activeMembership?.org.tier ? `${activeMembership.org.tier} plan` : ''}
      </p>

      {usageQuery.isLoading && <p className="mt-6 text-text-muted">Loading usage…</p>}
      {usageQuery.isError && (
        <p className="mt-6 text-danger">Couldn't load usage — try refreshing.</p>
      )}

      {usageQuery.data && (
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard
            label="Pipelines"
            value={`${usageQuery.data.pipeline_count} / ${usageQuery.data.pipeline_limit}`}
            sub={usageQuery.data.can_create_pipeline ? 'Room to add more' : 'Plan limit reached'}
          />
          <StatCard
            label="Members"
            value={`${usageQuery.data.member_count} / ${usageQuery.data.member_limit}`}
            sub={usageQuery.data.pending_invites > 0 ? `${usageQuery.data.pending_invites} pending invite(s)` : undefined}
          />
        </div>
      )}

      <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <PlaceholderCard
          title="Sources & discovery"
          description="Browse the source registry and discover new schemas from your Databricks workspace."
        />
        <PlaceholderCard
          title="Pipelines"
          description="Build, preview and deploy ingestion pipelines."
        />
        <PlaceholderCard
          title="Runs"
          description="See run history and trigger a pipeline manually."
        />
        <PlaceholderCard
          title="Team & connection"
          description="Manage members, invites, and your Databricks workspace connection."
        />
      </div>
    </div>
  )
}

function PlaceholderCard({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-surface p-5">
      <h2 className="font-medium text-text">{title}</h2>
      <p className="mt-1 text-sm text-text-muted">{description}</p>
      <p className="mt-3 text-xs font-medium uppercase tracking-wide text-accent">Coming next</p>
    </div>
  )
}
