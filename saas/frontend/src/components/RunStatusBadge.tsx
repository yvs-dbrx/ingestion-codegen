import { Badge } from './Badge'
import type { RunStatus } from '../lib/types'

const tones: Record<RunStatus, 'neutral' | 'gold' | 'success' | 'danger'> = {
  pending: 'neutral',
  running: 'gold',
  success: 'success',
  failed: 'danger',
  cancelled: 'neutral',
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={tones[status]}>{status}</Badge>
}
