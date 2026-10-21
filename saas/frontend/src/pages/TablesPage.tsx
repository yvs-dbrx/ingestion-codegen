import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Badge } from '../components/Badge'
import { useAuth } from '../lib/auth'
import { assets } from '../lib/endpoints'
import { formatDateTime } from '../lib/format'
import type { AssetLayer } from '../lib/types'

const FILTERS: { value: AssetLayer | 'all'; label: string }[] = [
  { value: 'all', label: 'All layers' },
  { value: 'raw', label: 'Raw' },
  { value: 'bronze', label: 'Bronze' },
  { value: 'silver', label: 'Silver' },
  { value: 'gold', label: 'Gold' },
  { value: 'reference', label: 'Reference' },
]

const LAYER_TONE: Record<AssetLayer, 'neutral' | 'gold' | 'success'> = {
  raw: 'neutral',
  bronze: 'neutral',
  silver: 'neutral',
  gold: 'gold',
  reference: 'success',
}

const LAYER_NOTES: [string, string][] = [
  ['Raw', 'Every file as loaded, every time — duplicates included.'],
  ['Bronze', 'Distinct records only.'],
  ['Silver', 'Integrated with master keys, in third normal form.'],
  ['Gold', 'Facts, dimensions, views and aggregates.'],
]

export function TablesPage() {
  const { token, activeOrgId } = useAuth()
  const [filter, setFilter] = useState<AssetLayer | 'all'>('all')

  const query = useQuery({
    queryKey: ['assets', activeOrgId, filter],
    queryFn: () => assets.list(token!, activeOrgId!, filter === 'all' ? undefined : filter),
    enabled: Boolean(token && activeOrgId),
  })

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-text">Tables</h1>
        <p className="mt-1 text-sm text-text-muted">
          Every table your pipelines produce and where it came from. Only names and metadata are kept here — the
          data itself stays in your Databricks workspace.
        </p>
      </div>

      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 rounded-lg border border-border bg-surface p-4 text-sm sm:grid-cols-2">
        {LAYER_NOTES.map(([name, note]) => (
          <div key={name} className="flex gap-2">
            <dt className="w-16 shrink-0 font-medium text-text">{name}</dt>
            <dd className="text-text-muted">{note}</dd>
          </div>
        ))}
      </dl>

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
        {query.isLoading && <p className="p-5 text-text-muted">Loading tables…</p>}
        {query.isError && <p className="p-5 text-danger">Couldn't load tables — try refreshing.</p>}
        {query.data && query.data.length === 0 && (
          <p className="p-5 text-text-muted">
            {filter === 'all'
              ? 'No tables yet. Deploy a pipeline and its target table appears here.'
              : `No ${filter} tables.`}
          </p>
        )}
        {query.data && query.data.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs text-text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Table</th>
                <th className="py-2 pr-4 font-medium">Layer</th>
                <th className="py-2 pr-4 font-medium">Loaded from</th>
                <th className="py-2 pr-4 font-medium">Produced by</th>
                <th className="py-2 pr-4 font-medium">Rows</th>
                <th className="py-2 pr-4 font-medium">Last run</th>
              </tr>
            </thead>
            <tbody>
              {query.data.map((a) => (
                <tr key={a.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 font-mono text-xs text-text">{a.full_name}</td>
                  <td className="py-3 pr-4">
                    <Badge tone={LAYER_TONE[a.layer]}>{a.layer}</Badge>
                  </td>
                  <td className="max-w-xs truncate py-3 pr-4 font-mono text-xs text-text-muted" title={a.origin ?? ''}>
                    {a.origin ?? '—'}
                  </td>
                  <td className="py-3 pr-4">
                    {a.produced_by ? (
                      <Link to={`/pipelines/${a.produced_by.pipeline_id}`} className="text-text hover:underline">
                        {a.produced_by.job_name}
                      </Link>
                    ) : a.layer === 'reference' ? (
                      <span className="text-text-muted" title="A table you maintain outside Refinaire">
                        You (external)
                      </span>
                    ) : (
                      <span className="text-text-muted" title="The pipeline was deleted; the table itself remains">
                        — (pipeline deleted)
                      </span>
                    )}
                  </td>
                  <td className="py-3 pr-4 text-text-muted">
                    {a.row_count === null ? '—' : a.row_count.toLocaleString()}
                  </td>
                  <td className="py-3 pr-4 text-text-muted">{formatDateTime(a.last_run_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
