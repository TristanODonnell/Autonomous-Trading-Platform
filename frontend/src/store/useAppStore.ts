import { create } from 'zustand'
import type { Environment } from '../types'

interface AppStore {
  activeEnv: Environment
  killSwitchActive: boolean
  toggleKillSwitch: () => void
  setKillSwitchActive: (active: boolean) => void
  setActiveEnv: (env: Environment) => void
}

export const useAppStore = create<AppStore>((set) => ({
  activeEnv: 'paper',
  killSwitchActive: false,
  toggleKillSwitch: () => set((s) => ({ killSwitchActive: !s.killSwitchActive })),
  setKillSwitchActive: (active) => set({ killSwitchActive: active }),
  setActiveEnv: (env) => set({ activeEnv: env }),
}))
