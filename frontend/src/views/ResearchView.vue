<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { researchApi } from '@/api/client'
import type { ArchivePartition, ReportJob } from '@/api/types'
import { appToast } from '@/components/common/appToast'
import EmptyState from '@/components/common/EmptyState.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import { useAuthStore } from '@/stores/auth'
import { useSystemStore } from '@/stores/system'

const auth = useAuthStore()
const system = useSystemStore()
const partitions = ref<ArchivePartition[]>([])
const sources = ref<Record<string, string>>({})
const jobs = ref<ReportJob[]>([])
const selected = ref('')
const source = ref('')
const busy = ref(false)
const submitting = ref(false)
const loading = ref(true)
const error = ref('')
const downloading = ref('')
const canReport = computed(() => auth.can('GENERATE_REPORT'))
const partition = computed(() => partitions.value.find(p => `${p.instrument}/${p.day}` === selected.value))
const collecting = computed(() => Boolean(system.gateway?.batch_id))
let timer: ReturnType<typeof setInterval> | undefined
const statusLabels: Record<string, string> = { running: '生成中', ready: '可下载', failed: '生成失败', interrupted: '生成已中断' }
async function refresh() {
  try {
    const archive = await researchApi.archives()
    partitions.value = archive.partitions
    sources.value = archive.sources
    if (canReport.value) {
      const reports = await researchApi.reports()
      jobs.value = reports.jobs
      busy.value = reports.busy
    }
    error.value = ''
  } catch (err) { error.value = err instanceof Error ? err.message : '加载数据失败' }
  finally { loading.value = false }
}
async function generate() {
  if (!partition.value || !source.value) return
  submitting.value = true
  try {
    await researchApi.generate({ instrument: partition.value.instrument, day: partition.value.day, source: source.value })
    appToast.success('报告已开始生成，完成后可下载。')
    await refresh()
  } catch (err) { appToast.error(err instanceof Error ? err.message : '报告生成失败') }
  finally { submitting.value = false }
}
async function download(job: ReportJob, kind: string) {
  downloading.value = `${job.id}/${kind}`
  try {
    const blob = await researchApi.download(job.id, kind)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${job.instrument}_${job.day}_${job.source}_${job.id}.${kind}`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) { appToast.error(err instanceof Error ? err.message : '下载失败') }
  finally { downloading.value = '' }
}
onMounted(() => { void refresh(); timer = setInterval(refresh, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>
<template>
  <PageContainer title="数据与报告" subtitle="从原始 tick 归档生成跳变统计、条件概率、特征分析与可复现报告">
    <template #actions><button class="btn" @click="refresh">刷新数据</button></template>
    <p v-if="error" class="notice error">{{ error }}</p>
    <p class="notice">请先在“采集与设置”中停止采集并等待归档完成，再生成报告。各来源独立分析，旧版未标记环境的 CTP 数据选择“CTP 旧数据”。</p>
    <div class="layout">
      <div class="card">
        <div class="card-title">原始数据归档 <span>{{ partitions.length }} 个分区</span></div>
        <div class="table-wrap" v-if="partitions.length"><table class="table">
          <thead><tr><th>合约</th><th>交易日</th><th>归档状态</th><th></th></tr></thead>
          <tbody><tr v-for="p in partitions" :key="`${p.instrument}/${p.day}`">
            <td class="mono">{{ p.instrument }}</td><td>{{ p.day }}</td>
            <td>{{ p.staging ? '有待归档分片' : '已归档' }}</td>
            <td><button class="btn btn-sm" @click="selected = `${p.instrument}/${p.day}`">选择</button></td>
          </tr></tbody>
        </table></div>
        <EmptyState v-else :title="loading ? '正在读取归档…' : '暂无原始数据'" hint="连接 CTP 开始记录，数据落盘后会显示在这里。旧数据若位于旧 Docker 卷中，需先迁移到当前共享数据卷。" />
      </div>
      <div class="card"><div class="card-title">生成分析报告</div>
        <form v-if="canReport" class="card-body form" @submit.prevent="generate">
          <div class="field"><label for="report-partition">合约与交易日</label><select id="report-partition" v-model="selected" class="select" required>
            <option value="" disabled>选择已有归档</option><option v-for="p in partitions" :key="`${p.instrument}/${p.day}`" :value="`${p.instrument}/${p.day}`">{{ p.instrument }} · {{ p.day }}</option>
          </select></div>
          <div class="field"><label for="report-source">数据来源</label><select id="report-source" v-model="source" class="select" required>
            <option value="" disabled>请选择实际采集来源</option><option v-for="(label, value) in sources" :key="value" :value="value">{{ label }}</option>
          </select></div>
          <p class="notice">报告包含反弹 / 真实报价移动占比、状态转移矩阵、订单簿不平衡条件概率、事件前特征与价格图。ZIP 包含 CSV、图表和来源元数据。</p>
          <button class="btn btn-primary" :disabled="submitting || busy || collecting || !partition || partition.staging || !source">{{ busy || submitting ? '报告生成中…' : collecting ? '请先停止采集并归档' : '生成分析报告' }}</button>
        </form>
        <EmptyState v-else title="需要研究权限" hint="研究员或管理员可以生成并下载分析报告。" icon="lock" />
      </div>
    </div>
    <div v-if="canReport" class="card"><div class="card-title">报告与导出</div>
      <div class="table-wrap" v-if="jobs.length"><table class="table"><thead><tr><th>合约 / 交易日</th><th>来源</th><th>状态</th><th>快照数</th><th>生成时间</th><th>下载</th></tr></thead>
        <tbody><tr v-for="job in jobs" :key="job.id">
          <td>{{ job.instrument }} / {{ job.day }}</td><td>{{ sources[job.source] || job.source }}</td>
          <td>{{ statusLabels[job.status] || job.status }}<p v-if="job.error" class="error">{{ job.error }}</p></td>
          <td>{{ job.snapshots?.toLocaleString('zh-CN') ?? '—' }}</td><td>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</td>
          <td><div v-if="job.status === 'ready'" class="downloads"><button v-for="kind in ['html', 'md', 'zip']" :key="kind" class="btn btn-sm" :disabled="Boolean(downloading)" @click="download(job, kind)">{{ kind === 'html' ? '网页报告' : kind === 'md' ? '文本文档' : '完整 ZIP' }}</button></div></td>
        </tr></tbody></table></div>
      <EmptyState v-else title="暂无报告" hint="选择合约、交易日和实际来源后生成报告。" />
    </div>
  </PageContainer>
</template>
<style scoped>
.layout { display: grid; grid-template-columns: 1.1fr 1fr; gap: 16px; align-items: start; }
.form { display: flex; flex-direction: column; gap: 14px; }
.notice { font-size: 13px; color: var(--text-2); line-height: 1.7; }
.error { color: var(--down); }
.table-wrap { overflow-x: auto; max-height: 440px; }
.downloads { display: flex; gap: 6px; }
@media (max-width: 1000px) { .layout { grid-template-columns: 1fr; } }
</style>
