import { useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ErrorBanner, FormField } from './FormField'
import { ApiError, useAuth } from '../lib/auth'
import { discovery } from '../lib/endpoints'
import type { DiscoveredSourceOut, SourceType } from '../lib/types'

const SOURCE_TYPES: { value: SourceType; label: string }[] = [
  { value: 'csv', label: 'CSV' },
  { value: 'json', label: 'JSON' },
  { value: 'xml', label: 'XML (from XSD)' },
  { value: 'cobol', label: 'COBOL / mainframe' },
]

interface FormState {
  sourceType: SourceType
  mode: 'single' | 'multi'
  sourcePath: string
  schemaDefinitionPath: string
  delimiter: string
  hasHeader: boolean
  multiline: boolean
  rowTag: string
  clusterId: string
  isRecordSequence: boolean
  isText: boolean
}

const initialState: FormState = {
  sourceType: 'csv',
  mode: 'single',
  sourcePath: '',
  schemaDefinitionPath: '',
  delimiter: ',',
  hasHeader: true,
  multiline: false,
  rowTag: 'record',
  clusterId: '',
  isRecordSequence: false,
  isText: false,
}

/** Discovering a folder (mode="multi") is only meaningful for csv/json — the engine reads
 * one file at a time for xml/cobol. */
const SUPPORTS_MULTI = new Set<SourceType>(['csv', 'json'])

export function DiscoverForm({ onDiscovered }: { onDiscovered: (results: DiscoveredSourceOut[]) => void }) {
  const { token, activeOrgId } = useAuth()
  const queryClient = useQueryClient()
  const [form, setForm] = useState<FormState>(initialState)
  const [error, setError] = useState<{ message: string; problems?: string[] } | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function selectSourceType(sourceType: SourceType) {
    setForm((f) => ({
      ...f,
      sourceType,
      mode: SUPPORTS_MULTI.has(sourceType) ? f.mode : 'single',
    }))
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const result = await discovery.discover(token!, activeOrgId!, {
        source_type: form.sourceType,
        mode: form.mode,
        source_path: form.sourcePath,
        schema_definition_path: form.schemaDefinitionPath || null,
        delimiter: form.delimiter,
        has_header: form.hasHeader,
        multiline: form.multiline,
        row_tag: form.rowTag,
        cluster_id: form.clusterId || null,
        is_record_sequence: form.isRecordSequence,
        is_text: form.isText,
      })
      await queryClient.invalidateQueries({ queryKey: ['sources', activeOrgId] })
      onDiscovered(result.sources)
      setForm((f) => ({ ...initialState, sourceType: f.sourceType, mode: f.mode })) // keep the type picked for the next one
    } catch (err) {
      setError(
        err instanceof ApiError
          ? { message: err.detail, problems: err.problems }
          : { message: 'Something went wrong — try again' },
      )
    } finally {
      setSubmitting(false)
    }
  }

  const isXml = form.sourceType === 'xml'
  const isCobol = form.sourceType === 'cobol'
  const isCsvOrJson = form.sourceType === 'csv' || form.sourceType === 'json'

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5">
      <h3 className="font-medium text-text">Discover a schema</h3>

      {error && <ErrorBanner message={error.message} problems={error.problems} />}

      <div className="grid grid-cols-2 gap-4">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-text">Source type</span>
          <select
            className="rounded-md border border-border bg-surface px-3 py-2"
            value={form.sourceType}
            onChange={(e) => selectSourceType(e.target.value as SourceType)}
          >
            {SOURCE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-text">Mode</span>
          <select
            className="rounded-md border border-border bg-surface px-3 py-2 disabled:opacity-60"
            value={form.mode}
            disabled={!SUPPORTS_MULTI.has(form.sourceType)}
            onChange={(e) => set('mode', e.target.value as 'single' | 'multi')}
          >
            <option value="single">Single file</option>
            <option value="multi">Every file in a folder</option>
          </select>
        </label>
      </div>

      <FormField
        label={form.mode === 'multi' ? 'Folder path' : 'File path'}
        placeholder="/Volumes/main/landing/customers.csv"
        required
        value={form.sourcePath}
        onChange={(e) => set('sourcePath', e.target.value)}
      />

      {(isXml || isCobol) && (
        <FormField
          label={isXml ? 'XSD path' : 'Copybook path'}
          placeholder={isXml ? '/Volumes/main/landing/customers.xsd' : '/Volumes/main/landing/customers.cpy'}
          required
          value={form.schemaDefinitionPath}
          onChange={(e) => set('schemaDefinitionPath', e.target.value)}
        />
      )}

      {isCsvOrJson && (
        <div className="grid grid-cols-2 gap-4">
          <FormField
            label="Delimiter"
            value={form.delimiter}
            onChange={(e) => set('delimiter', e.target.value)}
          />
          <div className="flex items-end gap-4 pb-2">
            <label className="flex items-center gap-2 text-sm text-text">
              <input type="checkbox" checked={form.hasHeader} onChange={(e) => set('hasHeader', e.target.checked)} />
              Has header row
            </label>
            <label className="flex items-center gap-2 text-sm text-text">
              <input type="checkbox" checked={form.multiline} onChange={(e) => set('multiline', e.target.checked)} />
              Multiline records
            </label>
          </div>
        </div>
      )}

      {isXml && (
        <FormField label="Row tag" required value={form.rowTag} onChange={(e) => set('rowTag', e.target.value)} />
      )}

      <FormField
        label={isCobol ? 'Cluster ID (required — needs the Cobrix library)' : 'Cluster ID (optional)'}
        placeholder="0101-000000-abc123"
        required={isCobol}
        value={form.clusterId}
        onChange={(e) => set('clusterId', e.target.value)}
      />

      {isCobol && (
        <div className="flex gap-4">
          <label className="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={form.isRecordSequence}
              onChange={(e) => set('isRecordSequence', e.target.checked)}
            />
            Record-sequence file
          </label>
          <label className="flex items-center gap-2 text-sm text-text">
            <input type="checkbox" checked={form.isText} onChange={(e) => set('isText', e.target.checked)} />
            Text-encoded
          </label>
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="self-start rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast transition-opacity hover:bg-accent-hover disabled:opacity-60"
      >
        {submitting ? 'Discovering… this can take a minute' : 'Discover'}
      </button>
    </form>
  )
}
