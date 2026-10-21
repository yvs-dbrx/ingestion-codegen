import { useState, type FormEvent, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ErrorBanner, FormField } from './FormField'
import { ApiError, useAuth } from '../lib/auth'
import { connection as connectionApi } from '../lib/endpoints'
import { useConnection } from '../lib/useConnection'

/** Renders `children` once the active org has a Databricks connection; otherwise shows a
 * connect form (admins) or a message to ask one (members). Used by anything that needs to
 * reach Databricks — discovery today, pipeline deploy/run later. */
export function ConnectionGate({ children }: { children: ReactNode }) {
  const { user, activeOrgId } = useAuth()
  const role = user?.memberships.find((m) => m.org.id === activeOrgId)?.role
  const query = useConnection()

  if (query.isLoading) {
    return <p className="text-sm text-text-muted">Checking your Databricks connection…</p>
  }
  if (query.isError) {
    return <ErrorBanner message="Couldn't check your Databricks connection — try refreshing." />
  }
  if (query.data) {
    return <>{children}</>
  }

  return (
    <div className="rounded-lg border border-dashed border-border bg-surface p-5">
      <h3 className="font-medium text-text">No Databricks workspace connected</h3>
      <p className="mt-1 text-sm text-text-muted">
        Connect your organization's Databricks workspace to discover schemas and deploy pipelines.
      </p>
      {role === 'admin' ? (
        <div className="mt-4 max-w-sm">
          <ConnectForm />
        </div>
      ) : (
        <p className="mt-3 text-sm text-text-muted">Ask an admin in your organization to connect it.</p>
      )}
    </div>
  )
}

export function ConnectForm({ onConnected }: { onConnected?: () => void }) {
  const { token, activeOrgId } = useAuth()
  const queryClient = useQueryClient()
  const [host, setHost] = useState('')
  const [pat, setPat] = useState('')
  const [error, setError] = useState<{ message: string; problems?: string[] } | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await connectionApi.connect(token!, activeOrgId!, { host, token: pat })
      await queryClient.invalidateQueries({ queryKey: ['connection', activeOrgId] })
      setPat('')
      onConnected?.()
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

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      {error && <ErrorBanner message={error.message} problems={error.problems} />}
      <FormField
        label="Workspace host"
        placeholder="dbc-xxxxxxxx.cloud.databricks.com"
        required
        value={host}
        onChange={(e) => setHost(e.target.value)}
      />
      <FormField
        label="Personal access token"
        type="password"
        placeholder="dapi…"
        required
        value={pat}
        onChange={(e) => setPat(e.target.value)}
      />
      <button
        type="submit"
        disabled={submitting}
        className="self-start rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast transition-opacity hover:bg-accent-hover disabled:opacity-60"
      >
        {submitting ? 'Connecting…' : 'Connect'}
      </button>
    </form>
  )
}
