<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { monitorApi } from '@/api/client'
import type { MonitorSettings, MonitorWorker } from '@/api/types'
import { appToast } from '@/components/common/appToast'
import EmptyState from '@/components/common/EmptyState.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const allowed = computed(() => auth.can('MANAGE_USER'))
const form = reactive<MonitorSettings>({ enabled: false, feishu_app_id: '', feishu_app_secret: '', feishu_receive_id: '', feishu_receive_id_type: 'chat_id', report_times: [], alert_only: false, title: 'Tick 采集研究', tz: 'Asia/Shanghai' })
const times = ref('08:00,12:30,21:30')
const worker = ref<MonitorWorker>({})
const hasSecret = ref(false)
const loaded = ref(false)
const busy = ref(false)
const error = ref('')
const now = ref(Date.now())
const online = computed(() => now.value - (worker.value.heartbeat_at ?? 0) < 90000)
let timer: ReturnType<typeof setInterval> | undefined
async function load(initial = false) {
  try {
    const result = await monitorApi.settings()
    worker.value = result.worker
    now.value = Date.now()
    if (initial) {
      Object.assign(form, result.settings, { feishu_app_secret: '' })
      hasSecret.value = Boolean(result.settings.has_secret)
      times.value = result.settings.report_times.join(',')
      loaded.value = true
    }
    error.value = ''
  } catch (err) { error.value = err instanceof Error ? err.message : '读取配置失败' }
}
async function save() {
  busy.value = true
  try {
    await monitorApi.save({ ...form, report_times: times.value.replaceAll('，', ',').split(',').map(s => s.trim()).filter(Boolean) })
    await load(true)
    appToast.success('飞书配置已保存，推送服务将在下一次检查时应用。')
  } catch (err) { appToast.error(err instanceof Error ? err.message : '保存失败') }
  finally { busy.value = false }
}
async function test() {
  busy.value = true
  try { await monitorApi.test(); appToast.success('已提交测试发送，请查看下方最近结果（通常 20 秒内更新）。') }
  catch (err) { appToast.error(err instanceof Error ? err.message : '提交失败') }
  finally { busy.value = false }
}
onMounted(() => { if (allowed.value) { void load(true); timer = setInterval(() => load(), 10000) } })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>
<template>
  <PageContainer title="飞书推送" subtitle="定时发送采集状态、数据落盘、反弹比例和异常提醒">
    <EmptyState v-if="!allowed" title="仅管理员可配置飞书推送" icon="lock" />
    <template v-else>
      <p v-if="error" class="error">{{ error }}</p>
      <div class="card"><div class="card-title">推送服务状态 <span>{{ online ? '运行中' : '未检测到服务' }}</span></div>
        <div class="card-body"><p>最近结果：{{ worker.last_result || '暂无发送记录' }}</p><p>最近发送：{{ worker.last_sent_at ? new Date(worker.last_sent_at).toLocaleString('zh-CN') : '—' }}</p><p v-if="!online" class="hint">推送服务尚未就绪，请联系管理员检查部署。</p></div>
      </div>
      <form class="card" @submit.prevent="save"><div class="card-title">应用与发送设置</div><div class="card-body form">
        <label class="checkbox"><input v-model="form.enabled" type="checkbox" />启用定时推送</label>
        <div class="grid">
          <div class="field"><label for="fs-id">飞书应用 ID</label><input id="fs-id" v-model="form.feishu_app_id" class="input" autocomplete="off" /></div>
          <div class="field"><label for="fs-secret">飞书应用密钥</label><input id="fs-secret" v-model="form.feishu_app_secret" class="input" type="password" autocomplete="new-password" :placeholder="hasSecret ? '已保存，留空保留原密钥' : '填写应用密钥'" /></div>
          <div class="field"><label for="fs-kind">接收方类型</label><select id="fs-kind" v-model="form.feishu_receive_id_type" class="select"><option value="chat_id">群聊 ID</option><option value="open_id">用户 Open ID</option><option value="user_id">用户 ID</option><option value="union_id">用户 Union ID</option><option value="email">邮箱</option></select></div>
          <div class="field"><label for="fs-receiver">接收方 ID / 邮箱</label><input id="fs-receiver" v-model="form.feishu_receive_id" class="input" /></div>
          <div class="field"><label for="fs-times">每天发送时间（逗号分隔）</label><input id="fs-times" v-model="times" class="input" placeholder="08:00,12:30,21:30" /></div>
          <div class="field"><label for="fs-tz">发送时区</label><select id="fs-tz" v-model="form.tz" class="select"><option :value="form.tz" v-if="!['Asia/Shanghai','Asia/Singapore','UTC'].includes(form.tz)">{{ form.tz }}</option><option value="Asia/Shanghai">北京时间</option><option value="Asia/Singapore">新加坡时间</option><option value="UTC">协调世界时</option></select></div>
          <div class="field"><label for="fs-title">简报标题</label><input id="fs-title" v-model="form.title" class="input" /></div>
        </div>
        <label class="checkbox"><input v-model="form.alert_only" type="checkbox" />仅异常时发送定时简报</label>
        <p class="hint">请先保存再发送测试。应用需具备机器人消息权限，并能访问指定群聊或用户；应用密钥不会回显。</p>
        <div class="actions"><button class="btn btn-primary" :disabled="busy || !loaded">保存配置</button><button class="btn" type="button" :disabled="busy || !loaded || !online" @click="test">发送一条测试简报</button></div>
      </div></form>
    </template>
  </PageContainer>
</template>
<style scoped>
.form { display: flex; flex-direction: column; gap: 16px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.actions { display: flex; gap: 10px; }
p { line-height: 1.7; }
.hint { color: var(--text-2); font-size: 13px; }
.error { color: var(--down); }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
</style>
