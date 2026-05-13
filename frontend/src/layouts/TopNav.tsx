import { Link } from '@tanstack/react-router'
import { useAppStore } from '../store/useAppStore'

const TAB_BASE =
  'px-4 py-[14px] font-mono text-[11px] font-normal text-[var(--text2)] uppercase tracking-[0.05em] ' +
  'border-b-2 border-transparent hover:text-[var(--text)] transition-all duration-150 select-none cursor-pointer whitespace-nowrap'

const TAB_ACTIVE =
  'px-4 py-[14px] font-mono text-[11px] font-normal text-[var(--accent)] uppercase tracking-[0.05em] ' +
  'border-b-2 border-[var(--accent)] transition-all duration-150 select-none cursor-pointer whitespace-nowrap'

const ENV_LABEL: Record<string, string> = {
  backtesting: 'Backtest Mode',
  paper: 'Paper Mode',
  live: 'Live Mode',
}

export default function TopNav() {
  const { activeEnv, killSwitchActive, toggleKillSwitch } = useAppStore()

  return (
    <nav
      className="flex items-center gap-0 px-6 sticky top-0 z-[100]"
      style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}
    >
      {/* Logo */}
      <div
        className="font-mono text-[13px] font-semibold text-[var(--accent)] uppercase tracking-[0.12em] py-[14px] pr-6 mr-2 shrink-0"
        style={{ borderRight: '1px solid var(--border)' }}
      >
        ◈ WeTrade
      </div>

      {/* Nav tabs */}
      <Link to="/" activeOptions={{ exact: true }} className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
        Dashboard
      </Link>
      <Link to="/portfolio" className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
        Portfolio
      </Link>
      <Link to="/strategy" className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
        Experiment Lab
      </Link>
      <Link to="/controls" className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
        Controls
      </Link>
      <Link to="/settings" className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
        Settings
      </Link>

      {/* Right side */}
      <div className="ml-auto flex items-center gap-3">
        {/* Status indicator */}
        <div className="flex items-center gap-1.5 font-mono text-[10px] text-[var(--text2)] tracking-[0.05em]">
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-pulse shrink-0" />
          Live · {ENV_LABEL[activeEnv]}
        </div>

        {/* Kill switch */}
        <button
          onClick={toggleKillSwitch}
          className="flex items-center gap-1.5 px-[14px] py-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.08em] rounded cursor-pointer transition-colors duration-150"
          style={{
            background: killSwitchActive ? 'rgba(255,77,109,0.25)' : 'var(--red-dim)',
            border: '1px solid var(--red)',
            color: 'var(--red)',
          }}
          onMouseEnter={(e) => {
            if (!killSwitchActive) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,77,109,0.18)'
          }}
          onMouseLeave={(e) => {
            if (!killSwitchActive) (e.currentTarget as HTMLButtonElement).style.background = 'var(--red-dim)'
          }}
        >
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--red)] shrink-0" />
          {killSwitchActive ? 'Trading Halted' : 'Kill Switch'}
        </button>
      </div>
    </nav>
  )
}
