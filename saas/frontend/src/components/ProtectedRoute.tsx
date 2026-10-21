import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { NoOrganizationPage } from '../pages/NoOrganizationPage'

export function ProtectedRoute() {
  const { token, user, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center text-text-muted">
        Loading…
      </div>
    )
  }
  if (!token) return <Navigate to="/login" replace />
  if (user && user.memberships.length === 0) return <NoOrganizationPage />
  return <Outlet />
}
