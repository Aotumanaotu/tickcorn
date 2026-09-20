<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { appToast } from '@/components/common/appToast'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const auth = useAuthStore()
const theme = useThemeStore()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const error = ref('')
const submitting = ref(false)

async function submit(): Promise<void> {
  if (!username.value.trim() || !password.value) {
    error.value = 'Please enter username and password.'
    return
  }
  submitting.value = true
  error.value = ''
  try {
    await auth.login(username.value.trim(), password.value)
    appToast.success(`Signed in as ${auth.user?.username}`)
    const redirect = (route.query.redirect as string) || '/overview'
    router.push(redirect)
  } catch (err) {
    error.value =
      err instanceof Error && err.message
        ? `Sign-in failed: ${err.message}`
        : 'Sign-in failed. Check your credentials.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <button
      class="theme-toggle icon-btn"
      type="button"
      :aria-label="theme.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'"
      @click="theme.toggle()"
    >
      <svg
        v-if="theme.theme === 'dark'"
        viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
      >
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
      <svg
        v-else
        viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
      >
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    </button>

    <div class="login-card card">
      <div class="brand">
        <svg class="brand-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M3 17l4-8 4 5 3-9 4 7" />
          <path d="M2 21h20" stroke-opacity=".45" />
        </svg>
        <h1 class="brand-name">MICROTERM</h1>
        <p class="brand-sub">Bid-Ask Bounce Research Terminal</p>
      </div>

      <form class="form" @submit.prevent="submit">
        <div class="field">
          <label for="login-username">Username</label>
          <input
            id="login-username"
            v-model="username"
            class="input"
            type="text"
            autocomplete="username"
            placeholder="e.g. admin"
            :disabled="submitting"
          />
        </div>
        <div class="field">
          <label for="login-password">Password</label>
          <input
            id="login-password"
            v-model="password"
            class="input"
            type="password"
            autocomplete="current-password"
            placeholder="••••••••"
            :disabled="submitting"
          />
        </div>

        <p v-if="error" class="error">{{ error }}</p>

        <button class="btn btn-primary submit" type="submit" :disabled="submitting">
          {{ submitting ? 'Signing in…' : 'Sign in' }}
        </button>
      </form>

      <p class="foot">MICROTERM v0.3.0 · CTP market data research build</p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  position: relative;
}

.theme-toggle {
  position: absolute;
  top: 20px;
  right: 20px;
  width: 34px;
  height: 34px;
  color: var(--text-2);
}

.login-card {
  width: 100%;
  max-width: 380px;
  padding: 32px;
  box-shadow: var(--shadow);
}

.brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  margin-bottom: 24px;
}

.brand-mark {
  width: 34px;
  height: 34px;
  color: var(--accent);
}

.brand-name {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.14em;
}

.brand-sub {
  font-size: 12px;
  color: var(--text-2);
}

.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.error {
  font-size: 12.5px;
  color: var(--down);
  background: var(--down-bg);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
}

.submit {
  margin-top: 4px;
  width: 100%;
  padding: 10px;
  font-size: 14px;
}

.foot {
  margin-top: 20px;
  text-align: center;
  font-size: 11px;
  color: var(--text-2);
}
</style>
