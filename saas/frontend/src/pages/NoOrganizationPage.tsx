import { JoinOrganizationForm } from '../components/JoinOrganizationForm'
import { useAuth } from '../lib/auth'

/** Shown to a signed-in user who belongs to no organization (they left their only one, or were
 * removed from it). Everything else in the app is organization-scoped, so this is the only way
 * back in: an invitation. */
export function NoOrganizationPage() {
  const { user, logout } = useAuth()

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="flex w-full max-w-md flex-col gap-4 rounded-lg border border-border bg-surface p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-text">You're not in an organization</h1>
        <p className="text-sm text-text-muted">
          {user?.email} isn't a member of any organization right now. Ask an admin to invite you, then paste the
          invitation link below.
        </p>
        <JoinOrganizationForm />
        <button onClick={logout} className="self-start text-sm text-text-muted hover:text-text">
          Log out
        </button>
      </div>
    </div>
  )
}
