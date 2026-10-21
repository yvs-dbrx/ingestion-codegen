import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { ErrorBanner, FormField } from '../components/FormField'
import { ApiError, useAuth } from '../lib/auth'

/** Public page an invitee lands on from the link an admin sent them. For someone who doesn't
 * have an account yet: the invitation fixes their email and organization, they only choose a
 * password. People who already have an account log in and use Team → "Join another organization". */
export function AcceptInvitePage() {
  const { acceptInvite, token: sessionToken } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [inviteToken, setInviteToken] = useState(params.get('token') ?? '')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (sessionToken) return <Navigate to="/team" replace />

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await acceptInvite(inviteToken.trim(), password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Something went wrong — try again')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <form
        onSubmit={handleSubmit}
        className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-border bg-surface p-8 shadow-sm"
      >
        <h1 className="text-xl font-semibold text-text">Join your team on Refinaire</h1>
        <p className="-mt-2 text-sm text-text-muted">
          Choose a password to create your account. Your email and organization come from the invitation.
        </p>

        {error && <ErrorBanner message={error} />}

        <FormField
          label="Invitation token"
          required
          value={inviteToken}
          onChange={(e) => setInviteToken(e.target.value)}
        />
        <FormField
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          minLength={10}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <p className="-mt-2 text-xs text-text-muted">At least 10 characters.</p>

        <button
          type="submit"
          disabled={submitting}
          className="mt-2 rounded-md bg-accent px-4 py-2 font-medium text-accent-contrast transition-opacity hover:bg-accent-hover disabled:opacity-60"
        >
          {submitting ? 'Creating account…' : 'Create account and join'}
        </button>

        <p className="text-center text-sm text-text-muted">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-accent hover:underline">
            Log in
          </Link>
          , then use Team → Join another organization.
        </p>
      </form>
    </div>
  )
}
