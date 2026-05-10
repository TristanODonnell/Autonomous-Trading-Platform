interface ToggleProps {
  on: boolean
  onChange?: (on: boolean) => void
  disabled?: boolean
}

export default function Toggle({ on, onChange, disabled }: ToggleProps) {
  return (
    <div
      role="switch"
      aria-checked={on}
      className="relative shrink-0 transition-colors duration-200"
      style={{
        width: 36,
        height: 20,
        borderRadius: 10,
        background: on ? 'var(--accent)' : 'var(--border2)',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.5 : 1,
      }}
      onClick={() => !disabled && onChange?.(!on)}
    >
      <div
        className="absolute top-[3px] w-3.5 h-3.5 rounded-full bg-white transition-all duration-200"
        style={{ left: on ? 19 : 3 }}
      />
    </div>
  )
}
