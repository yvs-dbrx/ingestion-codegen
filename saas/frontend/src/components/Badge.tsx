import type { ReactNode } from 'react'

type Tone = 'neutral' | 'gold' | 'success' | 'danger'

const toneClasses: Record<Tone, string> = {
  neutral: 'bg-border/50 text-text-muted',
  gold: 'bg-accent/15 text-accent-hover',
  success: 'bg-success/15 text-success',
  danger: 'bg-danger/15 text-danger',
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${toneClasses[tone]}`}>
      {children}
    </span>
  )
}
