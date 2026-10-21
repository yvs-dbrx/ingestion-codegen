import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../lib/auth'

const navItems = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/sources', label: 'Sources' },
  { to: '/pipelines', label: 'Pipelines' },
  { to: '/tables', label: 'Tables' },
  { to: '/runs', label: 'Runs' },
  { to: '/team', label: 'Team' },
  { to: '/connection', label: 'Connection' },
]

export function AppShell() {
  const { user, activeOrgId, setActiveOrgId, logout } = useAuth()
  const activeMembership = user?.memberships.find((m) => m.org.id === activeOrgId)

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border bg-surface px-4 py-3 sm:px-6">
        <span className="shrink-0 text-lg font-semibold tracking-tight">Refinaire</span>

        <nav className="order-3 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `shrink-0 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-accent text-accent-contrast'
                    : 'text-text-muted hover:bg-border/60 hover:text-text'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-3 text-sm">
          {user && user.memberships.length > 1 && (
            <select
              className="rounded-md border border-border bg-surface px-2 py-1 text-sm"
              value={activeOrgId ?? ''}
              onChange={(e) => setActiveOrgId(e.target.value)}
            >
              {user.memberships.map((m) => (
                <option key={m.org.id} value={m.org.id}>
                  {m.org.name}
                </option>
              ))}
            </select>
          )}
          {activeMembership && (
            <span className="hidden text-text-muted md:inline">
              {activeMembership.org.name} · <span className="capitalize">{activeMembership.role}</span>
            </span>
          )}
          <span className="hidden text-text-muted sm:inline">{user?.email}</span>
          <button
            onClick={logout}
            className="shrink-0 rounded-md border border-border px-3 py-1.5 font-medium text-text hover:bg-border/60"
          >
            Log out
          </button>
        </div>
      </header>

      <main className="flex-1 bg-bg px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}
