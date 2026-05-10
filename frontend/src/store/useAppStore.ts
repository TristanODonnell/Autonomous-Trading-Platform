import { create } from 'zustand'
import type { Environment } from '../types'

interface AppStore {
  activeEnv: Environment
  killSwitchActive: boolean
  toggleKillSwitch: () => void
  setActiveEnv: (env: Environment) => void
}

export const useAppStore = create<AppStore>((set) => ({
  activeEnv: 'paper',
  killSwitchActive: false,
  toggleKillSwitch: () => set((s) => ({ killSwitchActive: !s.killSwitchActive })),
  setActiveEnv: (env) => set({ activeEnv: env }),
}))
