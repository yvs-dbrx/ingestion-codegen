import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ErrorBanner, FormField } from './FormField'
import { ApiError, useAuth } from '../lib/auth'
import { team } from '../lib/endpoints'

/** Join an organization with an invitation token (or the whole invitation link) for someone who
 * already has an account. Used on the Team page and on the "not in any organization" screen. */
export function JoinOrganizationForm() {
  const { token, refreshUser, setActiveOrgId } = useAuth()
  const [value, setValue] = useState('')
  const [error, setError] = useState<{ message: string; problems?: string[] } | null>(null)

  const join = useMutation({
    mutationFn: () => team.acceptInvite(token!, value.trim()),
    onMutate: () => setError(null),
    onSuccess: async (membership) => {
      setValue('')
      await refreshUser()
      setActiveOrgId(membership.org.id)
    },
    onError: (err) =>
      setError(
        err instanceof ApiError
          ? { message: err.detail, problems: err.problems }
          : { message: 'Something went wrong — try again' },
      ),
  })

  function onChange(raw: string) {
    // accept either the bare token or the whole invitation link
    try {
      setValue(new URL(raw).searchParams.get('token') ?? raw)
    } catch {
      setValue(raw)
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        join.mutate()
      }}
      className="flex flex-col gap-3"
    >
      {error && <ErrorBanner message={error.message} problems={error.problems} />}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-60 flex-1">
          <FormField label="Invitation token or link" required value={value} onChange={(e) => onChange(e.target.value)} />
        </div>
        <button
          type="submit"
          disabled={join.isPending || value.trim().length < 20}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-60"
        >
          {join.isPending ? 'Joining…' : 'Join'}
        </button>
      </div>
    </form>
  )
}
