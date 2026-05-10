interface SparklineChartProps {
  data: number[]
  color?: string
}

export default function SparklineChart({ data, color = 'var(--accent)' }: SparklineChartProps) {
  if (data.length < 2) return null

  const W = 200
  const H = 36
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  const step = W / (data.length - 1)

  const pts = data.map((v, i) => {
    const x = i * step
    const y = H - ((v - min) / range) * (H - 4) - 2
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })

  const d = `M${pts[0]} L${pts.slice(1).join(' L')}`

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-9" preserveAspectRatio="none">
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
