import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { ConnectionGate } from '../components/ConnectionGate'
import { ErrorBanner, FormField } from '../components/FormField'
import { ApiError, useAuth } from '../lib/auth'
import { assets, bronze } from '../lib/endpoints'
import type { BronzePipelineIn, PipelinePreviewOut, ScheduleIn } from '../lib/types'

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

/** Builds a Bronze pipeline: pick Raw tables, say where the results go. Nothing else to configure —
 * what counts as "the same record" is every source column, decided by the table itself. */
export function BronzeBuilderPage() {
  const { token, activeOrgId } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [selected, setSelected] = useState<string[]>([])
  const [tableNames, setTableNames] = useState<Record<string, string>>({})
  const [jobName, setJobName] = useState('')
  const [catalog, setCatalog] = useState('')
  const [targetSchema, setTargetSchema] = useState('bronze')
  const [schedule, setSchedule] = useState<ScheduleIn>({
    frequency: 'daily',
    time: '03:00',
    day_of_week: 'MON',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  })
  const [runNow, setRunNow] = useState(false)
  const [preview, setPreview] = useState<PipelinePreviewOut | null>(null)
  const [busy, setBusy] = useState<'preview' | 'deploy' | null>(null)
  const [error, setError] = useState<ErrorState | null>(null)

  const rawQuery = useQuery({
    queryKey: ['assets', activeOrgId, 'raw'],
    queryFn: () => assets.list(token!, activeOrgId!, 'raw'),
    enabled: Boolean(token && activeOrgId),
  })

  // Any edit makes a previewed notebook stale: it has to be previewed again.
  const stale = () => setPreview(null)

  function toggle(id: string, table: string, rawCatalog: string) {
    stale()
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
    setTableNames((cur) => (cur[id] ? cur : { ...cur, [id]: table }))
    if (!catalog) setCatalog(rawCatalog)
  }

  function buildBody(): BronzePipelineIn {
    return {
      job_name: jobName,
      catalog,
      target_schema: targetSchema,
      inputs: selected.map((id) => ({ asset_id: id, table_name: tableNames[id] ?? '' })),
      schedule,
      cluster_id: null,
    }
  }

  async function handlePreview(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy('preview')
    try {
      setPreview(await bronze.preview(token!, activeOrgId!, buildBody()))
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
      const created = await bronze.deploy(token!, activeOrgId!, {
        ...buildBody(),
        notebook_sha256: preview.notebook_sha256,
        run_now: runNow,
      })
      await queryClient.invalidateQueries({ queryKey: ['pipelines', activeOrgId] })
      await queryClient.invalidateQueries({ queryKey: ['assets', activeOrgId] })
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
        <h1 className="mt-1 text-2xl font-semibold text-text">New Bronze pipeline</h1>
        <p className="mt-1 text-sm text-text-muted">
          Keeps one copy of each distinct record from your Raw tables. Two records are the same only if every
          column is identical, so a record whose values changed is kept as a new version. Bronze only ever adds
          rows — it never updates or deletes one.
        </p>
      </div>

      <form onSubmit={handlePreview} className="flex flex-col gap-6">
        {error && <ErrorBanner message={error.message} problems={error.problems} />}

        <section className="rounded-lg border border-border bg-surface p-5">
          <h2 className="font-medium text-text">1. Raw tables</h2>
          {rawQuery.isLoading && <p className="mt-3 text-sm text-text-muted">Loading tables…</p>}
          {rawQuery.data && rawQuery.data.length === 0 && (
            <p className="mt-3 text-sm text-text-muted">
              No Raw tables yet —{' '}
              <Link to="/pipelines/new" className="text-accent hover:underline">
                deploy a Raw pipeline first
              </Link>
              .
            </p>
          )}
          <ul className="mt-3 flex flex-col gap-2">
            {(rawQuery.data ?? []).map((a) => {
              const checked = selected.includes(a.id)
              return (
                <li key={a.id} className="flex flex-col gap-2 rounded-md border border-border p-3">
                  <label className="flex flex-wrap items-center gap-3 text-sm">
                    <input type="checkbox" checked={checked} onChange={() => toggle(a.id, a.table_name, a.catalog)} />
                    <span className="font-mono text-xs">{a.full_name}</span>
                    <span className="text-text-muted">{a.column_count} columns</span>
                    {a.row_count !== null && <span className="text-text-muted">{a.row_count.toLocaleString()} rows</span>}
                  </label>
                  {checked && (
                    <div className="pl-7">
                      <FormField
                        label="Bronze table name"
                        value={tableNames[a.id] ?? ''}
                        onChange={(e) => {
                          stale()
                          setTableNames((cur) => ({ ...cur, [a.id]: e.target.value }))
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
          <h2 className="font-medium text-text">2. Where it goes</h2>
          <FormField
            label="Job name"
            required
            value={jobName}
            onChange={(e) => {
              stale()
              setJobName(e.target.value)
            }}
          />
          <div className="grid grid-cols-2 gap-4">
            <FormField
              label="Catalog"
              required
              value={catalog}
              onChange={(e) => {
                stale()
                setCatalog(e.target.value)
              }}
            />
            <FormField
              label="Schema"
              required
              value={targetSchema}
              onChange={(e) => {
                stale()
                setTargetSchema(e.target.value)
              }}
            />
          </div>
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
                  stale()
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
                stale()
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
                    stale()
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
                stale()
                setSchedule({ ...schedule, timezone: e.target.value })
              }}
            />
          </div>
          <p className="text-xs text-text-muted">
            Schedule it to run after your Raw pipelines have finished loading.
          </p>
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
              Run it once right after deploying (this writes to the Bronze tables)
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
    </div>
  )
}
