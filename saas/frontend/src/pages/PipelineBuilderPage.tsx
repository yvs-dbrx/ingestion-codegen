import { useMemo, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { Badge } from '../components/Badge'
import { ConnectionGate } from '../components/ConnectionGate'
import { ErrorBanner, FormField } from '../components/FormField'
import { ApiError, useAuth } from '../lib/auth'
import { pipelines, sources as sourcesApi } from '../lib/endpoints'
import { suggestTableName } from '../lib/format'
import type { PipelineIn, PipelinePreviewOut, ScheduleIn } from '../lib/types'

const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']

interface ErrorState {
  message: string
  problems?: string[]
}

function toErrorState(err: unknown): ErrorState {
  return err instanceof ApiError
    ? { message: err.detail, problems: err.problems }
    : { message: 'Something went wrong — try again' }
}

export function PipelineBuilderPage() {
  const { token, activeOrgId } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [selected, setSelected] = useState<string[]>([])
  const [tableNames, setTableNames] = useState<Record<string, string>>({})
  const [jobName, setJobName] = useState('')
  const [catalog, setCatalog] = useState('')
  const [targetSchema, setTargetSchema] = useState('')
  const [clusterId, setClusterId] = useState('')
  const [schedule, setSchedule] = useState<ScheduleIn>({
    frequency: 'daily',
    time: '02:00',
    day_of_week: 'MON',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  })
  const [runNow, setRunNow] = useState(false)

  const [preview, setPreview] = useState<PipelinePreviewOut | null>(null)
  const [busy, setBusy] = useState<'preview' | 'deploy' | null>(null)
  const [error, setError] = useState<ErrorState | null>(null)

  const sourcesQuery = useQuery({
    queryKey: ['sources', activeOrgId, 'all'],
    queryFn: () => sourcesApi.list(token!, activeOrgId!),
    enabled: Boolean(token && activeOrgId),
  })

  const chosen = useMemo(
    () => (sourcesQuery.data ?? []).filter((s) => selected.includes(s.id)),
    [sourcesQuery.data, selected],
  )
  const chosenType = chosen[0]?.source_type

  // Any edit makes a previously previewed notebook stale — it has to be previewed again,
  // otherwise the notebook on screen wouldn't be the one the request describes.
  function invalidatePreview() {
    setPreview(null)
  }

  function toggle(id: string, path: string) {
    invalidatePreview()
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
    setTableNames((cur) => (cur[id] ? cur : { ...cur, [id]: suggestTableName(path) }))
  }

  function buildBody(): PipelineIn {
    return {
      job_name: jobName,
      mode: selected.length > 1 ? 'multi' : 'single',
      catalog,
      target_schema: targetSchema,
      tables: selected.map((id) => ({ source_id: id, table_name: tableNames[id] ?? '' })),
      schedule,
      cluster_id: clusterId || null,
    }
  }

  async function handlePreview(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy('preview')
    try {
      setPreview(await pipelines.preview(token!, activeOrgId!, buildBody()))
    } catch (err) {
      setPreview(null)
      setError(toErrorState(err))
    } finally {
      setBusy(null)
    }
  }

  async function handleDeploy() {
    if (!preview) return
    setError(null)
    setBusy('deploy')
    try {
      const created = await pipelines.deploy(token!, activeOrgId!, {
        ...buildBody(),
        notebook_sha256: preview.notebook_sha256,
        run_now: runNow,
      })
      await queryClient.invalidateQueries({ queryKey: ['pipelines', activeOrgId] })
      await queryClient.invalidateQueries({ queryKey: ['usage', activeOrgId] })
      navigate(`/pipelines/${created.id}`)
    } catch (err) {
      setError(toErrorState(err))
    } finally {
      setBusy(null)
    }
  }

  const canPreview = selected.length > 0 && jobName && catalog && targetSchema

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <Link to="/pipelines" className="text-sm text-text-muted hover:text-text">
          ← Pipelines
        </Link>
        <h1 className="mt-1 text-2xl font-semibold text-text">New Raw pipeline</h1>
      </div>

      <>
        <form onSubmit={handlePreview} className="flex flex-col gap-6">
          {error && <ErrorBanner message={error.message} problems={error.problems} />}

          <section className="rounded-lg border border-border bg-surface p-5">
            <h2 className="font-medium text-text">1. Sources</h2>
            <p className="mt-1 text-sm text-text-muted">
              Pick one source, or several of the same type from the same folder to load them in one job.
            </p>
            {sourcesQuery.isLoading && <p className="mt-3 text-sm text-text-muted">Loading sources…</p>}
            {sourcesQuery.data && sourcesQuery.data.length === 0 && (
              <p className="mt-3 text-sm text-text-muted">
                No sources yet —{' '}
                <Link to="/sources" className="text-accent hover:underline">
                  discover one first
                </Link>
                .
              </p>
            )}
            <ul className="mt-3 flex flex-col gap-2">
              {(sourcesQuery.data ?? []).map((s) => {
                const disabled = chosenType !== undefined && s.source_type !== chosenType
                const checked = selected.includes(s.id)
                return (
                  <li
                    key={s.id}
                    className={`flex flex-col gap-2 rounded-md border border-border p-3 ${disabled ? 'opacity-50' : ''}`}
                  >
                    <label className="flex items-center gap-3 text-sm">
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => toggle(s.id, s.source_path)}
                      />
                      <Badge>{s.source_type}</Badge>
                      <span className="truncate font-mono text-xs">{s.source_path}</span>
                      <span className="text-text-muted">v{s.version}</span>
                    </label>
                    {checked && (
                      <div className="pl-7">
                        <FormField
                          label="Target table name"
                          value={tableNames[s.id] ?? ''}
                          onChange={(e) => {
                            invalidatePreview()
                            setTableNames((cur) => ({ ...cur, [s.id]: e.target.value }))
                          }}
                        />
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>

          <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5">
            <h2 className="font-medium text-text">2. Destination and job</h2>
            <FormField
              label="Job name"
              required
              value={jobName}
              onChange={(e) => {
                invalidatePreview()
                setJobName(e.target.value)
              }}
            />
            <div className="grid grid-cols-2 gap-4">
              <FormField
                label="Catalog"
                required
                value={catalog}
                onChange={(e) => {
                  invalidatePreview()
                  setCatalog(e.target.value)
                }}
              />
              <FormField
                label="Schema"
                required
                value={targetSchema}
                onChange={(e) => {
                  invalidatePreview()
                  setTargetSchema(e.target.value)
                }}
              />
            </div>
            <FormField
              label={
                chosenType === 'cobol'
                  ? 'Cluster ID (required for COBOL)'
                  : 'Cluster ID (optional — leave empty for serverless)'
              }
              placeholder="0101-000000-abc123"
              value={clusterId}
              onChange={(e) => {
                invalidatePreview()
                setClusterId(e.target.value)
              }}
            />
          </section>

          <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5">
            <h2 className="font-medium text-text">3. Schedule</h2>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium text-text">Frequency</span>
                <select
                  className="rounded-md border border-border bg-surface px-3 py-2"
                  value={schedule.frequency}
                  onChange={(e) => {
                    invalidatePreview()
                    setSchedule({ ...schedule, frequency: e.target.value as ScheduleIn['frequency'] })
                  }}
                >
                  <option value="hourly">Hourly</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                </select>
              </label>
              <FormField
                label="Time (24h)"
                type="time"
                value={schedule.time}
                onChange={(e) => {
                  invalidatePreview()
                  setSchedule({ ...schedule, time: e.target.value })
                }}
              />
              {schedule.frequency === 'weekly' && (
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-text">Day</span>
                  <select
                    className="rounded-md border border-border bg-surface px-3 py-2"
                    value={schedule.day_of_week}
                    onChange={(e) => {
                      invalidatePreview()
                      setSchedule({ ...schedule, day_of_week: e.target.value })
                    }}
                  >
                    {WEEKDAYS.map((d) => (
                      <option key={d}>{d}</option>
                    ))}
                  </select>
                </label>
              )}
              <FormField
                label="Timezone"
                value={schedule.timezone}
                onChange={(e) => {
                  invalidatePreview()
                  setSchedule({ ...schedule, timezone: e.target.value })
                }}
              />
            </div>
          </section>

          <button
            type="submit"
            disabled={!canPreview || busy !== null}
            className="self-start rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-60"
          >
            {busy === 'preview' ? 'Generating…' : 'Preview notebook'}
          </button>
        </form>

        {preview && (
          <section className="flex flex-col gap-4 rounded-lg border border-accent/60 bg-surface p-5">
            <div>
              <h2 className="font-medium text-text">4. Review and deploy</h2>
              <p className="mt-1 text-sm text-text-muted">
                This exact notebook will be uploaded to <span className="font-mono">{preview.workspace_path}</span> and
                scheduled with cron <span className="font-mono">{preview.cron}</span>. Nothing has been created yet.
              </p>
            </div>
            <pre className="max-h-96 overflow-auto rounded-md border border-border bg-bg p-3 font-mono text-xs">
              {preview.notebook}
            </pre>
            <ConnectionGate>
              <label className="flex items-center gap-2 text-sm text-text">
                <input type="checkbox" checked={runNow} onChange={(e) => setRunNow(e.target.checked)} />
                Run it once right after deploying (this writes to the target table)
              </label>
              <button
                onClick={handleDeploy}
                disabled={busy !== null}
                className="self-start rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-60"
              >
                {busy === 'deploy' ? 'Deploying…' : 'Deploy pipeline'}
              </button>
            </ConnectionGate>
          </section>
        )}
      </>
    </div>
  )
}
