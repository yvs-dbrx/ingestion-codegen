import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { sources as sourcesApi } from '../lib/endpoints'

export function DriftPanel() {
  const { token, activeOrgId } = useAuth()
  const query = useQuery({
    queryKey: ['drift', activeOrgId],
    queryFn: () => sourcesApi.drift(token!, activeOrgId!),
    enabled: Boolean(token && activeOrgId),
  })

  if (!query.data || query.data.length === 0) return null

  return (
    <div className="rounded-lg border border-danger/30 bg-danger/5 p-4">
      <h3 className="font-medium text-danger">
        {query.data.length} pipeline{query.data.length === 1 ? '' : 's'} built from an outdated schema
      </h3>
      <ul className="mt-2 flex flex-col gap-1.5">
        {query.data.map((d) => (
          <li key={`${d.pipeline_id}-${d.table_name}`} className="text-sm text-text">
            <Link to="/pipelines" className="font-medium hover:underline">
              {d.job_name}
            </Link>
            <span className="text-text-muted">
              {' '}
              · table <span className="font-mono">{d.table_name}</span> was built from v
              {d.built_from_version} of <span className="font-mono">{d.source_path}</span>; the current
              version is v{d.current_version}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
