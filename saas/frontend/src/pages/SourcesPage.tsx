import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '../components/Badge'
import { ConnectionGate } from '../components/ConnectionGate'
import { DiscoverForm } from '../components/DiscoverForm'
import { DriftPanel } from '../components/DriftPanel'
import { SourceRow } from '../components/SourceRow'
import { useAuth } from '../lib/auth'
import { sources as sourcesApi } from '../lib/endpoints'
import type { DiscoveredSourceOut, SourceType } from '../lib/types'

const FILTERS: { value: SourceType | 'all'; label: string }[] = [
  { value: 'all', label: 'All types' },
  { value: 'csv', label: 'CSV' },
  { value: 'json', label: 'JSON' },
  { value: 'xml', label: 'XML' },
  { value: 'cobol', label: 'COBOL' },
]

function statusTone(status: DiscoveredSourceOut['status']) {
  if (status === 'created') return 'success' as const
  if (status === 'drifted') return 'gold' as const
  return 'neutral' as const
}

export function SourcesPage() {
  const { token, activeOrgId } = useAuth()
  const [filter, setFilter] = useState<SourceType | 'all'>('all')
  const [showDiscover, setShowDiscover] = useState(false)
  const [lastResults, setLastResults] = useState<DiscoveredSourceOut[] | null>(null)

  const sourcesQuery = useQuery({
    queryKey: ['sources', activeOrgId, filter],
    queryFn: () => sourcesApi.list(token!, activeOrgId!, filter === 'all' ? undefined : filter),
    enabled: Boolean(token && activeOrgId),
  })

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-text">Sources</h1>
        <button
          onClick={() => setShowDiscover((s) => !s)}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover"
        >
          {showDiscover ? 'Close' : 'Discover a source'}
        </button>
      </div>

      <DriftPanel />

      {showDiscover && (
        <ConnectionGate>
          <DiscoverForm
            onDiscovered={(results) => {
              setLastResults(results)
              setShowDiscover(false)
            }}
          />
        </ConnectionGate>
      )}

      {lastResults && (
        <div className="rounded-lg border border-border bg-surface p-4">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-text">Discovery results</h3>
            <button onClick={() => setLastResults(null)} className="text-sm text-text-muted hover:text-text">
              Dismiss
            </button>
          </div>
          <ul className="mt-2 flex flex-col gap-1.5">
            {lastResults.map((r) => (
              <li key={r.source.id} className="flex items-center gap-2 text-sm">
                <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                <span className="font-mono text-text-muted">{r.source.source_path}</span>
                <span className="text-text-muted">→ suggested table "{r.suggested_table_name}"</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-2">
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
        {sourcesQuery.isLoading && <p className="p-5 text-text-muted">Loading sources…</p>}
        {sourcesQuery.isError && <p className="p-5 text-danger">Couldn't load sources — try refreshing.</p>}
        {sourcesQuery.data && sourcesQuery.data.length === 0 && (
          <p className="p-5 text-text-muted">
            No sources yet. Click "Discover a source" to read a schema from your Databricks workspace.
          </p>
        )}
        {sourcesQuery.data && sourcesQuery.data.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs text-text-muted">
              <tr>
                <th className="py-2 pr-4 pl-4 font-medium" />
                <th className="py-2 pr-4 font-medium">Type</th>
                <th className="py-2 pr-4 font-medium">Path</th>
                <th className="py-2 pr-4 font-medium">Version</th>
                <th className="py-2 pr-4 font-medium">Columns</th>
                <th className="py-2 pr-4 font-medium">Last verified</th>
                <th className="py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="px-4">
              {sourcesQuery.data.map((s) => (
                <SourceRow key={s.id} source={s} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
