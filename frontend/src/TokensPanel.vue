<script setup>
import { onMounted, ref } from 'vue'
import { apiFetch, auth, hasRole } from './auth.js'

const tokens = ref([])
const loading = ref(false)
const error = ref('')
const name = ref('')
const expiresInDays = ref('')
const adminOnly = ref(false)
const creating = ref(false)
const newToken = ref(null)   // { token, name } shown once
const copied = ref(false)
const origin = window.location.origin

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await apiFetch('/api/v1/auth/tokens')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    tokens.value = await res.json()
  } catch (e) {
    error.value = `Tokens konnten nicht geladen werden (${e.message}).`
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!name.value.trim() || creating.value) return
  creating.value = true
  error.value = ''
  newToken.value = null
  try {
    const body = { name: name.value.trim() }
    const days = parseInt(expiresInDays.value, 10)
    if (days > 0) body.expires_in_days = days
    if (adminOnly.value === false && hasRole('holy-moly-admin')) body.roles = ['holy-moly-user']
    const res = await apiFetch('/api/v1/auth/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    newToken.value = { token: data.token, name: data.name }
    name.value = ''
    expiresInDays.value = ''
    await load()
  } catch (e) {
    error.value = `Token konnte nicht erstellt werden: ${e.message}`
  } finally {
    creating.value = false
  }
}

async function revoke(t) {
  if (!confirm(`Token „${t.name}" (${t.prefix}…) widerrufen? Clients, die es verwenden, verlieren sofort den Zugriff.`)) return
  const res = await apiFetch(`/api/v1/auth/tokens/${t.id}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 204) error.value = `Widerrufen fehlgeschlagen (HTTP ${res.status}).`
  await load()
}

async function purge(t) {
  const res = await apiFetch(`/api/v1/auth/tokens/${t.id}?purge=true`, { method: 'DELETE' })
  if (!res.ok && res.status !== 204) error.value = `Löschen fehlgeschlagen (HTTP ${res.status}).`
  await load()
}

async function copyToken() {
  if (!newToken.value) return
  await navigator.clipboard.writeText(newToken.value.token)
  copied.value = true
  setTimeout(() => { copied.value = false }, 1500)
}

function fmt(ts) {
  if (!ts) return '–'
  return new Date(ts * 1000).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' })
}

onMounted(load)
</script>

<template>
  <div class="tokens">
    <div class="section-header">
      <h1>API-Tokens</h1>
      <p>
        Persönliche Zugangstoken für Claude Code, VS Code, den Remote-Proxy oder Skripte. Jedes Token
        handelt mit deinen Rechten; der Klartext wird nur einmal angezeigt.
      </p>
    </div>

    <div v-if="auth.status !== 'authenticated'" class="tokens__note">
      Im Entwicklungsmodus ohne Anmeldung können keine Tokens verwaltet werden.
    </div>

    <template v-else>
      <form class="tokens__form" @submit.prevent="create">
        <label class="tokens__field">
          Name
          <input v-model="name" type="text" maxlength="100" placeholder="z. B. VS Code Laptop" required />
        </label>
        <label class="tokens__field tokens__field--short">
          Gültig (Tage)
          <input v-model="expiresInDays" type="number" min="1" max="3650" placeholder="unbegrenzt" />
        </label>
        <label v-if="hasRole('holy-moly-admin')" class="tokens__check">
          <input v-model="adminOnly" type="checkbox" /> mit Admin-Rechten
        </label>
        <button class="btn-load" type="submit" :disabled="creating || !name.trim()">
          <span v-if="creating" class="spinner" /> Token erstellen
        </button>
      </form>

      <div v-if="newToken" class="tokens__new">
        <div class="tokens__new-title">Neues Token „{{ newToken.name }}" – jetzt kopieren, es wird nicht noch einmal angezeigt.</div>
        <div class="tokens__new-row">
          <code class="tokens__new-value">{{ newToken.token }}</code>
          <button class="btn-load" type="button" @click="copyToken">{{ copied ? 'Kopiert ✓' : 'Kopieren' }}</button>
        </div>
        <div class="tokens__new-hint">
          Claude Code: <code>claude mcp add --transport http holy-moly {{ origin }}/mcp-server/mcp --header "Authorization: Bearer &lt;Token&gt;"</code>
        </div>
      </div>

      <p v-if="error" class="tokens__error">{{ error }}</p>

      <table v-if="tokens.length" class="tokens__table">
        <thead>
          <tr><th>Name</th><th>Präfix</th><th>Rollen</th><th>Erstellt</th><th>Läuft ab</th><th>Zuletzt benutzt</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="t in tokens" :key="t.id" :class="{ 'tokens__row--inactive': !t.active }">
            <td>{{ t.name }}</td>
            <td><code>{{ t.prefix }}…</code></td>
            <td>{{ t.roles.map(r => r.replace('holy-moly-', '')).join(', ') }}</td>
            <td>{{ fmt(t.created_at) }}</td>
            <td>{{ t.expires_at ? fmt(t.expires_at) : 'nie' }}</td>
            <td>{{ fmt(t.last_used_at) }}</td>
            <td>{{ t.active ? 'aktiv' : (t.revoked_at ? 'widerrufen' : 'abgelaufen') }}</td>
            <td class="tokens__actions">
              <button v-if="t.active" class="tokens__link" type="button" @click="revoke(t)">Widerrufen</button>
              <button v-else class="tokens__link" type="button" @click="purge(t)">Entfernen</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!loading" class="tokens__note">Noch keine Tokens.</p>
    </template>
  </div>
</template>

<style scoped>
.tokens { max-width: 1000px; }
.tokens__form { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-end; margin-bottom: 1rem; }
.tokens__field { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.8rem; color: #8b8fb0; flex: 1 1 220px; }
.tokens__field--short { flex: 0 1 140px; }
.tokens__field input {
  padding: 0.5rem 0.65rem; border-radius: 8px; border: 1px solid #23264a; background: #0b0c1b;
  color: #e9e9ee; font-size: 0.9rem; font-family: inherit;
}
.tokens__field input:focus { outline: none; border-color: #6366f1; }
.tokens__check { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; color: #8b8fb0; padding-bottom: 0.55rem; }
.tokens__new {
  background: #10162b; border: 1px solid #2d3a6e; border-radius: 10px; padding: 0.9rem 1rem; margin-bottom: 1rem;
  display: flex; flex-direction: column; gap: 0.55rem;
}
.tokens__new-title { font-size: 0.85rem; color: #c7cbe8; }
.tokens__new-row { display: flex; gap: 0.6rem; align-items: center; flex-wrap: wrap; }
.tokens__new-value {
  flex: 1 1 320px; padding: 0.5rem 0.65rem; background: #060715; border-radius: 8px; color: #a5b4fc;
  font-size: 0.85rem; word-break: break-all; user-select: all;
}
.tokens__new-hint { font-size: 0.76rem; color: #5c6080; word-break: break-all; }
.tokens__new-hint code { color: #8b8fb0; }
.tokens__error { color: #f87171; font-size: 0.85rem; }
.tokens__note { color: #5c6080; font-size: 0.85rem; }
.tokens__table { width: 100%; border-collapse: collapse; font-size: 0.83rem; }
.tokens__table th, .tokens__table td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #181b35; }
.tokens__table th { color: #5c6080; font-weight: 500; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
.tokens__row--inactive td { color: #5c6080; }
.tokens__actions { text-align: right; }
.tokens__link { background: none; border: none; color: #a5b4fc; cursor: pointer; font-family: inherit; font-size: 0.8rem; padding: 0; }
.tokens__link:hover { text-decoration: underline; }
</style>
