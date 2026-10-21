import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { RunMetrics } from '../components/RunMetrics'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { useAuth } from '../lib/auth'
import { orgRuns } from '../lib/endpoints'
import { formatDateTime } from '../lib/format'
import type { RunStatus } from '../lib/types'

const FILTERS: { value: RunStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'failed', label: 'Failed' },
  { value: 'running', label: 'Running' },
  { value: 'pending', label: 'Pending' },
  { value: 'success', label: 'Succeeded' },
  { value: 'cancelled', label: 'Cancelled' },
]

export function RunsPage() {
  const { token, activeOrgId } = useAuth()
  const [filter, setFilter] = useState<RunStatus | 'all'>('all')

  const query = useQuery({
    queryKey: ['org-runs', activeOrgId, filter],
    queryFn: () => orgRuns.list(token!, activeOrgId!, filter === 'all' ? undefined : filter),
    enabled: Boolean(token && activeOrgId),
  })

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-text">Runs</h1>
        <p className="mt-1 text-sm text-text-muted">
          Recent runs across all pipelines, as last recorded. Open a pipeline and click "Refresh runs" to pull the
          latest from Databricks.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              filter === f.value
                ? 'bg-accent text-accent-contrast'
                : 'border border-border text-text-muted hover:bg-border/60'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="rounded-lg border border-border bg-surface">
        {query.isLoading && <p className="p-5 text-text-muted">Loading runs…</p>}
        {query.isError && <p className="p-5 text-danger">Couldn't load runs — try refreshing.</p>}
        {query.data && query.data.length === 0 && (
          <p className="p-5 text-text-muted">
            {filter === 'all' ? 'No runs recorded yet.' : `No ${filter} runs.`}
          </p>
        )}
        {query.data && query.data.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs text-text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Pipeline</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 font-medium">Trigger</th>
                <th className="py-2 pr-4 font-medium">Started</th>
                <th className="py-2 pr-4 font-medium">Finished</th>
              </tr>
            </thead>
            <tbody>
              {query.data.map((r) => (
                <tr key={r.id} className="border-b border-border align-top last:border-0">
                  <td className="px-4 py-3">
                    <Link to={`/pipelines/${r.pipeline_id}`} className="font-medium text-text hover:underline">
                      {r.job_name}
                    </Link>
                  </td>
                  <td className="py-3 pr-4">
                    <RunStatusBadge status={r.status} />
                    {r.error_message && <p className="mt-1 max-w-sm text-xs text-danger">{r.error_message}</p>}
                    <RunMetrics metrics={r.metrics} />
                  </td>
                  <td className="py-3 pr-4 text-text-muted">{r.triggered_by}</td>
                  <td className="py-3 pr-4 text-text-muted">{formatDateTime(r.started_at ?? r.created_at)}</td>
                  <td className="py-3 pr-4 text-text-muted">{formatDateTime(r.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
