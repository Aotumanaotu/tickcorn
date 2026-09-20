import { reactive } from 'vue'

export type ToastKind = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

const toasts = reactive<ToastItem[]>([])

let nextId = 1
const DURATION_MS = 3_500

function push(kind: ToastKind, message: string): void {
  const id = nextId++
  toasts.push({ id, kind, message })
  setTimeout(() => dismiss(id), DURATION_MS)
}

function dismiss(id: number): void {
  const idx = toasts.findIndex((t) => t.id === id)
  if (idx !== -1) toasts.splice(idx, 1)
}

export const appToast = {
  toasts,
  dismiss,
  success(message: string): void {
    push('success', message)
  },
  error(message: string): void {
    push('error', message)
  },
  info(message: string): void {
    push('info', message)
  },
}
