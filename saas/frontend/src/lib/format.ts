export function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** A Databricks table name from a file path: "/a/b/Customer Data.csv" -> "customer_data". */
export function suggestTableName(path: string): string {
  const file = path.split('/').pop() ?? ''
  const stem = file.replace(/\.[^.]+$/, '')
  const cleaned = stem
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
  if (!cleaned) return 'table_1'
  return /^[a-z_]/.test(cleaned) ? cleaned : `t_${cleaned}`
}
