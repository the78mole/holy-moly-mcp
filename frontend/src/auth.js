/**
 * Authentication for the Holy Moly web UI.
 *
 * Two login methods, decided at runtime from GET /api/v1/auth/config (public):
 *   - OIDC against Keycloak via oidc-client-ts (Authorization Code + PKCE, silent renew)
 *   - a local account (username/password) exchanged for a short-lived token at POST /api/v1/auth/login
 *
 * Both end in a bearer token. `apiFetch` attaches it to every API call and `sseStream` replaces
 * `EventSource`, which cannot send headers. Tokens live in sessionStorage (tab-bound).
 * See docs/auth-oidc-and-tokens.md, section 8.
 */
import { reactive, readonly } from 'vue'
import { UserManager, WebStorageStateStore } from 'oidc-client-ts'

export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || ''

const LOCAL_SESSION_KEY = 'holy-moly.local-session'
const CALLBACK_PATH = '/callback'

const state = reactive({
  /** loading | anonymous | authenticated | disabled */
  status: 'loading',
  /** oidc | local | null */
  method: null,
  /** { name, roles } once authenticated */
  user: null,
  /** server-side auth config: { oidc: {realm_url, client_id} | null, local: bool, disabled: bool } */
  config: null,
  /** message for the login view (expired session, wrong password, ...) */
  message: '',
})

export const auth = readonly(state)

let userManager = null

// ── helpers ─────────────────────────────────────────────────────────────────

function decodeClaims(token) {
  // Display only. The server verifies signatures; the browser just reads the payload.
  try {
    const payload = token.split('.')[1]
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(payload.length / 4) * 4, '='))
    return JSON.parse(json)
  } catch {
    return {}
  }
}

function userFromToken(token) {
  const claims = decodeClaims(token)
  return {
    name: claims.preferred_username || claims.name || claims.sub || 'unbekannt',
    roles: claims.realm_access?.roles ?? [],
  }
}

function readLocalSession() {
  try {
    const raw = sessionStorage.getItem(LOCAL_SESSION_KEY)
    if (!raw) return null
    const session = JSON.parse(raw)
    if (!session.token || !session.exp || session.exp * 1000 <= Date.now() + 5000) {
      sessionStorage.removeItem(LOCAL_SESSION_KEY)
      return null
    }
    return session
  } catch {
    return null
  }
}

function setAuthenticated(method, token) {
  state.method = method
  state.user = userFromToken(token)
  state.status = 'authenticated'
  state.message = ''
}

function setAnonymous(message = '') {
  state.method = null
  state.user = null
  state.status = 'anonymous'
  state.message = message
}

// ── OIDC ────────────────────────────────────────────────────────────────────

function createUserManager(oidc) {
  const origin = window.location.origin
  const manager = new UserManager({
    authority: oidc.realm_url,
    client_id: oidc.client_id,
    redirect_uri: `${origin}${CALLBACK_PATH}`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: 'code',
    scope: 'openid profile email',
    automaticSilentRenew: true,
    loadUserInfo: false,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  })
  manager.events.addUserLoaded((user) => setAuthenticated('oidc', user.access_token))
  manager.events.addAccessTokenExpired(() => {
    if (state.method === 'oidc') setAnonymous('Die Sitzung ist abgelaufen. Bitte erneut anmelden.')
  })
  manager.events.addUserSignedOut(() => {
    manager.removeUser()
    if (state.method === 'oidc') setAnonymous('Abgemeldet.')
  })
  return manager
}

function isOidcCallback() {
  const params = new URLSearchParams(window.location.search)
  return params.has('state') && (params.has('code') || params.has('error'))
}

// ── public API ──────────────────────────────────────────────────────────────

/** Load the server config, finish a pending OIDC redirect, restore an existing session. */
export async function initAuth() {
  let config
  try {
    const res = await fetch(`${apiBaseUrl}/api/v1/auth/config`)
    config = res.ok ? await res.json() : { oidc: null, local: false, disabled: false }
  } catch {
    config = { oidc: null, local: false, disabled: false }
  }
  state.config = config

  if (config.disabled) {
    state.status = 'disabled'
    state.user = { name: 'dev', roles: ['holy-moly-admin', 'holy-moly-user'] }
    return
  }

  if (config.oidc) {
    userManager = createUserManager(config.oidc)
    if (isOidcCallback()) {
      try {
        const user = await userManager.signinRedirectCallback()
        setAuthenticated('oidc', user.access_token)
      } catch (err) {
        setAnonymous(`Keycloak-Anmeldung fehlgeschlagen: ${err?.message ?? err}`)
      } finally {
        window.history.replaceState({}, '', '/')
      }
      return
    }
    const user = await userManager.getUser()
    if (user && !user.expired) {
      setAuthenticated('oidc', user.access_token)
      return
    }
  }

  const local = readLocalSession()
  if (local) {
    setAuthenticated('local', local.token)
    return
  }
  setAnonymous()
}

export async function loginOidc() {
  if (!userManager) return
  state.message = ''
  await userManager.signinRedirect()
}

export async function loginLocal(username, password) {
  state.message = ''
  let res
  try {
    res = await fetch(`${apiBaseUrl}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
  } catch {
    state.message = 'Server nicht erreichbar.'
    return false
  }
  if (res.status === 401) { state.message = 'Benutzername oder Passwort ist falsch.'; return false }
  if (res.status === 429) { state.message = 'Zu viele Fehlversuche. Bitte später erneut versuchen.'; return false }
  if (!res.ok) { state.message = `Anmeldung fehlgeschlagen (HTTP ${res.status}).`; return false }
  const data = await res.json()
  const claims = decodeClaims(data.access_token)
  sessionStorage.setItem(LOCAL_SESSION_KEY, JSON.stringify({ token: data.access_token, exp: claims.exp }))
  setAuthenticated('local', data.access_token)
  return true
}

export async function logout() {
  const method = state.method
  sessionStorage.removeItem(LOCAL_SESSION_KEY)
  setAnonymous('Abgemeldet.')
  if (method === 'oidc' && userManager) {
    try {
      await userManager.signoutRedirect()
    } catch {
      await userManager.removeUser()
    }
  }
}

/** Current bearer token or null. Tries a silent OIDC renew once when the token is expired. */
export async function getAccessToken() {
  if (state.status === 'disabled') return null
  if (state.method === 'oidc' && userManager) {
    let user = await userManager.getUser()
    if (user && user.expired) {
      try { user = await userManager.signinSilent() } catch { user = null }
    }
    if (user && !user.expired) return user.access_token
    setAnonymous('Die Sitzung ist abgelaufen. Bitte erneut anmelden.')
    return null
  }
  if (state.method === 'local') {
    const session = readLocalSession()
    if (session) return session.token
    setAnonymous('Die Sitzung ist abgelaufen. Bitte erneut anmelden.')
    return null
  }
  return null
}

/** fetch() against the API with the bearer token attached; a 401 ends the session. */
export async function apiFetch(path, init = {}) {
  const headers = new Headers(init.headers || {})
  const token = await getAccessToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${apiBaseUrl}${path}`, { ...init, headers })
  if (res.status === 401 && state.status === 'authenticated') {
    sessionStorage.removeItem(LOCAL_SESSION_KEY)
    if (state.method === 'oidc' && userManager) await userManager.removeUser()
    setAnonymous('Die Sitzung ist abgelaufen. Bitte erneut anmelden.')
  }
  return res
}

/**
 * Server-Sent Events over fetch, because EventSource cannot carry an Authorization header.
 * Calls onMessage(dataString) per event, onError(err) on failure (not on a clean end).
 * Returns { close() }.
 */
export function sseStream(path, onMessage, onError) {
  const controller = new AbortController()
  ;(async () => {
    try {
      const res = await apiFetch(path, { signal: controller.signal, headers: { Accept: 'text/event-stream' } })
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let idx
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const chunk = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          const data = chunk
            .split('\n')
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trimStart())
            .join('\n')
          if (data) onMessage(data)
        }
      }
    } catch (err) {
      if (!controller.signal.aborted) onError?.(err)
    }
  })()
  return { close: () => controller.abort() }
}

export function hasRole(role) {
  return state.user?.roles?.includes(role) ?? false
}
