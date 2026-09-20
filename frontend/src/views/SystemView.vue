<script setup lang="ts">
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

const gatewayConnected = computed(() => system.gatewayConnected)
const gatewayState = computed(() => system.gatewayState)

const gatewayStateDot = computed<'ok' | 'warn' | 'error' | 'idle'>(() => {
  if (!system.gatewayConnected) return gatewayState.value === 'offline' ? 'idle' : 'warn'
  return system.isLive ? 'ok' : 'warn'
})

const gatewayInstruments = computed(
  () => system.gateway?.instruments?.join(', ') || '—',
)

const form = reactive({
  fronts: 'tcp://180.168.146.187:10131\ntcp://180.168.146.187:10111',
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
    appToast.error('All gateway fields except "remember" are required.')
    return
  }
  connecting.value = true
  try {
    await system.connectGateway({
      fronts,
      broker_id: form.broker_id,
      user: form.user,
      password: form.password,
      instruments,
      remember: form.remember,
    })
    appToast.success('Gateway connect command sent.')
    form.password = ''
  } catch (err) {
    appToast.error(err instanceof Error ? err.message : 'Gateway connect failed')
  } finally {
    connecting.value = false
  }
}

async function disconnect(): Promise<void> {
  disconnecting.value = true
  try {
    await system.disconnectGateway()
    appToast.info('Gateway disconnect command sent.')
  } catch (err) {
    appToast.error(err instanceof Error ? err.message : 'Gateway disconnect failed')
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
    appToast.error(err instanceof Error ? err.message : 'Failed to load users')
  }
}

async function createUser(): Promise<void> {
  if (!userForm.username.trim() || userForm.password.length < 6) {
    appToast.error('Username and a password of at least 6 characters are required.')
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
    appToast.success(`User "${created.username}" created.`)
    userForm.username = ''
    userForm.password = ''
  } catch (err) {
    appToast.error(err instanceof Error ? err.message : 'Failed to create user')
  } finally {
    creating.value = false
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}
</script>

<template>
  <PageContainer
    title="System"
    :subtitle="`Gateway control and account administration · backend v${system.version || '—'}`"
  >
    <div class="layout">
      <div class="col">
        <div class="card">
          <div class="card-title">
            <span>CTP Gateway</span>
            <div class="title-right">
              <ConnectionBadge
                v-if="system.simulate"
                label="SIM"
                tone="warn"
              />
              <StatusDot :state="gatewayStateDot" :label="gatewayState" />
            </div>
          </div>
          <div class="card-body gw-body">
            <dl class="kv num">
              <div class="row"><dt>Ingest link</dt><dd>{{ system.gatewayConnected ? 'connected' : 'down' }}</dd></div>
              <div class="row"><dt>Events published</dt><dd>{{ (system.gateway?.events_published ?? 0).toLocaleString() }}</dd></div>
              <div class="row"><dt>Batch ID</dt><dd class="mono">{{ system.gateway?.batch_id ?? '—' }}</dd></div>
              <div class="row instruments"><dt>Subscribed</dt><dd class="mono">{{ gatewayInstruments }}</dd></div>
            </dl>

            <div v-if="gatewayConnected" class="connected-row">
              <button
                class="btn btn-danger"
                type="button"
                :disabled="disconnecting"
                @click="disconnect"
              >
                {{ disconnecting ? 'Disconnecting…' : 'Disconnect' }}
              </button>
            </div>

            <template v-else>
              <div v-if="canManageGateway">
                <hr class="divider form-divider" />
                <form class="gw-form" @submit.prevent="connect">
                  <div class="field">
                    <label for="gw-fronts">Front addresses (one per line)</label>
                    <textarea id="gw-fronts" v-model="form.fronts" class="textarea" rows="3" />
                  </div>
                  <div class="two-col">
                    <div class="field">
                      <label for="gw-broker">Broker ID</label>
                      <input id="gw-broker" v-model="form.broker_id" class="input" type="text" placeholder="9999" />
                    </div>
                    <div class="field">
                      <label for="gw-user">User</label>
                      <input id="gw-user" v-model="form.user" class="input" type="text" autocomplete="off" />
                    </div>
                  </div>
                  <div class="field">
                    <label for="gw-password">Password</label>
                    <input id="gw-password" v-model="form.password" class="input" type="password" autocomplete="new-password" />
                  </div>
                  <div class="field">
                    <label for="gw-instruments">Instruments (comma separated)</label>
                    <input id="gw-instruments" v-model="form.instruments" class="input mono" type="text" placeholder="c2611, m2601" />
                  </div>
                  <label class="checkbox">
                    <input v-model="form.remember" type="checkbox" />
                    Remember credentials (stored server-side)
                  </label>
                  <button class="btn btn-primary" type="submit" :disabled="connecting">
                    {{ connecting ? 'Connecting…' : 'Connect Gateway' }}
                  </button>
                </form>
              </div>
              <div v-else class="no-perm">
                <EmptyState
                  icon="lock"
                  title="Insufficient permissions"
                  hint="Only administrators can control the CTP gateway (MANAGE_USER required)."
                />
              </div>
            </template>
          </div>
        </div>
      </div>

      <div class="col">
        <div class="card">
          <div class="card-title"><span>Users</span></div>
          <template v-if="canManageUsers">
            <div v-if="users.length" class="table-wrap">
              <table class="table">
                <thead>
                  <tr><th>User</th><th>Role</th><th>Status</th><th>Created</th></tr>
                </thead>
                <tbody>
                  <tr v-for="u in users" :key="u.id">
                    <td>
                      <span class="username">{{ u.username }}</span>
                      <span v-if="u.id === auth.user?.id" class="you badge">you</span>
                    </td>
                    <td><span class="badge" :class="u.role === 'ADMIN' ? 'badge-accent' : ''">{{ u.role }}</span></td>
                    <td>
                      <StatusDot :state="u.is_active ? 'ok' : 'idle'" :label="u.is_active ? 'active' : 'disabled'" />
                    </td>
                    <td class="dim num">{{ formatDate(u.created_at) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <EmptyState
              v-else-if="usersLoaded"
              title="No users yet"
              hint="Create the first account with the form below."
            />

            <div class="card-body">
              <form class="user-form" @submit.prevent="createUser">
                <div class="three-col">
                  <div class="field">
                    <label for="nu-username">Username</label>
                    <input id="nu-username" v-model="userForm.username" class="input" type="text" autocomplete="off" />
                  </div>
                  <div class="field">
                    <label for="nu-password">Password</label>
                    <input id="nu-password" v-model="userForm.password" class="input" type="password" autocomplete="new-password" placeholder="min 6 chars" />
                  </div>
                  <div class="field">
                    <label for="nu-role">Role</label>
                    <select id="nu-role" v-model="userForm.role" class="select">
                      <option v-for="r in ROLE_OPTIONS" :key="r" :value="r">{{ r }}</option>
                    </select>
                  </div>
                </div>
                <button class="btn btn-primary" type="submit" :disabled="creating">
                  {{ creating ? 'Creating…' : 'Create User' }}
                </button>
              </form>
            </div>
          </template>
          <EmptyState
            v-else
            icon="lock"
            title="Insufficient permissions"
            hint="User administration requires the MANAGE_USER permission (admin role)."
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
