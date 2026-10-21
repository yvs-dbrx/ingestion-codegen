import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../lib/auth'
import { assets } from '../lib/endpoints'
import { defaultAttrName, newId, type AttrDraft, type EntityDraft } from '../lib/silverDraft'
import type { AssetColumn, AssetOut } from '../lib/types'
import { FormField } from './FormField'
import { StepEditor } from './StepEditor'

const select = 'rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text outline-none focus:border-accent'

interface Props {
  entity: EntityDraft
  sourceColumns: AssetColumn[]
  crosswalks: AssetOut[]
  onChange: (next: EntityDraft) => void
  onRemove: () => void
  canRemove: boolean
}

/** One Silver table: its columns (each taken from a Bronze column, cleaned by steps), its business key,
 * and optionally the master key looked up in a crosswalk. */
export function SilverEntityEditor({ entity, sourceColumns, crosswalks, onChange, onRemove, canRemove }: Props) {
  const { token, activeOrgId } = useAuth()
  const set = (patch: Partial<EntityDraft>) => onChange({ ...entity, ...patch })
  const setAttr = (id: string, patch: Partial<AttrDraft>) =>
    set({ attrs: entity.attrs.map((a) => (a.id === id ? { ...a, ...patch } : a)) })

  const unused = sourceColumns.filter((c) => !entity.attrs.some((a) => a.source === c.name))
  const addColumn = (source: string) =>
    set({ attrs: [...entity.attrs, { id: newId(), name: defaultAttrName(source), source, steps: [], required: false, isKey: false }] })

  const master = entity.master
  const setMaster = (patch: Partial<typeof master>) => set({ master: { ...master, ...patch } })
  const crosswalkQuery = useQuery({
    queryKey: ['asset', activeOrgId, master.crosswalkId],
    queryFn: () => assets.get(token!, activeOrgId!, master.crosswalkId),
    enabled: Boolean(token && activeOrgId && master.crosswalkId),
  })
  const xcols = crosswalkQuery.data?.columns ?? []

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1">
          <FormField label="Silver table name" value={entity.tableName} onChange={(e) => set({ tableName: e.target.value })} />
        </div>
        {canRemove && (
          <button type="button" onClick={onRemove} className="rounded-md border border-danger/40 px-3 py-2 text-sm text-danger hover:bg-danger/10">
            Remove table
          </button>
        )}
      </div>

      <div className="flex flex-col gap-3">
        {entity.attrs.length === 0 && (
          <p className="text-sm text-text-muted">No columns yet. Add the Bronze columns this table should hold.</p>
        )}
        {entity.attrs.map((a) => (
          <div key={a.id} className="flex flex-col gap-2 rounded-md border border-border/70 bg-bg/40 p-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-44">
                <FormField label="Column name" value={a.name} onChange={(e) => setAttr(a.id, { name: e.target.value })} />
              </div>
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium text-text">From Bronze column</span>
                <select className={select} value={a.source} onChange={(e) => setAttr(a.id, { source: e.target.value })}>
                  {sourceColumns.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name} ({c.sql_type})
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1.5 pb-2 text-sm text-text">
                <input
                  type="checkbox"
                  checked={a.isKey}
                  onChange={(e) => setAttr(a.id, { isKey: e.target.checked, required: e.target.checked || a.required })}
                />
                key
              </label>
              <label className="flex items-center gap-1.5 pb-2 text-sm text-text" title="Records with no value here are quarantined">
                <input
                  type="checkbox"
                  checked={a.required || a.isKey}
                  disabled={a.isKey}
                  onChange={(e) => setAttr(a.id, { required: e.target.checked })}
                />
                required
              </label>
              <button
                type="button"
                onClick={() => set({ attrs: entity.attrs.filter((x) => x.id !== a.id) })}
                className="mb-1.5 ml-auto text-sm text-text-muted hover:text-danger"
                aria-label={`Remove column ${a.name}`}
              >
                ✕
              </button>
            </div>
            <StepEditor steps={a.steps} onChange={(steps) => setAttr(a.id, { steps })} />
          </div>
        ))}

        <div className="flex flex-wrap gap-2">
          <select
            className={select}
            aria-label="Add a column"
            value=""
            onChange={(e) => e.target.value && addColumn(e.target.value)}
          >
            <option value="">+ Add a Bronze column…</option>
            {sourceColumns.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.sql_type}){entity.attrs.some((a) => a.source === c.name) ? ' – used' : ''}
              </option>
            ))}
          </select>
          {unused.length > 0 && (
            <button
              type="button"
              onClick={() =>
                set({
                  attrs: [
                    ...entity.attrs,
                    ...unused.map((c) => ({ id: newId(), name: defaultAttrName(c.name), source: c.name, steps: [], required: false, isKey: false })),
                  ],
                })
              }
              className="rounded-md border border-border px-3 py-1.5 text-sm text-text hover:bg-border/60"
            >
              Add all {unused.length} remaining
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t border-border pt-3">
        <label className="flex items-center gap-2 text-sm font-medium text-text">
          <input
            type="checkbox"
            checked={master.enabled}
            onChange={(e) =>
              setMaster({
                enabled: e.target.checked,
                column: master.column || (entity.tableName ? `${entity.tableName}_mk` : ''),
                crosswalkId: master.crosswalkId || (crosswalks.length === 1 ? crosswalks[0].id : ''),
              })
            }
          />
          Look up a master key for this table
        </label>
        {master.enabled && (
          <div className="flex flex-col gap-3 rounded-md bg-border/20 p-3">
            <p className="text-xs text-text-muted">
              The table's single key column is matched against the crosswalk. A key with no match gets the unknown
              member and is flagged, so nothing is lost.
            </p>
            {crosswalks.length === 0 ? (
              <p className="text-sm text-danger">
                No crosswalk tables registered yet — register one above (step 2), then pick it here.
              </p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm sm:col-span-2">
                  <span className="font-medium text-text">Crosswalk table</span>
                  <select
                    className={select}
                    value={master.crosswalkId}
                    onChange={(e) =>
                      setMaster({ crosswalkId: e.target.value, systemColumn: '', keyColumn: '', masterColumn: '', entityTypeColumn: '', entityType: '' })
                    }
                  >
                    <option value="">Choose…</option>
                    {crosswalks.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.full_name}
                      </option>
                    ))}
                  </select>
                </label>
                {(
                  [
                    ['systemColumn', 'Column holding the source system'],
                    ['keyColumn', 'Column holding the source key'],
                    ['masterColumn', 'Column holding the master key'],
                  ] as const
                ).map(([field, label]) => (
                  <label key={field} className="flex flex-col gap-1 text-sm">
                    <span className="font-medium text-text">{label}</span>
                    <select className={select} value={master[field]} disabled={!master.crosswalkId} onChange={(e) => setMaster({ [field]: e.target.value })}>
                      <option value="">Choose…</option>
                      {xcols.map((c) => (
                        <option key={c.name} value={c.name}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-text">Column holding the entity type (if the crosswalk has one)</span>
                  <select
                    className={select}
                    value={master.entityTypeColumn}
                    disabled={!master.crosswalkId}
                    onChange={(e) =>
                      setMaster({
                        entityTypeColumn: e.target.value,
                        entityType: e.target.value ? master.entityType : '',
                      })
                    }
                  >
                    <option value="">None — one kind of thing per crosswalk</option>
                    {xcols.map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
                {master.entityTypeColumn && (
                  <div>
                    <FormField
                      label="Entity type for this table"
                      placeholder="e.g. customer"
                      value={master.entityType}
                      onChange={(e) => setMaster({ entityType: e.target.value })}
                    />
                    <p className="mt-1 text-xs text-text-muted">Only crosswalk rows with this value are used.</p>
                  </div>
                )}
                <FormField label="Name of the master key column in this table" value={master.column} onChange={(e) => setMaster({ column: e.target.value })} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
