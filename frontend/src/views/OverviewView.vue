<script setup lang="ts">
import { stateLabel } from '@/labels'
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

import EmptyState from '@/components/common/EmptyState.vue'
import MetricCard from '@/components/common/MetricCard.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import InstrumentName from '@/components/market/InstrumentName.vue'
import PriceText from '@/components/market/PriceText.vue'
import { useInstrumentsStore } from '@/stores/instruments'
import { useRealtimeStore } from '@/stores/realtime'
import { useSystemStore } from '@/stores/system'

const router = useRouter()
const system = useSystemStore()
const instruments = useInstrumentsStore()
const realtime = useRealtimeStore()

const REFRESH_MS = 3_000
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  void system.refreshStatus(true)
  void instruments.loadWatchlists()
  timer = setInterval(() => void system.refreshStatus(true), REFRESH_MS)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

// --- status cards -----------------------------------------------------------

const gatewayState = computed(() => system.gatewayState)
const gatewayStateDot = computed<'ok' | 'warn' | 'error' | 'idle'>(() => {
  if (!system.gatewayConnected) return system.gatewayState === 'offline' ? 'idle' : 'warn'
  return system.isLive ? 'ok' : 'warn'
})

const gatewaySub = computed(() => {
  const count = Object.keys(system.gateway?.instruments ?? {}).length ?? 0
  const events = system.gateway?.events_published
  const parts: string[] = []
  if (count) parts.push(`已订阅 ${count} 个合约`)
  if (events !== undefined) parts.push(`${events.toLocaleString('zh-CN')} 条事件`)
  return parts.join(' · ') || '暂无订阅'
})

const ingestSub = computed(() => {
  const events = system.status?.ingest.events_received
  if (events === undefined) return '—'
  return `已接收 ${events.toLocaleString('zh-CN')} 条事件`
})

const lastEventAgo = computed(() => {
  const iso = system.status?.ingest.last_event_at
  if (!iso) return '暂无事件'
  const ms = Date.now() - iso * 1000
  if (Number.isNaN(ms) || ms < 0) return '刚刚'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s} 秒前`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分钟前`
  return `${Math.floor(m / 60)} 小时前`
})

const wsDot = computed<'ok' | 'warn' | 'idle'>(() =>
  realtime.marketState === 'open' ? 'ok' : realtime.marketState === 'idle' ? 'idle' : 'warn',
)

const wsSub = computed(
  () =>
    `行情：${stateLabel(realtime.marketState)} · 分析：${stateLabel(realtime.analysisState)} · ${realtime.quotes.size} 条报价`,
)

// --- watchlist -----------------------------------------------------------------

const watchItems = computed(() =>
  (instruments.primaryWatchlist?.items ?? [])
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order),
)

function openInstrument(id: string): void {
  router.push({ name: 'workspace', params: { instrumentId: id } })
}
</script>

<template>
  <PageContainer
    title="总览"
    :subtitle="`系统状态、自选合约与研究动态 · 服务版本 v${system.version || '—'}`"
  >
    <div class="workflow">
      <router-link class="btn btn-primary" to="/system">CTP 采集控制</router-link>
      <router-link class="btn" to="/microstructure">查看 tick 跳变</router-link>
      <router-link class="btn" to="/research">历史数据与报告导出</router-link>
      <router-link class="btn" to="/monitor">飞书运行简报</router-link>
    </div>
    <div class="metrics">
      <MetricCard
        label="行情网关"
        :value="stateLabel(gatewayState)"
        :sub="gatewaySub"
        :state="gatewayStateDot"
      />
      <MetricCard
        label="数据库"
        :value="system.dbOk ? '正常' : '异常'"
        :state="system.dbOk ? 'ok' : 'error'"
        sub="PostgreSQL · 行情与事件"
      />
      <MetricCard
        label="实时连接"
        :value="realtime.marketState === 'open' ? '已连接' : '未连接'"
        :sub="wsSub"
        :state="wsDot"
      />
      <MetricCard
        label="数据接收"
        :value="(system.status?.ingest.events_received ?? 0).toLocaleString('zh-CN')"
        :sub="`${ingestSub} · ${lastEventAgo}`"
        :state="system.status?.ingest.last_event_at ? 'ok' : 'idle'"
      />
    </div>

    <div class="grid">
      <div class="card">
        <div class="card-title">
          <span>自选合约</span>
          <router-link class="manage" :to="{ name: 'markets' }">管理 →</router-link>
        </div>
        <div v-if="watchItems.length" class="watch-list">
          <button
            v-for="item in watchItems"
            :key="item.instrument_id"
            class="watch-row"
            type="button"
            @click="openInstrument(item.instrument_id)"
          >
            <InstrumentName :instrument-id="item.instrument_id" />
            <PriceText
              :value="realtime.quoteFor(item.instrument_id)?.data.last ?? null"
              tone="auto"
              :previous="
                realtime.quoteFor(item.instrument_id)?.data.pre_close ?? null
              "
              size="sm"
            />
          </button>
        </div>
        <EmptyState
          v-else
          title="尚未添加自选合约"
          hint="前往行情市场，将关注的合约加入自选。"
        >
          <router-link class="btn btn-primary" :to="{ name: 'markets' }">
            浏览行情市场
          </router-link>
        </EmptyState>
      </div>

      <div class="grid-col">
        <div class="card"><div class="card-title">研究流程</div><div class="card-body">
          <p>① 连接 CTP 并记录原始 tick 快照</p><p>② 观察价格阶梯跳变、反弹比例与真实报价移动</p>
          <p>③ 停止采集并归档，按合约、交易日和来源生成报告</p><p>④ 导出统计、特征与图表，积累后续量化模型研究数据</p>
        </div></div>
        <div class="card"><div class="card-title">统计口径</div><div class="card-body">
          <p>实时比例使用最近 200 次转换中，最新价变化恰好为 1 tick 的子集。完整历史统计以归档报告为准。</p>
          <p>本地模拟和 SimNow 测试数据用于联调；各来源分别分析。</p>
        </div></div>
      </div>
    </div>
  </PageContainer>
</template>

<style scoped>
.workflow { display: flex; gap: 10px; flex-wrap: wrap; }
.card-body p { margin-bottom: 12px; line-height: 1.7; }
.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
}

.grid-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.manage {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: none;
}

.watch-list {
  display: flex;
  flex-direction: column;
  max-height: 320px;
  overflow-y: auto;
}

.watch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background-color var(--dur-fast) ease;
  text-align: left;
}

.watch-list .watch-row:last-child {
  border-bottom: 0;
}

.watch-row:hover {
  background: var(--hover-layer);
}

@media (max-width: 1100px) {
  .metrics {
    grid-template-columns: repeat(2, 1fr);
  }

  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
