import { cn } from '../../lib/utils'
import type { ReactNode } from 'react'
import type { GovernanceState } from '../../types'

export type BadgeVariant = 'green' | 'blue' | 'yellow' | 'red' | 'purple' | 'gray'

const VARIANT_STYLES: Record<BadgeVariant, string> = {
  green:  'bg-[var(--accent-dim2)]  text-[var(--accent)]  border border-[rgba(0,229,160,0.3)]',
  blue:   'bg-[var(--blue-dim)]     text-[var(--blue)]    border border-[rgba(59,158,255,0.3)]',
  yellow: 'bg-[var(--yellow-dim)]   text-[var(--yellow)]  border border-[rgba(232,168,56,0.3)]',
  red:    'bg-[var(--red-dim)]      text-[var(--red)]     border border-[rgba(255,77,109,0.3)]',
  purple: 'bg-[var(--purple-dim)]   text-[var(--purple)]  border border-[rgba(155,114,255,0.3)]',
  gray:   'bg-[rgba(139,148,158,0.1)] text-[var(--text2)] border border-[rgba(139,148,158,0.2)]',
}

interface StatusBadgeProps {
  variant: BadgeVariant
  children: ReactNode
  dot?: boolean
  className?: string
}

export default function StatusBadge({ variant, children, dot, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-[3px]',
        'font-mono text-[9px] font-medium uppercase tracking-[0.08em] whitespace-nowrap',
        VARIANT_STYLES[variant],
        className
      )}
    >
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0" />}
      {children}
    </span>
  )
}

export function governanceBadgeVariant(state: GovernanceState): BadgeVariant {
  const map: Record<GovernanceState, BadgeVariant> = {
    live:     'green',
    paper:    'blue',
    research: 'purple',
    proposed: 'gray',
    rejected: 'red',
    retired:  'gray',
  }
  return map[state]
}

export function healthBadgeVariant(status: 'healthy' | 'delayed' | 'error'): BadgeVariant {
  return status === 'healthy' ? 'green' : status === 'delayed' ? 'yellow' : 'red'
}
