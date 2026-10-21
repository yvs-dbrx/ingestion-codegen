import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ConnectForm } from '../components/ConnectionGate'
import { ErrorBanner } from '../components/FormField'
import { ApiError, useAuth } from '../lib/auth'
import { connection as connectionApi } from '../lib/endpoints'
import { formatDateTime } from '../lib/format'
import { useConnection } from '../lib/useConnection'

export function ConnectionPage() {
  const { token, activeOrgId, user } = useAuth()
  const queryClient = useQueryClient()
  const query = useConnection()
  const [replacing, setReplacing] = useState(false)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const role = user?.memberships.find((m) => m.org.id === activeOrgId)?.role
  const isAdmin = role === 'admin'

  const disconnect = useMutation({
    mutationFn: () => connectionApi.disconnect(token!, activeOrgId!),
    onMutate: () => setError(null),
    onSuccess: async () => {
      setConfirmDisconnect(false)
      await queryClient.invalidateQueries({ queryKey: ['connection', activeOrgId] })
    },
    onError: (err) => {
      setConfirmDisconnect(false)
      setError(err instanceof ApiError ? err.detail : 'Something went wrong — try again')
    },
  })

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <h1 className="text-2xl font-semibold text-text">Databricks connection</h1>

      {error && <ErrorBanner message={error} />}
      {query.isLoading && <p className="text-text-muted">Checking connection…</p>}
      {query.isError && <ErrorBanner message="Couldn't load the connection — try refreshing." />}

      {query.data === null && (
        <section className="rounded-lg border border-border bg-surface p-5">
          <h2 className="font-medium text-text">Not connected</h2>
          <p className="mt-1 text-sm text-text-muted">
            Connect your Databricks workspace to discover schemas, deploy pipelines and run them.
          </p>
          {isAdmin ? (
            <div className="mt-4 max-w-sm">
              <ConnectForm />
            </div>
          ) : (
            <p className="mt-3 text-sm text-text-muted">Ask an admin in your organization to connect it.</p>
          )}
        </section>
      )}

      {query.data && (
        <section className="rounded-lg border border-border bg-surface p-5">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-success" />
            <h2 className="font-medium text-text">Connected</h2>
          </div>
          <dl className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-text-muted">Workspace</dt>
              <dd className="mt-0.5 break-all font-mono text-xs text-text">{query.data.host}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Last updated</dt>
              <dd className="mt-0.5 text-text">{formatDateTime(query.data.updated_at)}</dd>
            </div>
          </dl>
          <p className="mt-4 text-xs text-text-muted">
            The access token is stored encrypted and is never shown again — not even to admins.
          </p>

          {isAdmin ? (
            <div className="mt-5 flex flex-col gap-4 border-t border-border pt-5">
              {replacing ? (
                <div className="max-w-sm">
                  <p className="mb-3 text-sm text-text-muted">
                    Enter the host and a new token. Nothing changes unless Databricks accepts them.
                  </p>
                  <ConnectForm onConnected={() => setReplacing(false)} />
                  <button
                    onClick={() => setReplacing(false)}
                    className="mt-3 text-sm text-text-muted hover:text-text"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => setReplacing(true)}
                    className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text hover:bg-border/60"
                  >
                    Replace token or workspace
                  </button>
                  {confirmDisconnect ? (
                    <>
                      <button
                        onClick={() => disconnect.mutate()}
                        disabled={disconnect.isPending}
                        className="rounded-md bg-danger px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
                      >
                        {disconnect.isPending ? 'Disconnecting…' : 'Yes, disconnect'}
                      </button>
                      <button
                        onClick={() => setConfirmDisconnect(false)}
                        className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text hover:bg-border/60"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() => setConfirmDisconnect(true)}
                      className="rounded-md border border-danger/50 px-4 py-2 text-sm font-medium text-danger hover:bg-danger/10"
                    >
                      Disconnect
                    </button>
                  )}
                </div>
              )}
              {confirmDisconnect && (
                <p className="text-sm text-text-muted">
                  Pipelines already deployed keep running in Databricks, but you won't be able to discover, deploy,
                  run or delete anything from here until you reconnect.
                </p>
              )}
            </div>
          ) : (
            <p className="mt-4 text-sm text-text-muted">Only an admin can change the connection.</p>
          )}
        </section>
      )}
    </div>
  )
}
