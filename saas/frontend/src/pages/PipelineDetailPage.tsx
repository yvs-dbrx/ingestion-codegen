import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Badge } from '../components/Badge'
import { ErrorBanner } from '../components/FormField'
import { RunMetrics } from '../components/RunMetrics'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { ApiError, useAuth } from '../lib/auth'
import { pipelines, runs as runsApi } from '../lib/endpoints'
import { formatDateTime } from '../lib/format'

export function PipelineDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { token, activeOrgId, user } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showNotebook, setShowNotebook] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<{ message: string; problems?: string[] } | null>(null)

  const enabled = Boolean(token && activeOrgId && id)
  const pipelineQuery = useQuery({
    queryKey: ['pipeline', activeOrgId, id],
    queryFn: () => pipelines.get(token!, activeOrgId!, id!),
    enabled,
  })
  const runsQuery = useQuery({
    queryKey: ['runs', activeOrgId, id],
    queryFn: () => runsApi.list(token!, activeOrgId!, id!),
    enabled,
  })

  function onError(err: unknown) {
    setError(
      err instanceof ApiError
        ? { message: err.detail, problems: err.problems }
        : { message: 'Something went wrong — try again' },
    )
  }

  async function reloadRuns() {
    await queryClient.invalidateQueries({ queryKey: ['runs', activeOrgId, id] })
    await queryClient.invalidateQueries({ queryKey: ['pipeline', activeOrgId, id] })
    await queryClient.invalidateQueries({ queryKey: ['pipelines', activeOrgId] })
  }

  const runNow = useMutation({
    mutationFn: () => runsApi.runNow(token!, activeOrgId!, id!),
    onSuccess: reloadRuns,
    onError,
    onMutate: () => setError(null),
  })
  const refresh = useMutation({
    mutationFn: () => runsApi.refresh(token!, activeOrgId!, id!),
    onSuccess: reloadRuns,
    onError,
    onMutate: () => setError(null),
  })
  const remove = useMutation({
    mutationFn: () => pipelines.remove(token!, activeOrgId!, id!),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['pipelines', activeOrgId] })
      await queryClient.invalidateQueries({ queryKey: ['usage', activeOrgId] })
      navigate('/pipelines')
    },
    onError: (err) => {
      setConfirmDelete(false)
      onError(err)
    },
    onMutate: () => setError(null),
  })

  if (pipelineQuery.isLoading) return <p className="text-text-muted">Loading pipeline…</p>
  if (pipelineQuery.isError || !pipelineQuery.data) {
    return (
      <div className="mx-auto max-w-3xl">
        <p className="text-danger">Couldn't load this pipeline — it may have been deleted.</p>
        <Link to="/pipelines" className="mt-2 inline-block text-sm text-accent hover:underline">
          ← Back to pipelines
        </Link>
      </div>
    )
  }

  const p = pipelineQuery.data
  const role = user?.memberships.find((m) => m.org.id === activeOrgId)?.role
  const busy = runNow.isPending || refresh.isPending || remove.isPending

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <Link to="/pipelines" className="text-sm text-text-muted hover:text-text">
          ← Pipelines
        </Link>
        <div className="mt-1 flex flex-wrap items-center justify-between gap-3">
          <h1 className="flex items-center gap-3 text-2xl font-semibold text-text">
            {p.job_name}
            <Badge>{p.layer}</Badge>
          </h1>
          <div className="flex gap-2">
            <button
              onClick={() => runNow.mutate()}
              disabled={busy}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-60"
            >
              {runNow.isPending ? 'Starting…' : 'Run now'}
            </button>
            <button
              onClick={() => refresh.mutate()}
              disabled={busy}
              className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text hover:bg-border/60 disabled:opacity-60"
            >
              {refresh.isPending ? 'Refreshing…' : 'Refresh runs'}
            </button>
          </div>
        </div>
      </div>

      {error && <ErrorBanner message={error.message} problems={error.problems} />}

      <section className="grid grid-cols-2 gap-4 rounded-lg border border-border bg-surface p-5 text-sm sm:grid-cols-4">
        <Fact label="Target" value={`${p.catalog}.${p.target_schema}`} mono />
        <Fact
          label={p.layer === 'raw' ? 'Mode' : 'Behaviour'}
          value={
            p.layer === 'bronze'
              ? 'Insert-only, full re-read'
              : p.layer === 'silver'
                ? 'Current row per key, full re-read'
                : p.mode === 'multi'
                  ? 'Several files'
                  : 'Single file'
          }
        />
        <Fact
          label="Schedule"
          value={`${p.schedule_frequency ?? '—'}${p.schedule_time ? ` ${p.schedule_time}` : ''} ${p.schedule_timezone}`}
        />
        <Fact label="Created" value={formatDateTime(p.created_at)} />
        {p.databricks_job_url && (
          <div className="col-span-2 sm:col-span-4">
            <a
              href={p.databricks_job_url}
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              Open the job in Databricks ↗
            </a>
          </div>
        )}
      </section>

      {p.layer === 'bronze' && (
        <section className="rounded-lg border border-border bg-surface p-5">
          <h2 className="font-medium text-text">Distinct records from</h2>
          <ul className="mt-3 flex flex-col gap-2 text-sm">
            {(p.spec.inputs ?? []).map((i) => (
              <li key={i.bronze} className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs">{i.raw}</span>
                <span className="text-text-muted">→</span>
                <span className="font-mono text-xs">{i.bronze}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {p.layer === 'silver' && p.spec.source && (
        <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5">
          <div>
            <h2 className="font-medium text-text">Built from</h2>
            <p className="mt-1 text-sm text-text-muted">
              <span className="font-mono text-xs">{p.spec.source.bronze}</span> as system{' '}
              <span className="font-mono text-xs">{p.spec.source.system}</span>
            </p>
          </div>
          {(p.spec.entities ?? []).map((e) => (
            <div key={e.name} className="rounded-md border border-border p-3 text-sm">
              <p className="font-mono text-xs text-text">{e.target}</p>
              <p className="mt-1 text-xs text-text-muted">
                one row per <span className="font-mono">{e.business_key.join(', ')}</span> · bad records go to{' '}
                <span className="font-mono">{e.quarantine.split('.').pop()}</span>
              </p>
              <ul className="mt-2 flex flex-col gap-1">
                {e.attributes.map((a) => (
                  <li key={a.name} className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono text-text">{a.name}</span>
                    <span className="text-text-muted">{a.type}</span>
                    <span className="text-text-muted">← {a.from}</span>
                    {a.required && <Badge>required</Badge>}
                    {a.steps.length > 0 && (
                      <span className="text-text-muted">
                        ({a.steps.map((st) => (st.kind === 'null_if' ? `empty if ${st.values.map((v) => v || 'blank').join('/')}` : st.kind === 'cast' ? `to ${st.type}` : st.kind === 'parse_date' || st.kind === 'parse_timestamp' ? `${st.kind.replace('_', ' ')} ${st.format}` : st.kind === 'default' ? `default ${st.value}` : st.kind)).join(' → ')})
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              {e.master_key && (
                <p className="mt-2 text-xs text-text-muted">
                  master key <span className="font-mono text-text">{e.master_key.column}</span> looked up in{' '}
                  <span className="font-mono">{e.master_key.crosswalk}</span>
                  {e.master_key.entity_type && (
                    <>
                      {' '}
                      (entity type <span className="font-mono">{e.master_key.entity_type}</span>)
                    </>
                  )}
                  ; unmatched keys get{' '}
                  <span className="font-mono">{p.spec.unknown_master_key}</span>
                </p>
              )}
            </div>
          ))}
        </section>
      )}

      {p.layer === 'raw' && (
      <section className="rounded-lg border border-border bg-surface p-5">
        <h2 className="font-medium text-text">Tables</h2>
        <ul className="mt-3 flex flex-col gap-2 text-sm">
          {p.tables.map((t) => (
            <li key={t.table_name} className="flex flex-wrap items-center gap-2">
              <span className="font-mono">{t.table_name}</span>
              <span className="text-text-muted">from</span>
              <span className="font-mono text-xs text-text-muted">{t.source_path}</span>
              <span className="text-text-muted">v{t.source_version}</span>
              {t.is_stale && <Badge tone="danger">newer schema available</Badge>}
            </li>
          ))}
        </ul>
        {p.tables.some((t) => t.is_stale) && (
          <p className="mt-3 text-sm text-text-muted">
            The source's schema changed after this pipeline was deployed. Build a new pipeline from the
            current version to pick up the change.
          </p>
        )}
      </section>
      )}

      <section className="rounded-lg border border-border bg-surface p-5">
        <h2 className="font-medium text-text">Runs</h2>
        {runsQuery.isLoading && <p className="mt-3 text-sm text-text-muted">Loading runs…</p>}
        {runsQuery.data && runsQuery.data.length === 0 && (
          <p className="mt-3 text-sm text-text-muted">
            No runs recorded yet. Scheduled runs appear after you click "Refresh runs".
          </p>
        )}
        {runsQuery.data && runsQuery.data.length > 0 && (
          <table className="mt-3 w-full text-left text-sm">
            <thead className="text-xs text-text-muted">
              <tr>
                <th className="pb-2 pr-4 font-medium">Status</th>
                <th className="pb-2 pr-4 font-medium">Trigger</th>
                <th className="pb-2 pr-4 font-medium">Started</th>
                <th className="pb-2 pr-4 font-medium">Finished</th>
                <th className="pb-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {runsQuery.data.map((r) => (
                <tr key={r.id} className="border-t border-border align-top">
                  <td className="py-2 pr-4">
                    <RunStatusBadge status={r.status} />
                    {r.error_message && <p className="mt-1 max-w-sm text-xs text-danger">{r.error_message}</p>}
                    <RunMetrics metrics={r.metrics} />
                  </td>
                  <td className="py-2 pr-4 text-text-muted">{r.triggered_by}</td>
                  <td className="py-2 pr-4 text-text-muted">{formatDateTime(r.started_at ?? r.created_at)}</td>
                  <td className="py-2 pr-4 text-text-muted">{formatDateTime(r.finished_at)}</td>
                  <td className="py-2">
                    {r.databricks_run_url && (
                      <a href={r.databricks_run_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                        Databricks ↗
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="rounded-lg border border-border bg-surface p-5">
        <button onClick={() => setShowNotebook((s) => !s)} className="font-medium text-text">
          {showNotebook ? '▾' : '▸'} Deployed notebook
        </button>
        {showNotebook && (
          <pre className="mt-3 max-h-96 overflow-auto rounded-md border border-border bg-bg p-3 font-mono text-xs">
            {p.notebook_source ?? 'Not stored.'}
          </pre>
        )}
      </section>

      <section className="rounded-lg border border-danger/30 p-5">
        <h2 className="font-medium text-danger">Delete pipeline</h2>
        <p className="mt-1 text-sm text-text-muted">
          Removes the Databricks job and its notebook, and frees a pipeline slot. The tables it already wrote are
          not deleted.
          {role !== 'admin' && ' Only an admin or the person who created it can do this.'}
        </p>
        {confirmDelete ? (
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => remove.mutate()}
              disabled={busy}
              className="rounded-md bg-danger px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {remove.isPending ? 'Deleting…' : 'Yes, delete it'}
            </button>
            <button
              onClick={() => setConfirmDelete(false)}
              className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text hover:bg-border/60"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setConfirmDelete(true)}
            className="mt-3 rounded-md border border-danger/50 px-4 py-2 text-sm font-medium text-danger hover:bg-danger/10"
          >
            Delete…
          </button>
        )}
      </section>
    </div>
  )
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p className="text-xs text-text-muted">{label}</p>
      <p className={`mt-0.5 text-text ${mono ? 'font-mono text-xs' : ''}`}>{value}</p>
    </div>
  )
}
