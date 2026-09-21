<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { marketApi } from '@/api/client'
import type { MicroEventRow } from '@/api/types'
import EmptyState from '@/components/common/EmptyState.vue'
import { eventLabel } from '@/labels'
import { useRealtimeStore } from '@/stores/realtime'

/**
 * Event timeline: live microstructure events merged with recent DB history
 * (GET /market/events), with label-family filter chips.
 */

const props = defineProps<{ instrumentId: string }>()

const realtime = useRealtimeStore()

type FilterKey = 'all' | 'bounce' | 'genuine' | 'no_move' | 'ambiguous'

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'bounce', label: '反弹' },
  { key: 'genuine', label: '真实移动' },
  { key: 'no_move', label: '无变化' },
  { key: 'ambiguous', label: '不确定' },
]

const HISTORY_WINDOW_MS = 6 * 60 * 60 * 1000
const HISTORY_LIMIT = 5000
const DISPLAY_CAP = 400

const filter = ref<FilterKey>('all')
const history = ref<MicroEventRow[]>([])
const historyState = ref<'idle' | 'loading' | 'done' | 'error'>('idle')

interface RowView {
  ts: number
  label: string
  dl: number | null
  db: number | null
  da: number | null
  spread: number | null
  seq: number | null
  live: boolean
}

function family(label: string): FilterKey {
  if (label.includes('BOUNCE')) return 'bounce'
  if (label.startsWith('GENUINE')) return 'genuine'
  if (label === 'NO_MOVE') return 'no_move'
  if (label === 'AMBIGUOUS') return 'ambiguous'
  return 'all'
}

const liveEvents = computed(() =>
  realtime.microRing.filter((e) => e.instrument_id === props.instrumentId),
)

function histTs(row: MicroEventRow): number {
  if (typeof row.exchange_ts_ns === 'number' && row.exchange_ts_ns > 0) return row.exchange_ts_ns
  if (typeof row.local_ts_ns === 'number' && row.local_ts_ns > 0) return row.local_ts_ns
  if (row.ts) {
    const ms = Date.parse(row.ts)
    if (!Number.isNaN(ms)) return ms * 1e6
  }
  return 0
}

function liveTs(e: { exchange_ts_ns: number | null; local_ts_ns: number | null }): number {
  return e.exchange_ts_ns ?? e.local_ts_ns ?? 0
}

const merged = computed<RowView[]>(() => {
  const histRows: RowView[] = history.value
    .filter((r) => r.instrument_id === props.instrumentId && r.label)
    .map((r) => ({
      ts: histTs(r),
      label: r.label ?? '',
      dl: r.dl_ticks,
      db: r.db_ticks,
      da: r.da_ticks,
      spread: r.spread_cur_ticks,
      seq: r.seq,
      live: false,
    }))
  const cutoff = histRows.reduce((m, r) => Math.max(m, r.ts), 0)
  const liveRows: RowView[] = liveEvents.value
    .filter((e) => {
      const data = e.data as Record<string, unknown>
      return liveTs(e) > cutoff && typeof data.label === 'string' && data.label
    })
    .map((e) => {
      const data = e.data as Record<string, unknown>
      const n = (v: unknown): number | null =>
        typeof v === 'number' && Number.isFinite(v) ? v : null
      return {
        ts: liveTs(e),
        label: String(data.label),
        dl: n(data.dl_ticks),
        db: n(data.db_ticks),
        da: n(data.da_ticks),
        spread: n(data.spread_cur_ticks),
        seq: e.seq ?? null,
        live: true,
      }
    })
  return [...histRows, ...liveRows].reverse()
})

const counts = computed<Record<FilterKey, number>>(() => {
  const c: Record<FilterKey, number> = {
    all: 0, bounce: 0, genuine: 0, no_move: 0, ambiguous: 0,
  }
  for (const r of merged.value) {
    c.all += 1
    c[family(r.label)] += 1
  }
  return c
})

const filtered = computed(() =>
  filter.value === 'all' ? merged.value : merged.value.filter((r) => family(r.label) === filter.value),
)

const displayed = computed(() => filtered.value.slice(0, DISPLAY_CAP))

async function loadHistory(): Promise<void> {
  const id = props.instrumentId
  if (!id) return
  historyState.value = 'loading'
  try {
    const fromNs = (BigInt(Date.now() - HISTORY_WINDOW_MS) * 1_000_000n).toString()
    const rows = await marketApi.events(id, { from_ns: fromNs, limit: HISTORY_LIMIT })
    if (id !== props.instrumentId) return
    history.value = rows
    historyState.value = 'done'
  } catch {
    if (id === props.instrumentId) historyState.value = 'error'
  }
}

function time(ns: number): string {
  return ns ? new Date(ns / 1e6).toLocaleTimeString('zh-CN', { hour12: false }) : '—'
}

function fmt(v: number | null): string {
  return v == null ? '—' : `${v > 0 ? '+' : ''}${v}`
}

onMounted(() => {
  if (props.instrumentId) void loadHistory()
})

watch(
  () => props.instrumentId,
  (next) => {
    filter.value = 'all'
    history.value = []
    historyState.value = 'idle'
    if (next) void loadHistory()
  },
)
</script>

<template>
  <div class="timeline">
    <div class="toolbar">
      <div class="chips" role="group" aria-label="事件分类筛选">
        <button
          v-for="f in FILTERS"
          :key="f.key"
          type="button"
          class="chip"
          :class="{ active: filter === f.key }"
          @click="filter = f.key"
        >
          {{ f.label }} <span class="cnt num">{{ counts[f.key] }}</span>
        </button>
      </div>
      <div class="status">
        <span v-if="historyState === 'loading'" class="dim">正在加载历史…</span>
        <span v-else-if="historyState === 'error'" class="err">历史加载失败（仅显示实时事件）</span>
        <span v-else-if="historyState === 'done'" class="dim">历史 {{ history.length }} 条 · 近 6 小时</span>
      </div>
    </div>

    <div v-if="displayed.length" class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>时间</th><th>事件分类</th><th>ΔLast（tick）</th><th>ΔBid / ΔAsk（tick）</th><th>价差（tick）</th><th>序号</th><th>来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in displayed" :key="`${e.seq ?? e.ts}-${i}`">
            <td class="mono">{{ time(e.ts) }}</td>
            <td>{{ eventLabel(e.label) }}</td>
            <td class="num">{{ fmt(e.dl) }}</td>
            <td class="num dim">{{ fmt(e.db) }} / {{ fmt(e.da) }}</td>
            <td class="num">{{ e.spread ?? '—' }}</td>
            <td class="num dim">{{ e.seq ?? '—' }}</td>
            <td><span class="src" :class="{ live: e.live }">{{ e.live ? '实时' : '库' }}</span></td>
          </tr>
        </tbody>
      </table>
      <p v-if="filtered.length > DISPLAY_CAP" class="dim cap">
        仅显示最近 {{ DISPLAY_CAP }} 条（共 {{ filtered.length }} 条匹配）。
      </p>
    </div>
    <EmptyState
      v-else
      title="暂无分类事件"
      hint="收到连续有效报价后，此处合并显示数据库历史与实时分类事件。"
    />
  </div>
</template>

<style scoped>
.timeline {
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.chip {
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-2);
  border-radius: 999px;
  padding: 3px 12px;
  font-size: 12px;
  cursor: pointer;
  transition: all var(--dur-fast) ease;
}

.chip:hover {
  color: var(--text);
  border-color: var(--text-2);
}

.chip.active {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-dim);
}

.cnt {
  font-size: 11px;
  opacity: 0.75;
}

.status {
  font-size: 12px;
}

.dim {
  color: var(--text-2);
}

.err {
  color: var(--down);
  font-size: 12px;
}

.table-wrap {
  overflow: auto;
  max-height: 380px;
}

.cap {
  font-size: 12px;
  padding: 6px 0 0;
}

.src {
  font-size: 11px;
  color: var(--text-2);
}

.src.live {
  color: var(--accent);
}
</style>
