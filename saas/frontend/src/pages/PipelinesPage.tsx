import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Badge } from '../components/Badge'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { useAuth } from '../lib/auth'
import { pipelines } from '../lib/endpoints'
import { formatDateTime } from '../lib/format'

export function PipelinesPage() {
  const { token, activeOrgId } = useAuth()
  const query = useQuery({
    queryKey: ['pipelines', activeOrgId],
    queryFn: () => pipelines.list(token!, activeOrgId!),
    enabled: Boolean(token && activeOrgId),
  })

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-text">Pipelines</h1>
        <div className="flex gap-2">
          <Link
            to="/pipelines/new"
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover"
          >
            New Raw pipeline
          </Link>
          <Link
            to="/pipelines/new/bronze"
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text hover:bg-border/60"
          >
            New Bronze pipeline
          </Link>
          <Link
            to="/pipelines/new/silver"
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text hover:bg-border/60"
          >
            New Silver pipeline
          </Link>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-surface">
        {query.isLoading && <p className="p-5 text-text-muted">Loading pipelines…</p>}
        {query.isError && <p className="p-5 text-danger">Couldn't load pipelines — try refreshing.</p>}
        {query.data && query.data.length === 0 && (
          <p className="p-5 text-text-muted">
            No pipelines yet. Discover a source first, then click "New Raw pipeline" to load it.
          </p>
        )}
        {query.data && query.data.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs text-text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium">Layer</th>
                <th className="py-2 pr-4 font-medium">Target</th>
                <th className="py-2 pr-4 font-medium">Tables</th>
                <th className="py-2 pr-4 font-medium">Schedule</th>
                <th className="py-2 pr-4 font-medium">Last run</th>
                <th className="py-2 pr-4 font-medium" />
              </tr>
            </thead>
            <tbody>
              {query.data.map((p) => (
                <tr key={p.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3">
                    <Link to={`/pipelines/${p.id}`} className="font-medium text-text hover:underline">
                      {p.job_name}
                    </Link>
                  </td>
                  <td className="py-3 pr-4">
                    <Badge>{p.layer}</Badge>
                  </td>
                  <td className="py-3 pr-4 font-mono text-xs text-text-muted">
                    {p.catalog}.{p.target_schema}
                  </td>
                  <td className="py-3 pr-4 text-text-muted">{p.layer === 'raw' ? p.tables.length : '—'}</td>
                  <td className="py-3 pr-4 text-text-muted">
                    {p.schedule_frequency ?? '—'}
                    {p.schedule_time ? ` ${p.schedule_time}` : ''}
                  </td>
                  <td className="py-3 pr-4">
                    {p.last_run ? (
                      <span className="flex items-center gap-2">
                        <RunStatusBadge status={p.last_run.status} />
                        <span className="text-xs text-text-muted">
                          {formatDateTime(p.last_run.started_at ?? p.last_run.created_at)}
                        </span>
                      </span>
                    ) : (
                      <span className="text-text-muted">—</span>
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    {p.tables.some((t) => t.is_stale) && <Badge tone="danger">schema changed</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
