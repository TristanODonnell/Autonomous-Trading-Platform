import {
  createRouter,
  createRootRoute,
  createRoute,
  RouterProvider,
} from '@tanstack/react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppShell from './layouts/AppShell'
import Dashboard from './pages/Dashboard'
import Portfolio from './pages/Portfolio'
import ExperimentLab from './pages/ExperimentLab'
import Controls from './pages/Controls'
import Settings from './pages/Settings'

const rootRoute = createRootRoute({ component: AppShell })

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: Dashboard })
const portfolioRoute = createRoute({ getParentRoute: () => rootRoute, path: '/portfolio', component: Portfolio })
const strategyRoute = createRoute({ getParentRoute: () => rootRoute, path: '/strategy', component: ExperimentLab })
const controlsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/controls', component: Controls })
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/settings', component: Settings })

const routeTree = rootRoute.addChildren([
  indexRoute,
  portfolioRoute,
  strategyRoute,
  controlsRoute,
  settingsRoute,
])

const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 2,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
