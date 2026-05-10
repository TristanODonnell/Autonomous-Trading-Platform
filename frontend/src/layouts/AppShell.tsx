import { Outlet } from '@tanstack/react-router'
import TopNav from './TopNav'

export default function AppShell() {
  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)' }}>
      <TopNav />
      <main>
        <Outlet />
      </main>
    </div>
  )
}
