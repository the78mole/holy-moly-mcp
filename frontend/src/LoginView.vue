<script setup>
import { ref, computed } from 'vue'
import { auth, loginOidc, loginLocal } from './auth.js'

const username = ref('')
const password = ref('')
const busy = ref(false)
const showLocal = ref(false)

const hasOidc = computed(() => Boolean(auth.config?.oidc))
const hasLocal = computed(() => Boolean(auth.config?.local))
// Local form is expanded by default when it is the only method.
const localOpen = computed(() => hasLocal.value && (!hasOidc.value || showLocal.value))

async function submitLocal() {
  if (!username.value || !password.value || busy.value) return
  busy.value = true
  try {
    const ok = await loginLocal(username.value, password.value)
    if (ok) password.value = ''
  } finally {
    busy.value = false
  }
}

async function startOidc() {
  busy.value = true
  try { await loginOidc() } finally { busy.value = false }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-card__brand">⚡ Holy Moly</div>
      <h1 class="login-card__title">Anmelden</h1>

      <p v-if="auth.message" class="login-card__message">{{ auth.message }}</p>

      <button v-if="hasOidc" class="login-btn login-btn--primary" :disabled="busy" @click="startOidc">
        Mit Keycloak anmelden
      </button>

      <template v-if="hasLocal">
        <button
          v-if="hasOidc && !showLocal"
          class="login-btn login-btn--ghost"
          type="button"
          @click="showLocal = true"
        >
          Lokales Konto verwenden
        </button>

        <form v-if="localOpen" class="login-form" @submit.prevent="submitLocal">
          <div v-if="hasOidc" class="login-form__divider">lokales Konto</div>
          <label class="login-form__label">
            Benutzername
            <input v-model.trim="username" type="text" autocomplete="username" autofocus required />
          </label>
          <label class="login-form__label">
            Passwort
            <input v-model="password" type="password" autocomplete="current-password" required />
          </label>
          <button class="login-btn login-btn--primary" type="submit" :disabled="busy || !username || !password">
            {{ busy ? 'Anmelden …' : 'Anmelden' }}
          </button>
        </form>
      </template>

      <p v-if="!hasOidc && !hasLocal" class="login-card__message">
        Auf diesem Server ist keine Anmeldemethode konfiguriert. Bitte den Administrator kontaktieren.
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #09091a;
  color: #e9e9ee;
  font-family: Inter, system-ui, sans-serif;
  padding: 1rem;
}

.login-card {
  width: 100%;
  max-width: 360px;
  background: #0f1024;
  border: 1px solid #181b35;
  border-radius: 14px;
  padding: 1.75rem 1.6rem 1.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}

.login-card__brand {
  font-size: 0.85rem;
  font-weight: 700;
  color: #a5b4fc;
  letter-spacing: 0.02em;
}

.login-card__title {
  margin: 0 0 0.4rem;
  font-size: 1.3rem;
  font-weight: 600;
  color: #d0d4ff;
}

.login-card__message {
  margin: 0;
  padding: 0.55rem 0.7rem;
  border-radius: 8px;
  background: #1c1530;
  border: 1px solid #3b2a5c;
  color: #e0c4ff;
  font-size: 0.84rem;
}

.login-btn {
  width: 100%;
  padding: 0.6rem 0.9rem;
  border-radius: 9px;
  font-size: 0.9rem;
  font-family: inherit;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.login-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.login-btn--primary {
  background: #4f46e5;
  color: #fff;
}

.login-btn--primary:hover:not(:disabled) {
  background: #6366f1;
}

.login-btn--ghost {
  background: none;
  color: #8b8fb0;
  border-color: #23264a;
}

.login-btn--ghost:hover:not(:disabled) {
  color: #c7cbe8;
  border-color: #3a3f6e;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.login-form__divider {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  color: #5c6080;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.login-form__divider::before,
.login-form__divider::after {
  content: '';
  flex: 1;
  border-top: 1px solid #181b35;
}

.login-form__label {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: 0.8rem;
  color: #8b8fb0;
}

.login-form__label input {
  padding: 0.55rem 0.7rem;
  border-radius: 8px;
  border: 1px solid #23264a;
  background: #0b0c1b;
  color: #e9e9ee;
  font-size: 0.92rem;
  font-family: inherit;
}

.login-form__label input:focus {
  outline: none;
  border-color: #6366f1;
}
</style>
