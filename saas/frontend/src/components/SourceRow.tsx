import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from './Badge'
import { useAuth } from '../lib/auth'
import { sources as sourcesApi } from '../lib/endpoints'
import type { SourceOut } from '../lib/types'

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

export function SourceRow({ source }: { source: SourceOut }) {
  const [expanded, setExpanded] = useState(false)
  const { token, activeOrgId } = useAuth()

  const historyQuery = useQuery({
    queryKey: ['source-history', source.id],
    queryFn: () => sourcesApi.history(token!, activeOrgId!, source.id),
    enabled: expanded,
  })

  return (
    <>
      <tr className="border-b border-border last:border-0">
        <td className="py-2 pr-4">
          <button
            onClick={() => setExpanded((e) => !e)}
            className="text-text-muted hover:text-text"
            aria-label={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? '▾' : '▸'}
          </button>
        </td>
        <td className="py-2 pr-4">
          <Badge tone="neutral">{source.source_type}</Badge>
        </td>
        <td className="max-w-xs truncate py-2 pr-4 font-mono text-xs" title={source.source_path}>
          {source.source_path}
        </td>
        <td className="py-2 pr-4 text-text-muted">v{source.version}</td>
        <td className="py-2 pr-4 text-text-muted">{source.columns.length}</td>
        <td className="py-2 pr-4 text-text-muted">{formatDate(source.last_verified_at)}</td>
        <td className="py-2">
          {source.record_is_active ? (
            <Badge tone="success">Active</Badge>
          ) : (
            <Badge tone="neutral">Superseded</Badge>
          )}
        </td>
      </tr>

      {expanded && (
        <tr className="border-b border-border last:border-0 bg-bg/60">
          <td />
          <td colSpan={6} className="py-3 pr-4">
            {source.schema_definition_path && (
              <p className="mb-2 text-xs text-text-muted">
                Schema definition: <span className="font-mono">{source.schema_definition_path}</span>
              </p>
            )}

            <table className="w-full text-left text-xs">
              <thead className="text-text-muted">
                <tr>
                  <th className="pb-1 pr-4 font-medium">Column</th>
                  <th className="pb-1 pr-4 font-medium">Type</th>
                  <th className="pb-1 pr-4 font-medium">Nullable</th>
                  <th className="pb-1 font-medium">Struct path</th>
                </tr>
              </thead>
              <tbody>
                {source.columns.map((c) => (
                  <tr key={c.name} className="border-t border-border/60">
                    <td className="py-1 pr-4 font-mono">{c.name}</td>
                    <td className="py-1 pr-4 font-mono">{c.sql_type}</td>
                    <td className="py-1 pr-4">{c.nullable ? 'yes' : 'no'}</td>
                    <td className="py-1 font-mono">{c.struct_path?.join(' → ') ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="mt-3">
              <p className="mb-1 text-xs font-medium text-text-muted">Version history</p>
              {historyQuery.isLoading && <p className="text-xs text-text-muted">Loading…</p>}
              {historyQuery.data && (
                <ul className="flex flex-col gap-1">
                  {historyQuery.data.map((v) => (
                    <li key={v.id} className="flex items-center gap-2 text-xs text-text-muted">
                      <span className="font-mono">v{v.version}</span>
                      <span>{formatDate(v.record_start_ts)}</span>
                      {v.record_end_ts && <span>→ {formatDate(v.record_end_ts)}</span>}
                      {v.record_is_active && <Badge tone="success">current</Badge>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
