import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
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
          to="/sources"
        />
        <PlaceholderCard
          title="Pipelines"
          description="Build, preview and deploy ingestion pipelines, and see how they've been running."
          to="/pipelines"
        />
        <PlaceholderCard
          title="Tables"
          description="Every table your pipelines produce, by layer, and where it came from."
          to="/tables"
        />
        <PlaceholderCard
          title="Runs"
          description="Recent runs across every pipeline, filterable by status."
          to="/runs"
        />
        <PlaceholderCard
          title="Team"
          description="Invite people, and see who's in your organization."
          to="/team"
        />
        <PlaceholderCard
          title="Databricks connection"
          description="See, replace or disconnect the workspace this organization deploys to."
          to="/connection"
        />
      </div>
    </div>
  )
}

function PlaceholderCard({ title, description, to }: { title: string; description: string; to?: string }) {
  const body = (
    <>
      <h2 className="font-medium text-text">{title}</h2>
      <p className="mt-1 text-sm text-text-muted">{description}</p>
      <p className="mt-3 text-xs font-medium uppercase tracking-wide text-accent">
        {to ? 'Open →' : 'Coming next'}
      </p>
    </>
  )
  if (to) {
    return (
      <Link to={to} className="rounded-lg border border-border bg-surface p-5 hover:border-accent">
        {body}
      </Link>
    )
  }
  return <div className="rounded-lg border border-dashed border-border bg-surface p-5">{body}</div>
}
