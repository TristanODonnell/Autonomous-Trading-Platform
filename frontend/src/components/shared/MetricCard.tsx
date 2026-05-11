import { cn } from '../../lib/utils'
import type { ReactNode } from 'react'

interface MetricCardProps {
  label: string
  value: ReactNode
  change?: ReactNode
  changeDirection?: 'up' | 'down' | 'neutral'
  className?: string
}

const CHANGE_COLOR: Record<'up' | 'down' | 'neutral', string> = {
  up:      'var(--accent)',
  down:    'var(--red)',
  neutral: 'var(--text2)',
}

export default function MetricCard({ label, value, change, changeDirection, className }: MetricCardProps) {
  return (
    <div
      className={cn('rounded-lg p-4', className)}
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div className="font-mono text-[9px] font-medium text-[var(--text3)] uppercase tracking-[0.15em] mb-2">
        {label}
      </div>
      <div className="font-mono font-light text-[var(--text)] leading-none mb-1.5">
        {value}
      </div>
      {change !== undefined && (
        <div
          className="font-mono text-[11px]"
          style={{ color: changeDirection ? CHANGE_COLOR[changeDirection] : 'var(--text2)' }}
        >
          {change}
        </div>
      )}
    </div>
  )
}
