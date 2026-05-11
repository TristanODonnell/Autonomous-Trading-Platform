import type { ActivityItem } from '../../types'

const DOT_COLOR: Record<ActivityItem['type'], string> = {
  fill:    'var(--accent)',
  paper:   'var(--blue)',
  warning: 'var(--yellow)',
  system:  'var(--text3)',
}

interface ActivityFeedProps {
  items: ActivityItem[]
}

export default function ActivityFeed({ items }: ActivityFeedProps) {
  return (
    <div>
      {items.map((item, i) => (
        <div
          key={item.id}
          className="flex gap-3 py-2.5"
          style={{ borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none' }}
        >
          <span
            className="w-1.5 h-1.5 rounded-full shrink-0 mt-[5px]"
            style={{ background: DOT_COLOR[item.type] }}
          />
          <div>
            <div className="text-[12px] text-[var(--text)]">{item.text}</div>
            <div className="font-mono text-[10px] text-[var(--text2)] mt-0.5">{item.meta}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
