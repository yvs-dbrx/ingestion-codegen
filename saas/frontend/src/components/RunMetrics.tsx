import { Badge } from './Badge'
import type { RunMetrics as RunMetricsData, TableMetrics } from '../lib/types'

const n = (v: number | undefined) => (v === undefined ? '—' : v.toLocaleString())

function describe(layer: RunMetricsData['layer'], t: TableMetrics): string {
  if (layer === 'silver') {
    const parts = [
      `${n(t.distinct_keys)} keys: ${n(t.rows_inserted)} new, ${n(t.rows_updated)} updated, ${n(t.unchanged)} unchanged`,
      `${n(t.quarantined_keys)} quarantined`,
    ]
    if (t.null_key_records) parts.push(`${n(t.null_key_records)} records without a key`)
    if (t.unmapped_keys !== undefined) parts.push(`${n(t.unmapped_keys)} without a master key`)
    if (t.conflicting_keys) parts.push(`${n(t.conflicting_keys)} keys with conflicting values`)
    return `${parts.join(' · ')} → ${n(t.rows_in_target)} rows`
  }
  if (layer === 'bronze') {
    return (
      `read ${n(t.rows_read)} · ${n(t.duplicates_removed)} duplicates removed · ` +
      `${n(t.rows_inserted)} new · ${n(t.already_in_target)} already there → ${n(t.rows_in_target)} rows`
    )
  }
  return `loaded ${n(t.rows_written ?? t.rows_read)} rows → ${n(t.rows_in_target)} in the table`
}

/** A run's own account of what it did, in plain words. Says nothing when there is nothing to say. */
export function RunMetrics({ metrics }: { metrics: RunMetricsData | null }) {
  if (!metrics) return null
  if (metrics.unavailable) {
    return (
      <p className="mt-1 text-xs text-text-muted">
        No counts reported by this run. Pipelines deployed before count reporting existed never report them — delete
        and redeploy the pipeline (its tables are kept).
      </p>
    )
  }
  const tables = Object.entries(metrics.tables ?? {})
  if (tables.length === 0) return null

  return (
    <div className="mt-1 flex flex-col gap-0.5 text-xs text-text-muted">
      {tables.map(([name, t]) => (
        <p key={name}>
          <span className="font-mono text-text">{name}</span>: {describe(metrics.layer, t)}
          {metrics.layer === 'silver' && t.crosswalk_rows === 0 && (
            <span className="ml-1 text-danger">
              (the crosswalk had no rows for this source system and entity type, so no master key could be found — check
              both names against the crosswalk)
            </span>
          )}
          {metrics.layer === 'silver' &&
            (t.crosswalk_rows ?? 0) > 0 &&
            (t.distinct_keys ?? 0) > 0 &&
            t.unmapped_keys === t.distinct_keys && (
              <span className="ml-1 text-danger">(none of the keys matched the crosswalk)</span>
            )}
          {t.extra_source_columns && t.extra_source_columns.length > 0 && (
            <span className="ml-1 text-danger">
              (new Raw columns not compared: {t.extra_source_columns.join(', ')})
            </span>
          )}
        </p>
      ))}
      {metrics.reconciled === false && (
        <p>
          <Badge tone="danger">counts don't add up</Badge>
        </p>
      )}
    </div>
  )
}
