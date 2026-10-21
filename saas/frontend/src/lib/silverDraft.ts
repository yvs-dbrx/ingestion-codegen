import type { SilverEntityIn, SilverStep } from './types'

/** What the entity editor edits: one row per attribute, a "key" tick-box instead of a separate key list. */
export interface AttrDraft {
  id: string
  name: string
  source: string
  steps: SilverStep[]
  required: boolean
  isKey: boolean
}

export interface MasterDraft {
  enabled: boolean
  crosswalkId: string
  column: string
  systemColumn: string
  keyColumn: string
  masterColumn: string
  entityTypeColumn: string // optional: only when the crosswalk holds several kinds of thing
  entityType: string
}

export interface EntityDraft {
  id: string
  tableName: string
  attrs: AttrDraft[]
  master: MasterDraft
}

let counter = 0
export const newId = () => `d${Date.now().toString(36)}${(counter++).toString(36)}`

export function emptyMaster(): MasterDraft {
  return { enabled: false, crosswalkId: '', column: '', systemColumn: '', keyColumn: '', masterColumn: '', entityTypeColumn: '', entityType: '' }
}

export function newEntity(tableName = ''): EntityDraft {
  return { id: newId(), tableName, attrs: [], master: emptyMaster() }
}

/** A plain starting name for an attribute: the source column, lower-cased. */
export const defaultAttrName = (source: string) => source.toLowerCase()

export function toEntityBody(d: EntityDraft): SilverEntityIn {
  return {
    table_name: d.tableName,
    business_key: d.attrs.filter((a) => a.isKey).map((a) => a.name),
    attributes: d.attrs.map((a) => ({
      name: a.name,
      source_column: a.source,
      steps: a.steps,
      required: a.required,
    })),
    master_key: d.master.enabled
      ? {
          column: d.master.column,
          crosswalk_asset_id: d.master.crosswalkId,
          system_column: d.master.systemColumn,
          key_column: d.master.keyColumn,
          master_column: d.master.masterColumn,
          entity_type_column: d.master.entityTypeColumn || null,
          entity_type: d.master.entityTypeColumn ? d.master.entityType : null,
        }
      : null,
  }
}

/** The obvious gaps, so the Preview button can say what's missing before the server has to. */
export function draftGaps(entities: EntityDraft[]): string[] {
  const gaps: string[] = []
  if (entities.length === 0) gaps.push('add at least one Silver table')
  for (const e of entities) {
    const label = e.tableName || 'a table'
    if (!e.tableName) gaps.push('every Silver table needs a name')
    if (e.attrs.length === 0) gaps.push(`${label} needs at least one column`)
    if (e.attrs.length > 0 && !e.attrs.some((a) => a.isKey)) gaps.push(`${label} needs a business key (tick "key" on a column)`)
    if (e.attrs.some((a) => !a.name)) gaps.push(`${label} has a column without a name`)
    const m = e.master
    if (m.enabled && Boolean(m.entityTypeColumn) !== Boolean(m.entityType)) {
      gaps.push(`${label}: give both the entity type column and its value, or neither`)
    }
    if (m.enabled && !(m.crosswalkId && m.column && m.systemColumn && m.keyColumn && m.masterColumn)) {
      gaps.push(`${label}: finish the master key settings or switch them off`)
    }
  }
  return gaps
}
