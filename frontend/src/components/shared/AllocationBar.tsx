import { cn } from '../../lib/utils'

type BarColor = 'green' | 'blue' | 'purple' | 'yellow'

interface AllocationBarProps {
  label: string
  percent: number
  color?: BarColor
}

const COLOR_VAR: Record<BarColor, string> = {
  green:  'var(--accent)',
  blue:   'var(--blue)',
  purple: 'var(--purple)',
  yellow: 'var(--yellow)',
}

export default function AllocationBar({ label, percent, color = 'green' }: AllocationBarProps) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[12px] text-[var(--text)]">{label}</span>
        <span className="font-mono text-[11px] text-[var(--text2)]">{percent}%</span>
      </div>
      <div
        className="h-[5px] rounded-[3px] overflow-hidden"
        style={{ background: 'var(--border2)' }}
      >
        <div
          className="h-full rounded-[3px] transition-[width] duration-300"
          style={{ width: `${percent}%`, background: COLOR_VAR[color] }}
        />
      </div>
    </div>
  )
}
