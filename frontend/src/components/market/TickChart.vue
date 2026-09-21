<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
  type WhitespaceData,
} from 'lightweight-charts'
import { marketApi } from '@/api/client'
import type { TickRow } from '@/api/types'
import EmptyState from '@/components/common/EmptyState.vue'
import { useRealtimeStore } from '@/stores/realtime'
import { useThemeStore } from '@/stores/theme'

/**
 * LWC realtime price chart: last / bid1 / ask1 lines + OBI histogram.
 *
 * Seeded from GET /market/ticks (recent window), then kept live from the
 * market websocket. Points are conflated to 1-second granularity: sub-second
 * snapshots replace the current second's point (LWC `update` semantics), so
 * the time axis stays strictly ascending.
 */

const props = withDefaults(
  defineProps<{
    instrumentId: string
    decimals?: number
    tickSize?: number | null
  }>(),
  { decimals: 1, tickSize: null },
)

const realtime = useRealtimeStore()
const theme = useThemeStore()

const el = ref<HTMLElement | null>(null)
const loading = ref(false)
const loadError = ref('')
const liveCount = ref(0)
const historyCount = ref(0)

const HISTORY_WINDOW_MS = 2 * 60 * 60 * 1000
const HISTORY_LIMIT = 5000
const EMPTY_ID = '__empty__'

interface Point {
  last: number | null
  bid1: number | null
  ask1: number | null
  obi1: number | null
}

let chart: IChartApi | null = null
let lastSeries: ISeriesApi<'Line'> | null = null
let bidSeries: ISeriesApi<'Line'> | null = null
let askSeries: ISeriesApi<'Line'> | null = null
let obiSeries: ISeriesApi<'Histogram'> | null = null
let lastTimeSec = 0
let loadToken = 0

const activeId = computed(() => props.instrumentId || EMPTY_ID)
const isEmpty = computed(() => !props.instrumentId)

const priceFormat = computed(() => ({
  type: 'price' as const,
  precision: props.decimals,
  minMove:
    props.tickSize && props.tickSize > 0
      ? props.tickSize
      : Number((10 ** -props.decimals).toFixed(props.decimals)),
}))

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function themeOptions() {
  return {
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: cssVar('--text-2'),
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: cssVar('--border') },
      horzLines: { color: cssVar('--border') },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: cssVar('--border') },
    timeScale: {
      borderColor: cssVar('--border'),
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 4,
    },
  }
}

function linePoint(p: Point, t: number, key: 'last' | 'bid1' | 'ask1'):
  LineData | WhitespaceData {
  const v = p[key]
  const time = t as Time
  return v == null ? { time } : { time, value: v }
}

function obiColor(v: number): string {
  return v >= 0 ? cssVar('--up') : cssVar('--down')
}

function obiPoint(p: Point, t: number): HistogramData | WhitespaceData {
  const time = t as Time
  return p.obi1 == null ? { time } : { time, value: p.obi1, color: obiColor(p.obi1) }
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function rowTimeSec(row: TickRow): number {
  if (typeof row.exchange_ts_ns === 'number' && row.exchange_ts_ns > 0) {
    return Math.floor(row.exchange_ts_ns / 1e9)
  }
  if (typeof row.local_ts_ns === 'number' && row.local_ts_ns > 0) {
    return Math.floor(row.local_ts_ns / 1e9)
  }
  if (row.ts) {
    const ms = Date.parse(row.ts)
    if (!Number.isNaN(ms)) return Math.floor(ms / 1000)
  }
  return 0
}

function applySnapshot(rows: TickRow[]): void {
  const merged = new Map<number, Point>()
  for (const row of rows) {
    const t = rowTimeSec(row)
    if (!t) continue
    merged.set(t, {
      last: num(row.last),
      bid1: num(row.bid1),
      ask1: num(row.ask1),
      obi1: num(row.obi1),
    })
  }
  const times = [...merged.keys()].sort((a, b) => a - b)
  lastSeries?.setData(times.map((t) => linePoint(merged.get(t)!, t, 'last')))
  bidSeries?.setData(times.map((t) => linePoint(merged.get(t)!, t, 'bid1')))
  askSeries?.setData(times.map((t) => linePoint(merged.get(t)!, t, 'ask1')))
  obiSeries?.setData(times.map((t) => obiPoint(merged.get(t)!, t)))
  lastTimeSec = times.length ? times[times.length - 1] : 0
  historyCount.value = times.length
  if (times.length) chart?.timeScale().fitContent()
}

async function loadHistory(): Promise<void> {
  const id = props.instrumentId
  if (!id || !chart) return
  const token = ++loadToken
  loading.value = true
  loadError.value = ''
  try {
    const fromNs = (BigInt(Date.now() - HISTORY_WINDOW_MS) * 1_000_000n).toString()
    const rows = await marketApi.ticks(id, { from_ns: fromNs, limit: HISTORY_LIMIT })
    if (token !== loadToken || id !== props.instrumentId) return
    applySnapshot(rows)
    seedCurrentQuote()
  } catch (err) {
    if (token === loadToken) {
      loadError.value = err instanceof Error ? err.message : '历史行情加载失败'
    }
  } finally {
    if (token === loadToken) loading.value = false
  }
}

function seedCurrentQuote(): void {
  const q = realtime.quoteFor(props.instrumentId)
  if (q) pushQuote(q.exchangeTsNs, q.receivedAt, q.data)
}

function pushQuote(
  exchangeTsNs: number | null,
  receivedAt: number,
  data: Record<string, unknown>,
): void {
  if (!chart || !props.instrumentId) return
  const tSec = Math.floor((exchangeTsNs ?? receivedAt * 1e6) / 1e9)
  if (!tSec || tSec < lastTimeSec) return
  lastTimeSec = tSec
  const time = tSec as Time
  const last = num(data.last)
  const bid1 = num(data.bid1)
  const ask1 = num(data.ask1)
  const obi1 = num(data.obi1)
  if (last != null) lastSeries?.update({ time, value: last })
  if (bid1 != null) bidSeries?.update({ time, value: bid1 })
  if (ask1 != null) askSeries?.update({ time, value: ask1 })
  if (obi1 != null) obiSeries?.update({ time, value: obi1, color: obiColor(obi1) })
  liveCount.value += 1
}

function resetSeries(): void {
  loadToken += 1
  lastTimeSec = 0
  liveCount.value = 0
  historyCount.value = 0
  loadError.value = ''
  lastSeries?.setData([])
  bidSeries?.setData([])
  askSeries?.setData([])
  obiSeries?.setData([])
}

onMounted(() => {
  if (!el.value) return
  chart = createChart(el.value, {
    ...themeOptions(),
    autoSize: true,
    height: 320,
    localization: { locale: 'zh-CN' },
  })
  const fmt = priceFormat.value
  lastSeries = chart.addLineSeries({
    color: cssVar('--accent'),
    lineWidth: 2,
    title: '最新价',
    priceFormat: fmt,
  })
  bidSeries = chart.addLineSeries({
    color: cssVar('--up'),
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    title: '买一',
    lastValueVisible: false,
    priceLineVisible: false,
    priceFormat: fmt,
    crosshairMarkerVisible: false,
  })
  askSeries = chart.addLineSeries({
    color: cssVar('--down'),
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    title: '卖一',
    lastValueVisible: false,
    priceLineVisible: false,
    priceFormat: fmt,
    crosshairMarkerVisible: false,
  })
  obiSeries = chart.addHistogramSeries({
    priceScaleId: 'obi',
    lastValueVisible: false,
    priceLineVisible: false,
    priceFormat: { type: 'volume' },
  })
  obiSeries.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } })
  if (props.instrumentId) void loadHistory()
})

onUnmounted(() => {
  loadToken += 1
  chart?.remove()
  chart = null
  lastSeries = bidSeries = askSeries = null
  obiSeries = null
})

watch(activeId, () => {
  resetSeries()
  if (props.instrumentId) void loadHistory()
})

watch(priceFormat, (fmt) => {
  lastSeries?.applyOptions({ priceFormat: fmt })
  bidSeries?.applyOptions({ priceFormat: fmt })
  askSeries?.applyOptions({ priceFormat: fmt })
})

watch(() => theme.theme, () => {
  chart?.applyOptions(themeOptions())
  lastSeries?.applyOptions({ color: cssVar('--accent') })
  bidSeries?.applyOptions({ color: cssVar('--up') })
  askSeries?.applyOptions({ color: cssVar('--down') })
})

watch(
  () => realtime.quoteFor(props.instrumentId),
  (q) => {
    if (q) pushQuote(q.exchangeTsNs, q.receivedAt, q.data as Record<string, unknown>)
  },
)
</script>

<template>
  <div class="tick-chart">
    <div class="chart-head">
      <div class="legend">
        <span class="lg" style="color: var(--accent)">━ 最新价</span>
        <span class="lg" style="color: var(--up)">╌ 买一</span>
        <span class="lg" style="color: var(--down)">╌ 卖一</span>
        <span class="lg obi">▮ 买卖失衡（买方为绿）</span>
      </div>
      <span v-if="loading" class="dim">正在加载历史…</span>
      <span v-else-if="loadError" class="err">{{ loadError }}</span>
      <span v-else-if="historyCount" class="dim">历史 {{ historyCount }} 点 · 实时 {{ liveCount }}</span>
    </div>
    <div class="chart-wrap">
      <div ref="el" class="chart-el" :aria-label="`${instrumentId} 价格与买卖报价实时图`"></div>
      <div v-if="isEmpty || (!historyCount && !liveCount && !loading)" class="overlay">
        <EmptyState
          :title="isEmpty ? '尚未选择合约' : '等待行情数据'"
          hint="图表在加载近 2 小时历史后随实时快照更新；若无数据请确认网关已连接并订阅该合约。"
          icon="chart"
        />
      </div>
    </div>
    <p class="foot">
      横轴为交易所时间（快照按秒合并显示）；下方柱状为一档买卖量失衡 OBI=(Vb−Va)/(Vb+Va)。
      完整原始记录保存在不可变归档中。
    </p>
  </div>
</template>

<style scoped>
.tick-chart {
  padding: 12px 16px 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.chart-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.legend {
  display: flex;
  gap: 16px;
  font-size: 12px;
  flex-wrap: wrap;
}

.lg.obi {
  color: var(--text-2);
}

.chart-wrap {
  position: relative;
  min-height: 320px;
  flex: 1;
}

.chart-el {
  width: 100%;
  height: 100%;
  min-height: 320px;
}

.overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--card) 55%, transparent);
  pointer-events: none;
}

.dim {
  color: var(--text-2);
  font-size: 12px;
}

.err {
  color: var(--down);
  font-size: 12px;
}

.foot {
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.6;
}
</style>
