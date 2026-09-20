<script setup lang="ts">
import { roleLabel, stateLabel } from '@/labels'
import { computed, onMounted, reactive, ref } from 'vue'

import { appToast } from '@/components/common/appToast'
import ConnectionBadge from '@/components/common/ConnectionBadge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import StatusDot from '@/components/common/StatusDot.vue'
import type { User } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { useSystemStore } from '@/stores/system'

const auth = useAuthStore()
const system = useSystemStore()

const canManageGateway = computed(() => auth.can('MANAGE_USER'))
const canManageUsers = computed(() => auth.can('MANAGE_USER'))

// --- gateway ----------------------------------------------------------------

const gatewayConnected = computed(() => Boolean(system.gateway?.batch_id) || ['connecting', 'connected', 'streaming'].includes(system.gatewayState))
const gatewayState = computed(() => system.gatewayState)

const gatewayStateDot = computed<'ok' | 'warn' | 'error' | 'idle'>(() => {
  if (!system.gatewayConnected) return gatewayState.value === 'offline' ? 'idle' : 'warn'
  return system.isLive ? 'ok' : 'warn'
})

const gatewayInstruments = computed(
  () => Object.keys(system.gateway?.instruments ?? {}).join(', ') || '—',
)

const form = reactive({
  fronts: '',
  source_kind: 'simnow_standard',
  broker_id: '',
  user: '',
  password: '',
  instruments: 'c2611',
  remember: false,
})

const connecting = ref(false)
const disconnecting = ref(false)

async function connect(): Promise<void> {
  const fronts = form.fronts
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  const instruments = form.instruments
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (!fronts.length || !form.broker_id || !form.user || !form.password || !instruments.length) {
    appToast.error('请完整填写行情前置地址、经纪商代码、账号、密码和订阅合约。')
    return
  }
  connecting.value = true
  try {
    await system.connectGateway({
      fronts,
      source_kind: form.source_kind,
      broker_id: form.broker_id,
      user: form.user,
      password: form.password,
      instruments,
      remember: form.remember,
    })
    appToast.success('已发送网关连接指令。')
    form.password = ''
  } catch (err) {
    appToast.error(err instanceof Error ? err.message : '网关连接失败')
  } finally {
    connecting.value = false
  }
}

async function disconnect(): Promise<void> {
  disconnecting.value = true
  try {
    await system.disconnectGateway()
    appToast.info('已发送网关断开指令。')
  } catch (err) {
    appToast.error(err instanceof Error ? err.message : '网关断开失败')
  } finally {
    disconnecting.value = false
  }
}

// --- users --------------------------------------------------------------------

const users = ref<User[]>([])
const usersLoaded = ref(false)
const creating = ref(false)
const userForm = reactive({ username: '', password: '', role: 'VIEWER' })

const ROLE_OPTIONS = ['ADMIN', 'RESEARCHER', 'TRADER', 'VIEWER']

onMounted(() => {
  void system.refreshStatus(true)
  if (canManageUsers.value) void loadUsers()
})

async function loadUsers(): Promise<void> {
  try {
    const { systemApi } = await import('@/api/client')
    users.value = await systemApi.users()
    usersLoaded.value = true
  } catch (err) {
    appToast.error(err instanceof Error ? err.message : '加载用户列表失败')
  }
}

async function createUser(): Promise<void> {
  if (!userForm.username.trim() || userForm.password.length < 6) {
    appToast.error('请填写用户名，并设置至少 6 位的密码。')
    return
  }
  creating.value = true
  try {
    const { systemApi } = await import('@/api/client')
    const created = await systemApi.createUser({
      username: userForm.username.trim(),
      password: userForm.password,
      role: userForm.role,
    })
    users.value = [...users.value, created].sort((a, b) => a.id - b.id)
    appToast.success(`用户“${created.username}”已创建。`)
    userForm.username = ''
    userForm.password = ''
  } catch (err) {
    appToast.error(err instanceof Error ? err.message : '创建用户失败')
  } finally {
    creating.value = false
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN')
  } catch {
    return iso
  }
}
</script>

<template>
  <PageContainer
    title="系统管理"
    :subtitle="`行情网关与账号管理 · 服务版本 v${system.version || '—'}`"
  >
    <div class="layout">
      <div class="col">
        <div class="card">
          <div class="card-title">
            <span>CTP 行情网关</span>
            <div class="title-right">
              <ConnectionBadge
                v-if="system.simulate"
                label="模拟行情"
                tone="warn"
              />
              <StatusDot :state="gatewayStateDot" :label="stateLabel(gatewayState)" />
            </div>
          </div>
          <div class="card-body gw-body">
            <p v-if="system.gateway?.detail" class="dim">{{ system.gateway.detail }}</p>
            <dl class="kv num">
              <div class="row"><dt>数据接收连接</dt><dd>{{ system.gatewayConnected ? '已连接' : '未连接' }}</dd></div>
              <div class="row"><dt>已发布事件</dt><dd>{{ (system.gateway?.events_published ?? 0).toLocaleString('zh-CN') }}</dd></div>
              <div class="row"><dt>批次编号</dt><dd class="mono">{{ system.gateway?.batch_id ?? '—' }}</dd></div>
              <div class="row instruments"><dt>已订阅合约</dt><dd class="mono">{{ gatewayInstruments }}</dd></div>
            </dl>

            <div v-if="gatewayConnected && canManageGateway" class="connected-row">
              <button
                class="btn btn-danger"
                type="button"
                :disabled="disconnecting"
                @click="disconnect"
              >
                {{ disconnecting ? '正在断开…' : '停止采集并归档' }}
              </button>
            </div>

            <template v-else>
              <div v-if="canManageGateway">
                <hr class="divider form-divider" />
                <form class="gw-form" @submit.prevent="connect">
                  <p class="dim">连接后自动记录原始 tick 快照。停止采集时会整理归档，完成后可前往“数据与报告”分析导出。</p>
                  <div class="field">
                    <label for="gw-source">数据来源</label>
                    <select id="gw-source" v-model="form.source_kind" class="select">
                      <option value="simnow_standard">SimNow 标准仿真环境</option>
                      <option value="simnow_test">SimNow API 测试环境（仅联调）</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="gw-fronts">行情前置地址（每行一个）</label>
                    <textarea id="gw-fronts" v-model="form.fronts" class="textarea" rows="3" placeholder="填写 SimNow 官方页面提供的行情前置地址，格式 tcp://主机:端口" />
                  </div>
                  <div class="two-col">
                    <div class="field">
                      <label for="gw-broker">经纪商代码</label>
                      <input id="gw-broker" v-model="form.broker_id" class="input" type="text" placeholder="9999" />
                    </div>
                    <div class="field">
                      <label for="gw-user">账号</label>
                      <input id="gw-user" v-model="form.user" class="input" type="text" autocomplete="off" />
                    </div>
                  </div>
                  <div class="field">
                    <label for="gw-password">密码</label>
                    <input id="gw-password" v-model="form.password" class="input" type="password" autocomplete="new-password" />
                  </div>
                  <div class="field">
                    <label for="gw-instruments">订阅合约（以逗号分隔）</label>
                    <input id="gw-instruments" v-model="form.instruments" class="input mono" type="text" placeholder="c2611, m2601" />
                  </div>
                  <label class="checkbox">
                    <input v-model="form.remember" type="checkbox" />
                    记住凭据（明文保存在服务器）
                  </label>
                  <button class="btn btn-primary" type="submit" :disabled="connecting">
                    {{ connecting ? '正在连接…' : '连接并开始记录' }}
                  </button>
                </form>
              </div>
              <div v-else class="no-perm">
                <EmptyState
                  icon="lock"
                  title="权限不足"
                  hint="仅管理员可以控制 CTP 行情网关。"
                />
              </div>
            </template>
          </div>
        </div>
      </div>

      <div class="col">
        <div class="card">
          <div class="card-title"><span>用户管理</span></div>
          <template v-if="canManageUsers">
            <div v-if="users.length" class="table-wrap">
              <table class="table">
                <thead>
                  <tr><th>账号</th><th>角色</th><th>状态</th><th>创建时间</th></tr>
                </thead>
                <tbody>
                  <tr v-for="u in users" :key="u.id">
                    <td>
                      <span class="username">{{ u.username }}</span>
                      <span v-if="u.id === auth.user?.id" class="you badge">当前用户</span>
                    </td>
                    <td><span class="badge" :class="u.role === 'ADMIN' ? 'badge-accent' : ''">{{ roleLabel(u.role) }}</span></td>
                    <td>
                      <StatusDot :state="u.is_active ? 'ok' : 'idle'" :label="u.is_active ? '启用' : '停用'" />
                    </td>
                    <td class="dim num">{{ formatDate(u.created_at) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <EmptyState
              v-else-if="usersLoaded"
              title="暂无用户"
              hint="使用下方表单创建账号。"
            />

            <div class="card-body">
              <form class="user-form" @submit.prevent="createUser">
                <div class="three-col">
                  <div class="field">
                    <label for="nu-username">用户名</label>
                    <input id="nu-username" v-model="userForm.username" class="input" type="text" autocomplete="off" />
                  </div>
                  <div class="field">
                    <label for="nu-password">密码</label>
                    <input id="nu-password" v-model="userForm.password" class="input" type="password" autocomplete="new-password" placeholder="至少 6 位" />
                  </div>
                  <div class="field">
                    <label for="nu-role">角色</label>
                    <select id="nu-role" v-model="userForm.role" class="select">
                      <option v-for="r in ROLE_OPTIONS" :key="r" :value="r">{{ roleLabel(r) }}</option>
                    </select>
                  </div>
                </div>
                <button class="btn btn-primary" type="submit" :disabled="creating">
                  {{ creating ? '正在创建…' : '创建用户' }}
                </button>
              </form>
            </div>
          </template>
          <EmptyState
            v-else
            icon="lock"
            title="权限不足"
            hint="仅管理员可以管理用户账号。"
          />
        </div>
      </div>
    </div>
  </PageContainer>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}

.col {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.title-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.kv {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
}

.kv .row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.kv dt {
  color: var(--text-2);
}

.kv dd {
  margin: 0;
  text-align: right;
  overflow-wrap: anywhere;
}

.instruments dd {
  max-width: 340px;
  font-size: 12px;
  color: var(--text-2);
}

.connected-row {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

.form-divider {
  margin: 16px 0;
}

.gw-form,
.user-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.three-col {
  display: grid;
  grid-template-columns: 1.2fr 1.2fr 0.8fr;
  gap: 12px;
}

.no-perm {
  margin-top: 8px;
}

.table-wrap {
  overflow-x: auto;
}

.username {
  font-weight: 600;
}

.you {
  margin-left: 8px;
  font-size: 10px;
}

.dim {
  color: var(--text-2);
}

@media (max-width: 1000px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
