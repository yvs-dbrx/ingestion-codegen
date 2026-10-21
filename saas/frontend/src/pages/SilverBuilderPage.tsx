import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { ConnectionGate } from '../components/ConnectionGate'
import { ErrorBanner, FormField } from '../components/FormField'
import { SilverEntityEditor } from '../components/SilverEntityEditor'
import { ApiError, useAuth } from '../lib/auth'
import { assets, silver } from '../lib/endpoints'
import { draftGaps, newEntity, toEntityBody, type EntityDraft } from '../lib/silverDraft'
import type { PipelinePreviewOut, ScheduleIn, SilverPipelineIn } from '../lib/types'

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

/** Designs Silver tables: pick a Bronze table, then for each Silver table choose its columns, how each is
 * cleaned (a short list of fixed steps), its business key, and optionally a master key from a crosswalk. */
export function SilverBuilderPage() {
  const { token, activeOrgId } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [sourceId, setSourceId] = useState('')
  const [jobName, setJobName] = useState('')
  const [catalog, setCatalog] = useState('')
  const [targetSchema, setTargetSchema] = useState('silver')
  const [sourceSystem, setSourceSystem] = useState('')
  const [unknownKey, setUnknownKey] = useState('UNKNOWN')
  const [entities, setEntities] = useState<EntityDraft[]>([newEntity()])
  const [schedule, setSchedule] = useState<ScheduleIn>({
    frequency: 'daily',
    time: '04:00',
    day_of_week: 'MON',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  })
  const [refTable, setRefTable] = useState({ catalog: '', schema_name: '', table_name: '' })
  const [runNow, setRunNow] = useState(false)
  const [preview, setPreview] = useState<PipelinePreviewOut | null>(null)
  const [busy, setBusy] = useState<'preview' | 'deploy' | 'register' | null>(null)
  const [error, setError] = useState<ErrorState | null>(null)

  const enabled = Boolean(token && activeOrgId)
  const bronzeQuery = useQuery({
    queryKey: ['assets', activeOrgId, 'bronze'],
    queryFn: () => assets.list(token!, activeOrgId!, 'bronze'),
    enabled,
  })
  const referenceQuery = useQuery({
    queryKey: ['assets', activeOrgId, 'reference'],
    queryFn: () => assets.list(token!, activeOrgId!, 'reference'),
    enabled,
  })
  const sourceQuery = useQuery({
    queryKey: ['asset', activeOrgId, sourceId],
    queryFn: () => assets.get(token!, activeOrgId!, sourceId),
    enabled: enabled && Boolean(sourceId),
  })
  const sourceColumns = sourceQuery.data?.columns ?? []

  // Any edit makes a previewed notebook stale: it has to be previewed again.
  const stale = () => setPreview(null)

  function chooseSource(id: string) {
    stale()
    setSourceId(id)
    const picked = (bronzeQuery.data ?? []).find((a) => a.id === id)
    if (picked && !catalog) setCatalog(picked.catalog)
    // the old columns no longer exist in the new source
    setEntities((cur) => cur.map((e) => ({ ...e, attrs: [] })))
  }

  function buildBody(): SilverPipelineIn {
    return {
      job_name: jobName,
      catalog,
      target_schema: targetSchema,
      source_asset_id: sourceId,
      source_system: sourceSystem,
      unknown_master_key: unknownKey,
      entities: entities.map(toEntityBody),
      schedule,
      cluster_id: null,
    }
  }

  async function handleRegister(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy('register')
    try {
      await assets.registerReference(token!, activeOrgId!, refTable)
      await queryClient.invalidateQueries({ queryKey: ['assets', activeOrgId] })
      setRefTable({ catalog: refTable.catalog, schema_name: refTable.schema_name, table_name: '' })
    } catch (err) {
      setError(toErrorState(err))
    } finally {
      setBusy(null)
    }
  }

  async function handlePreview(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy('preview')
    try {
      setPreview(await silver.preview(token!, activeOrgId!, buildBody()))
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
      const created = await silver.deploy(token!, activeOrgId!, { ...buildBody(), notebook_sha256: preview.notebook_sha256, run_now: runNow })
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

  const gaps = draftGaps(entities)
  const missing = [
    ...(sourceId ? [] : ['choose a Bronze table']),
    ...(jobName && catalog && targetSchema && sourceSystem ? [] : ['fill in the job name, catalog, schema and source system']),
    ...gaps,
  ]
  const references = referenceQuery.data ?? []

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <Link to="/pipelines" className="text-sm text-text-muted hover:text-text">
          ← Pipelines
        </Link>
        <h1 className="mt-1 text-2xl font-semibold text-text">New Silver pipeline</h1>
        <p className="mt-1 text-sm text-text-muted">
          Turns a Bronze table into clean, typed tables with one current row per business key. Of a key's versions
          the one seen most recently wins. Records that can't be converted, or miss a required value, are set aside
          in a quarantine table instead of being dropped.
        </p>
      </div>

      {error && <ErrorBanner message={error.message} problems={error.problems} />}

      <section className="rounded-lg border border-border bg-surface p-5">
        <h2 className="font-medium text-text">1. Bronze table to read</h2>
        {bronzeQuery.data && bronzeQuery.data.length === 0 && (
          <p className="mt-3 text-sm text-text-muted">
            No Bronze tables yet —{' '}
            <Link to="/pipelines/new/bronze" className="text-accent hover:underline">
              deploy a Bronze pipeline first
            </Link>
            , and run it once so it has data.
          </p>
        )}
        <select
          className="mt-3 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
          value={sourceId}
          onChange={(e) => chooseSource(e.target.value)}
          aria-label="Bronze table"
        >
          <option value="">Choose a Bronze table…</option>
          {(bronzeQuery.data ?? []).map((a) => (
            <option key={a.id} value={a.id}>
              {a.full_name} ({a.column_count} columns)
            </option>
          ))}
        </select>
        {sourceColumns.length > 0 && (
          <p className="mt-3 text-xs text-text-muted">
            Columns: {sourceColumns.map((c) => `${c.name} ${c.sql_type}`).join(' · ')}
          </p>
        )}
      </section>

      <section className="rounded-lg border border-border bg-surface p-5">
        <h2 className="font-medium text-text">2. Crosswalk tables (optional)</h2>
        <p className="mt-1 text-sm text-text-muted">
          A table you already have in Databricks that maps each source key to a master key — typically the result of
          your identity resolution. Only its column names are read, never its rows.
        </p>
        {references.length > 0 && (
          <ul className="mt-3 flex flex-col gap-1 text-sm">
            {references.map((r) => (
              <li key={r.id} className="font-mono text-xs">
                {r.full_name} <span className="font-sans text-text-muted">· {r.column_count} columns</span>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={handleRegister} className="mt-3 flex flex-wrap items-end gap-3">
          <div className="w-36">
            <FormField label="Catalog" value={refTable.catalog} onChange={(e) => setRefTable({ ...refTable, catalog: e.target.value })} />
          </div>
          <div className="w-36">
            <FormField label="Schema" value={refTable.schema_name} onChange={(e) => setRefTable({ ...refTable, schema_name: e.target.value })} />
          </div>
          <div className="w-44">
            <FormField label="Table" value={refTable.table_name} onChange={(e) => setRefTable({ ...refTable, table_name: e.target.value })} />
          </div>
          <button
            type="submit"
            disabled={busy !== null || !refTable.catalog || !refTable.schema_name || !refTable.table_name}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text hover:bg-border/60 disabled:opacity-60"
          >
            {busy === 'register' ? 'Registering…' : 'Register crosswalk'}
          </button>
        </form>
      </section>

      <form onSubmit={handlePreview} className="flex flex-col gap-6">
        <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5">
          <h2 className="font-medium text-text">3. Where it goes</h2>
          <FormField label="Job name" required value={jobName} onChange={(e) => { stale(); setJobName(e.target.value) }} />
          <div className="grid grid-cols-2 gap-4">
            <FormField label="Catalog" required value={catalog} onChange={(e) => { stale(); setCatalog(e.target.value) }} />
            <FormField label="Schema" required value={targetSchema} onChange={(e) => { stale(); setTargetSchema(e.target.value) }} />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <FormField
                label="Source system name"
                required
                placeholder="e.g. CRM"
                value={sourceSystem}
                onChange={(e) => { stale(); setSourceSystem(e.target.value) }}
              />
              <p className="mt-1 text-xs text-text-muted">Matched against the crosswalk's source-system column.</p>
            </div>
            <div>
              <FormField label="Master key for unmatched keys" value={unknownKey} onChange={(e) => { stale(); setUnknownKey(e.target.value) }} />
              <p className="mt-1 text-xs text-text-muted">The "unknown member" those rows are given.</p>
            </div>
          </div>
        </section>

        <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5">
          <div>
            <h2 className="font-medium text-text">4. Silver tables</h2>
            <p className="mt-1 text-sm text-text-muted">
              Each table holds one current row per key. To normalise, add a second table for a repeated group (for
              example product categories) and read it from the same Bronze table.
            </p>
          </div>
          {entities.map((entity) => (
            <SilverEntityEditor
              key={entity.id}
              entity={entity}
              sourceColumns={sourceColumns}
              crosswalks={references}
              canRemove={entities.length > 1}
              onChange={(next) => { stale(); setEntities((cur) => cur.map((x) => (x.id === next.id ? next : x))) }}
              onRemove={() => { stale(); setEntities((cur) => cur.filter((x) => x.id !== entity.id)) }}
            />
          ))}
          <button
            type="button"
            onClick={() => { stale(); setEntities((cur) => [...cur, newEntity()]) }}
            className="self-start rounded-md border border-border px-3 py-1.5 text-sm text-text hover:bg-border/60"
          >
            + Add another Silver table
          </button>
        </section>

        <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5">
          <h2 className="font-medium text-text">5. Schedule</h2>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-text">Frequency</span>
              <select
                className="rounded-md border border-border bg-surface px-3 py-2"
                value={schedule.frequency}
                onChange={(e) => { stale(); setSchedule({ ...schedule, frequency: e.target.value as ScheduleIn['frequency'] }) }}
              >
                <option value="hourly">Hourly</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </select>
            </label>
            <FormField label="Time (24h)" type="time" value={schedule.time} onChange={(e) => { stale(); setSchedule({ ...schedule, time: e.target.value }) }} />
            {schedule.frequency === 'weekly' && (
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium text-text">Day</span>
                <select
                  className="rounded-md border border-border bg-surface px-3 py-2"
                  value={schedule.day_of_week}
                  onChange={(e) => { stale(); setSchedule({ ...schedule, day_of_week: e.target.value }) }}
                >
                  {WEEKDAYS.map((d) => (
                    <option key={d}>{d}</option>
                  ))}
                </select>
              </label>
            )}
            <FormField label="Timezone" value={schedule.timezone} onChange={(e) => { stale(); setSchedule({ ...schedule, timezone: e.target.value }) }} />
          </div>
          <p className="text-xs text-text-muted">Schedule it to run after your Bronze pipeline has finished.</p>
        </section>

        {missing.length > 0 && (
          <p className="text-sm text-text-muted">Before you can preview: {missing.join('; ')}.</p>
        )}
        <button
          type="submit"
          disabled={missing.length > 0 || busy !== null}
          className="self-start rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-60"
        >
          {busy === 'preview' ? 'Generating…' : 'Preview notebook'}
        </button>
      </form>

      {preview && (
        <section className="flex flex-col gap-4 rounded-lg border border-accent/60 bg-surface p-5">
          <div>
            <h2 className="font-medium text-text">6. Review and deploy</h2>
            <p className="mt-1 text-sm text-text-muted">
              This exact notebook will be uploaded to <span className="font-mono">{preview.workspace_path}</span> and
              scheduled with cron <span className="font-mono">{preview.cron}</span>. Nothing has been created yet.
            </p>
          </div>
          <pre className="max-h-96 overflow-auto rounded-md border border-border bg-bg p-3 font-mono text-xs">{preview.notebook}</pre>
          <ConnectionGate>
            <label className="flex items-center gap-2 text-sm text-text">
              <input type="checkbox" checked={runNow} onChange={(e) => setRunNow(e.target.checked)} />
              Run it once right after deploying (this writes to the Silver tables)
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
