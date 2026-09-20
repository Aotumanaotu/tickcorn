import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

/** Feature flags gate upcoming workstreams (sidebar + routes). */
export const FEATURES = {
  strategies: false,
  trading: false,
  models: false,
} as const

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: 'Sign in', public: true },
  },
  {
    path: '/overview',
    name: 'overview',
    component: () => import('@/views/OverviewView.vue'),
    meta: { title: 'Overview' },
  },
  {
    path: '/markets',
    name: 'markets',
    component: () => import('@/views/MarketsView.vue'),
    meta: { title: 'Markets' },
  },
  {
    path: '/workspace/:instrumentId',
    name: 'workspace',
    component: () => import('@/views/WorkspaceView.vue'),
    meta: { title: 'Workspace' },
  },
  {
    path: '/microstructure',
    name: 'microstructure',
    component: () => import('@/views/MicrostructureView.vue'),
    meta: { title: 'Microstructure' },
  },
  {
    path: '/research',
    name: 'research',
    component: () => import('@/views/ResearchView.vue'),
    meta: { title: 'Research' },
  },
  {
    path: '/system',
    name: 'system',
    component: () => import('@/views/SystemView.vue'),
    meta: { title: 'System' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: 'Not found' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.booted) await auth.boot()

  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: 'login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
  }
  if (to.name === 'login' && auth.isAuthenticated) {
    return { name: 'overview' }
  }
})

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) ?? 'MICROTERM'
  document.title = `${title} · MICROTERM`
})
