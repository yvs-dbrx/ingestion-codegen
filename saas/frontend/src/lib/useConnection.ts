import { useQuery } from '@tanstack/react-query'
import { connection } from './endpoints'
import { useAuth } from './auth'

/** The active org's Databricks connection — null (not an error) when none exists yet. */
export function useConnection() {
  const { token, activeOrgId } = useAuth()
  return useQuery({
    queryKey: ['connection', activeOrgId],
    queryFn: () => connection.get(token!, activeOrgId!),
    enabled: Boolean(token && activeOrgId),
  })
}
