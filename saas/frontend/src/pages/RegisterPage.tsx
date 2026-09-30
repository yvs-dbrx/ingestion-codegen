import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { ErrorBanner, FormField } from '../components/FormField'
import { ApiError, useAuth } from '../lib/auth'

export function RegisterPage() {
  const { register, token } = useAuth()
  const navigate = useNavigate()
  const [orgName, setOrgName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (token) return <Navigate to="/" replace />

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await register(email, password, orgName)
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
        <h1 className="text-xl font-semibold text-text">Create your Refinaire account</h1>
        <p className="-mt-2 text-sm text-text-muted">
          This creates your own organization, with you as its admin.
        </p>

        {error && <ErrorBanner message={error} />}

        <FormField
          label="Organization name"
          required
          value={orgName}
          onChange={(e) => setOrgName(e.target.value)}
        />
        <FormField
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
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
          {submitting ? 'Creating account…' : 'Create account'}
        </button>

        <p className="text-center text-sm text-text-muted">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-accent hover:underline">
            Log in
          </Link>
        </p>
      </form>
    </div>
  )
}
