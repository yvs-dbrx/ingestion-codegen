import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { ProtectedRoute } from './components/ProtectedRoute'
import { AuthProvider } from './lib/auth'
import { BronzeBuilderPage } from './pages/BronzeBuilderPage'
import { SilverBuilderPage } from './pages/SilverBuilderPage'
import { AcceptInvitePage } from './pages/AcceptInvitePage'
import { ConnectionPage } from './pages/ConnectionPage'
import { DashboardPage } from './pages/DashboardPage'
import { LoginPage } from './pages/LoginPage'
import { PipelineBuilderPage } from './pages/PipelineBuilderPage'
import { PipelineDetailPage } from './pages/PipelineDetailPage'
import { PipelinesPage } from './pages/PipelinesPage'
import { RegisterPage } from './pages/RegisterPage'
import { RunsPage } from './pages/RunsPage'
import { SourcesPage } from './pages/SourcesPage'
import { TablesPage } from './pages/TablesPage'
import { TeamPage } from './pages/TeamPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/accept-invite" element={<AcceptInvitePage />} />

            <Route element={<ProtectedRoute />}>
              <Route element={<AppShell />}>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/sources" element={<SourcesPage />} />
                <Route path="/pipelines" element={<PipelinesPage />} />
                <Route path="/pipelines/new" element={<PipelineBuilderPage />} />
                <Route path="/pipelines/new/bronze" element={<BronzeBuilderPage />} />
                <Route path="/pipelines/new/silver" element={<SilverBuilderPage />} />
                <Route path="/pipelines/:id" element={<PipelineDetailPage />} />
                <Route path="/tables" element={<TablesPage />} />
                <Route path="/runs" element={<RunsPage />} />
                <Route path="/team" element={<TeamPage />} />
                <Route path="/connection" element={<ConnectionPage />} />
              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
