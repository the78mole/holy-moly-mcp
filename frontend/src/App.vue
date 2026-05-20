<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const file = ref(null)
const mode = ref('plain')
const loading = ref(false)
const output = ref('')
const error = ref('')
const activeSection = ref('speech')
const speakerRecognition = ref(false)
const audioTypes = '.mp3,.ogg,.wav,.m4a,.flac,.mp4'

// Model status (SSE)
const modelPhase = ref('idle')
const modelProgress = ref(0)
const modelMessage = ref('Model not started')

// Model selector
const availableModels = ref([])   // [{name, cached}]
const activeModel = ref('')
const selectedModel = ref('')
let eventSource = null

const uiText = {
  emptyDropLabel: 'Datei hier ablegen oder klicken, um eine Datei auszuwählen',
  selectedPrefix: 'Ausgewählt',
  pickFileError: 'Bitte zuerst eine Datei auswählen.',
  genericError: 'Konvertierung fehlgeschlagen.',
  unknownError: 'Unbekannter Fehler',
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || ''

const isPdf = computed(() => file.value?.name?.toLowerCase().endsWith('.pdf') ?? false)

const endpoint = computed(() =>
  isPdf.value
    ? `${apiBaseUrl}/api/v1/convert/pdf-to-markdown`
    : `${apiBaseUrl}/api/v1/convert/speech-to-text`,
)

const dropLabel = computed(() =>
  file.value ? `${uiText.selectedPrefix}: ${file.value.name}` : uiText.emptyDropLabel,
)

const modelReady = computed(() => modelPhase.value === 'ready')
const modelLoading = computed(() => modelPhase.value === 'loading')
const modelError = computed(() => modelPhase.value === 'error')

const progressBarStyle = computed(() => ({
  width: `${modelProgress.value}%`,
}))

// ── model metadata & system info ────────────────────────────────────────────

const deviceInfo = ref({ device: 'cpu', compute_type: 'int8' })
const systemInfo = ref(null)  // { cpu_count, cpu_model, gpus }

const selectedModelMeta = computed(() =>
  availableModels.value.find(m => m.name === selectedModel.value) ?? null)

const selectedModelSpeed = computed(() => {
  if (!selectedModelMeta.value) return null
  return deviceInfo.value.device === 'cpu'
    ? selectedModelMeta.value.cpu_speed
    : selectedModelMeta.value.gpu_speed
})

function formatSize(mb) {
  if (!mb) return '?'
  return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`
}

function speedClass(speed) {
  return {
    'badge--fast': speed === 'fast',
    'badge--medium': speed === 'medium',
    'badge--slow': speed === 'slow',
    'badge--very-slow': speed === 'very slow',
  }
}

function qualityClass(quality) {
  return {
    'badge--best': quality === 'best',
    'badge--excellent': quality === 'excellent',
    'badge--very-good': quality === 'very good',
    'badge--good': quality === 'good',
    'badge--fair': quality === 'fair',
  }
}

async function fetchModelInfo() {
  try {
    const res = await fetch(`${apiBaseUrl}/api/v1/model/info`)
    if (!res.ok) return
    const data = await res.json()
    availableModels.value = data.models ?? []
    activeModel.value = data.active ?? ''
    deviceInfo.value = { device: data.device ?? 'cpu', compute_type: data.compute_type ?? 'int8' }
    if (!selectedModel.value) selectedModel.value = data.active ?? ''
  } catch (_) {}
}

async function fetchSystemInfo() {
  try {
    const res = await fetch(`${apiBaseUrl}/api/v1/system/info`)
    if (!res.ok) return
    systemInfo.value = await res.json()
  } catch (_) {}
}

async function applyModelSelection() {
  if (!selectedModel.value || selectedModel.value === activeModel.value) return
  await fetch(`${apiBaseUrl}/api/v1/model/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: selectedModel.value }),
  })
  // reconnect SSE to track the new load progress
  eventSource?.close()
  eventSource = null
  connectModelStatus()
}

// ── SSE ─────────────────────────────────────────────────────────────────────

function connectModelStatus() {
  if (eventSource) return
  eventSource = new EventSource(`${apiBaseUrl}/api/v1/model/status`)
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      modelPhase.value = data.phase
      modelProgress.value = data.progress ?? 0
      modelMessage.value = data.message ?? ''
      if (data.model) activeModel.value = data.model
      if (data.phase === 'ready' || data.phase === 'error') {
        eventSource.close()
        eventSource = null
        fetchModelInfo()   // refresh cached-status after load completes
      }
    } catch (_) {}
  }
  eventSource.onerror = () => {
    if (modelPhase.value !== 'ready') {
      eventSource?.close()
      eventSource = null
      setTimeout(connectModelStatus, 2000)
    }
  }
}

onMounted(() => { fetchModelInfo(); fetchSystemInfo(); connectModelStatus() })
onUnmounted(() => { eventSource?.close() })

// ── file handling ────────────────────────────────────────────────────────────

function onDrop(event) {
  event.preventDefault()
  const dropped = event.dataTransfer?.files?.[0]
  if (dropped) { file.value = dropped; error.value = '' }
}

function onSelect(event) {
  const selected = event.target?.files?.[0]
  if (selected) { file.value = selected; error.value = '' }
}

// ── convert ──────────────────────────────────────────────────────────────────

async function convert() {
  if (!file.value) { error.value = uiText.pickFileError; return }
  loading.value = true
  error.value = ''
  output.value = ''
  try {
    const formData = new FormData()
    formData.append('file', file.value)
    const requestUrl = isPdf.value
      ? endpoint.value
      : `${endpoint.value}?mode=${encodeURIComponent(mode.value)}`
    const response = await fetch(requestUrl, { method: 'POST', body: formData })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || uiText.genericError)
    output.value = data.result || ''
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : uiText.unknownError
  } finally {
    loading.value = false
  }
}

async function copyOutput() {
  if (output.value) await navigator.clipboard.writeText(output.value)
}
</script>

<template>
  <div class="app-layout">
    <!-- ─────────────────────────── Sidebar ──────────────────────────────── -->
    <nav class="sidebar">
      <div class="sidebar__brand">⚡ Holy Moly</div>
      <div class="sidebar__nav">
        <button
          class="nav-item"
          :class="{ 'nav-item--active': activeSection === 'speech' }"
          @click="activeSection = 'speech'"
        >
          <span class="nav-item__icon">🎙</span> Speech
        </button>
        <button class="nav-item nav-item--disabled" disabled>
          <span class="nav-item__icon">🖼</span> Images
        </button>
        <button class="nav-item nav-item--disabled" disabled>
          <span class="nav-item__icon">📄</span> Documents
        </button>
      </div>
    </nav>

    <!-- ───────────────────────── Main content ───────────────────────────── -->
    <main class="main-content">

      <!-- ══ Speech ══════════════════════════════════════════════════════ -->
      <section v-if="activeSection === 'speech'">
        <div class="section-header">
          <h1>Speech</h1>
          <p>Sprachverarbeitung mit lokalem Whisper-Modell</p>
        </div>

        <!-- Model-Status-Banner -->
        <div v-if="!modelReady" class="model-banner" :class="{ 'model-banner--error': modelError }">
          <div class="model-banner__header">
            <span v-if="modelLoading" class="spinner" />
            <span v-if="modelError">⚠</span>
            <span class="model-banner__message">{{ modelMessage }}</span>
          </div>
          <div v-if="modelLoading" class="progress-track">
            <div class="progress-bar" :style="progressBarStyle" />
          </div>
        </div>

        <!-- Modell-Selektor -->
        <div class="model-row">
          <label class="model-row__label">
            Modell
            <div class="model-select-wrap">
              <select v-model="selectedModel">
                <option v-for="m in availableModels" :key="m.name" :value="m.name">
                  {{ m.name }}{{ m.name === activeModel ? ' ★' : '' }}
                </option>
              </select>
              <span
                class="model-dot"
                :class="{ 'model-dot--ready': selectedModelMeta?.cached }"
                :title="selectedModelMeta?.cached ? 'Modell im Cache' : 'Noch nicht heruntergeladen'"
              />
            </div>
          </label>
          <button
            v-if="selectedModel && selectedModel !== activeModel"
            class="btn-load"
            :class="{ 'btn-load--download': !selectedModelMeta?.cached }"
            :disabled="modelLoading"
            @click="applyModelSelection"
          >
            <span v-if="modelLoading" class="spinner" />
            {{ selectedModelMeta?.cached ? 'Aktivieren' : 'Herunterladen' }}
          </button>

          <!-- Debug: alle Modelle mit Cache-Status -->
          <div class="model-debug">
            <span
              v-for="m in availableModels"
              :key="m.name"
              class="model-chip"
              :class="{
                'model-chip--active': m.name === activeModel,
                'model-chip--cached': m.cached && m.name !== activeModel,
              }"
              :title="m.path"
            >{{ m.name }}</span>
          </div>
        </div>

        <!-- Modell-Infokarte -->
        <div v-if="selectedModelMeta" class="model-card">
          <div class="model-card__row">
            <span class="model-card__chip">{{ selectedModelMeta.params }} Params</span>
            <span class="model-card__chip">{{ formatSize(selectedModelMeta.size_int8_mb) }}</span>
            <span class="model-card__chip">{{ deviceInfo.compute_type }}</span>
          </div>
          <div class="model-card__row">
            <span class="model-card__label">Qualität</span>
            <span class="badge" :class="qualityClass(selectedModelMeta.quality)">
              {{ selectedModelMeta.quality }}&nbsp;(WER&nbsp;~{{ selectedModelMeta.wer_pct }}%)
            </span>
            <span class="model-card__sep">·</span>
            <span class="model-card__label">Geschwindigkeit</span>
            <span class="badge" :class="speedClass(selectedModelSpeed)">{{ selectedModelSpeed }}</span>
          </div>
          <div v-if="systemInfo" class="model-card__row model-card__hw">
            <span>{{ systemInfo.cpu_count }}× {{ systemInfo.cpu_model }}</span>
            <template v-if="systemInfo.gpus && systemInfo.gpus.length > 0">
              <span class="model-card__sep">·</span>
              <span v-for="gpu in systemInfo.gpus" :key="gpu.index">
                {{ gpu.name }} ({{ formatSize(gpu.memory_mb) }})
              </span>
            </template>
            <span class="model-card__sep">·</span>
            <span class="model-card__active">{{ deviceInfo.device }} · {{ deviceInfo.compute_type }}</span>
          </div>
        </div>

        <!-- Extension-Cards -->
        <div class="card-grid">

          <!-- ── Speech-to-Text ────────────────────────────────────── -->
          <div class="ext-card">
            <div class="ext-card__header">
              <span class="ext-card__icon">🎤</span>
              <h2 class="ext-card__title">Speech-to-Text</h2>
            </div>
            <div class="ext-card__body">
              <div class="stt-options">
                <div class="option-row">
                  <span class="option-label">Modus</span>
                  <div class="mode-segmented">
                    <button :class="{ active: mode === 'plain' }" @click="mode = 'plain'">Transkription</button>
                    <button :class="{ active: mode === 'minutes' }" @click="mode = 'minutes'">Protokoll</button>
                  </div>
                </div>
                <div class="option-row">
                  <span class="option-label">
                    Sprechererkennung
                    <span class="tag-soon">bald</span>
                  </span>
                  <label class="toggle">
                    <input type="checkbox" v-model="speakerRecognition" disabled />
                    <span class="toggle__slider" />
                  </label>
                </div>
              </div>

              <label class="dropzone" @dragover.prevent @drop="onDrop">
                <input type="file" :accept="audioTypes" @change="onSelect" />
                <span>{{ dropLabel }}</span>
              </label>

              <p v-if="error" class="error">{{ error }}</p>

              <button class="btn-convert" :disabled="loading || !modelReady" @click="convert">
                <span v-if="loading" class="spinner" />
                {{ loading ? 'Konvertiere…' : 'Konvertieren' }}
              </button>

              <section v-if="output" class="result">
                <div class="result-header">
                  <span>Ergebnis</span>
                  <button @click="copyOutput">Kopieren</button>
                </div>
                <pre>{{ output }}</pre>
              </section>
            </div>
          </div>

          <!-- ── Text-to-Speech ────────────────────────────────────── -->
          <div class="ext-card">
            <div class="ext-card__header">
              <span class="ext-card__icon">🔊</span>
              <h2 class="ext-card__title">Text-to-Speech</h2>
              <span class="ext-card__badge">Demnächst</span>
            </div>
            <div class="ext-card__body">
              <p class="tts-placeholder">
                Lorem ipsum dolor sit amet, consectetur adipiscing elit. Donec at purus non lectus
                rhoncus tempor. Futura functio Text-to-Speech textum scriptum in vocem artificialem
                convertit. Parametri inclusi erunt: selectio vocis, velocitas, altitudo et emphasis.
                Integratio cum modello locali TTS (e.g. Coqui, Bark) mox aderit.
              </p>
            </div>
          </div>

        </div>
      </section>

      <!-- ══ Images ══════════════════════════════════════════════════════ -->
      <section v-else-if="activeSection === 'images'">
        <div class="section-header">
          <h1>Images</h1>
          <p>Bildverarbeitung und -konvertierung</p>
        </div>
        <div class="coming-soon">
          <div class="coming-soon__icon">🖼</div>
          <p class="coming-soon__title">Noch nicht verfügbar</p>
          <p>Bildbearbeitung und -konvertierung kommt in einer zukünftigen Version.</p>
        </div>
      </section>

      <!-- ══ Documents ════════════════════════════════════════════════════ -->
      <section v-else-if="activeSection === 'documents'">
        <div class="section-header">
          <h1>Documents</h1>
          <p>Dokumentenverarbeitung und -konvertierung</p>
        </div>
        <div class="coming-soon">
          <div class="coming-soon__icon">📄</div>
          <p class="coming-soon__title">Noch nicht verfügbar</p>
          <p>PDF- und Dokumentenverarbeitung kommt in einer zukünftigen Version.</p>
        </div>
      </section>

    </main>
  </div>
</template>

<style scoped>
/* ── Layout ─────────────────────────────────────────────────────────────── */
.app-layout {
  display: flex;
  min-height: 100vh;
  font-family: Inter, system-ui, sans-serif;
  color: #e9e9ee;
  background: #09091a;
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
.sidebar {
  width: 190px;
  min-width: 190px;
  background: #060715;
  border-right: 1px solid #181b35;
  padding: 1.25rem 0 2rem;
  display: flex;
  flex-direction: column;
}

.sidebar__brand {
  padding: 0 1.1rem 1.1rem;
  font-size: 0.9rem;
  font-weight: 700;
  color: #a5b4fc;
  border-bottom: 1px solid #181b35;
  margin-bottom: 0.5rem;
  letter-spacing: 0.02em;
}

.sidebar__nav {
  display: flex;
  flex-direction: column;
  padding: 0 0.5rem;
  gap: 0.1rem;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.5rem 0.7rem;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.86rem;
  color: #5c6080;
  transition: background 0.15s, color 0.15s;
  border: none;
  background: none;
  width: 100%;
  text-align: left;
  font-family: inherit;
}

.nav-item:hover:not(:disabled) {
  background: #10122a;
  color: #b0b3d0;
}

.nav-item--active {
  background: #171a36 !important;
  color: #a5b4fc !important;
  font-weight: 500;
}

.nav-item--disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.nav-item__icon {
  font-size: 0.95rem;
  width: 1.2rem;
  text-align: center;
  flex-shrink: 0;
}

/* ── Main content ───────────────────────────────────────────────────────── */
.main-content {
  flex: 1;
  padding: 1.75rem 2rem 3rem;
  min-width: 0;
  overflow-y: auto;
}

.section-header {
  margin-bottom: 1.25rem;
}

.section-header h1 {
  font-size: 1.35rem;
  margin: 0 0 0.15rem;
  color: #d0d4ff;
  font-weight: 600;
}

.section-header p {
  color: #5c6080;
  margin: 0;
  font-size: 0.85rem;
}

/* ── Model row ──────────────────────────────────────────────────────────── */
.model-row {
  display: flex;
  align-items: flex-end;
  gap: 0.65rem;
  margin-bottom: 0.6rem;
  flex-wrap: wrap;
}

.model-row__label {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.78rem;
  color: #6b6f88;
}

/* ── Card grid ───────────────────────────────────────────────────────────── */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 1.1rem;
  align-items: start;
}

/* ── Extension card ─────────────────────────────────────────────────────── */
.ext-card {
  background: #0c0e22;
  border: 1px solid #1e2244;
  border-radius: 14px;
  overflow: hidden;
}

.ext-card__header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.8rem 1rem;
  border-bottom: 1px solid #181b35;
  background: #07091a;
}

.ext-card__icon {
  font-size: 1.05rem;
}

.ext-card__title {
  font-size: 0.92rem;
  font-weight: 600;
  color: #c0c4ff;
  margin: 0;
}

.ext-card__badge {
  margin-left: auto;
  font-size: 0.66rem;
  padding: 0.1rem 0.4rem;
  border-radius: 7px;
  background: #161830;
  color: #454868;
  border: 1px solid #1e2244;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.ext-card__body {
  padding: 1rem;
}

/* ── STT options ────────────────────────────────────────────────────────── */
.stt-options {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  margin-bottom: 0.8rem;
}

.option-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}

.option-label {
  font-size: 0.8rem;
  color: #8a8eb0;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.tag-soon {
  font-size: 0.62rem;
  padding: 0.06rem 0.32rem;
  border-radius: 5px;
  background: #141630;
  color: #454868;
  border: 1px solid #1e2244;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

/* ── Segmented mode ─────────────────────────────────────────────────────── */
.mode-segmented {
  display: flex;
  background: #0c0e22;
  border: 1px solid #1e2244;
  border-radius: 7px;
  padding: 2px;
  gap: 2px;
}

.mode-segmented button {
  flex: 1;
  padding: 0.26rem 0.6rem;
  border: none;
  background: transparent;
  color: #5c6080;
  border-radius: 5px;
  cursor: pointer;
  font-size: 0.76rem;
  transition: all 0.15s;
  font-family: inherit;
}

.mode-segmented button.active {
  background: #1e2244;
  color: #a5b4fc;
  font-weight: 500;
}

/* ── Toggle ─────────────────────────────────────────────────────────────── */
.toggle {
  position: relative;
  display: inline-block;
  width: 32px;
  height: 17px;
  flex-shrink: 0;
}

.toggle input {
  opacity: 0;
  width: 0;
  height: 0;
  position: absolute;
}

.toggle__slider {
  position: absolute;
  inset: 0;
  background: #161830;
  border-radius: 9px;
  border: 1px solid #1e2244;
  cursor: pointer;
  transition: 0.2s;
}

.toggle__slider::before {
  content: '';
  position: absolute;
  width: 11px;
  height: 11px;
  left: 2px;
  top: 2px;
  background: #454868;
  border-radius: 50%;
  transition: 0.2s;
}

.toggle input:checked + .toggle__slider {
  background: #2e2b6e;
  border-color: #4338ca;
}

.toggle input:checked + .toggle__slider::before {
  transform: translateX(15px);
  background: #a5b4fc;
}

.toggle input:disabled + .toggle__slider {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── Dropzone ───────────────────────────────────────────────────────────── */
.dropzone {
  display: block;
  border: 2px dashed #1e2244;
  border-radius: 10px;
  padding: 1.5rem 1rem;
  margin: 0.5rem 0 0.65rem;
  text-align: center;
  background: #080919;
  cursor: pointer;
  color: #5c6080;
  font-size: 0.82rem;
  transition: border-color 0.2s;
}

.dropzone:hover {
  border-color: #4f56b0;
}

.dropzone input {
  display: none;
}

/* ── Convert button ─────────────────────────────────────────────────────── */
.btn-convert {
  width: 100%;
  margin-top: 0.1rem;
  padding: 0.6rem 1rem;
  border: 1px solid #2d3153;
  border-radius: 9px;
  background: #161830;
  color: #a5b4fc;
  font-size: 0.86rem;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.45rem;
  font-family: inherit;
}

.btn-convert:hover:not(:disabled) {
  background: #1e2244;
}

.btn-convert:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── TTS placeholder ────────────────────────────────────────────────────── */
.tts-placeholder {
  color: #30324e;
  font-size: 0.8rem;
  line-height: 1.7;
  margin: 0;
}

/* ── Coming soon (Abschnitt) ────────────────────────────────────────────── */
.coming-soon {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem 2rem;
  color: #30324e;
  text-align: center;
  gap: 0.4rem;
}

.coming-soon__icon {
  font-size: 3rem;
  opacity: 0.15;
  margin-bottom: 0.4rem;
}

.coming-soon__title {
  font-size: 0.95rem;
  color: #454868;
  font-weight: 500;
  margin: 0;
}

/* ── Base form elements ─────────────────────────────────────────────────── */
select,
button {
  border: 1px solid #1e2244;
  border-radius: 8px;
  background: #0c0e22;
  color: inherit;
  padding: 0.45rem 0.7rem;
  font-family: inherit;
  font-size: 0.83rem;
}

button {
  cursor: pointer;
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* ── Spinner ────────────────────────────────────────────────────────────── */
.spinner {
  display: inline-block;
  width: 0.72rem;
  height: 0.72rem;
  border: 2px solid #5c6080;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ── Error ──────────────────────────────────────────────────────────────── */
.error {
  padding: 0.55rem 0.75rem;
  border-radius: 8px;
  background: #200d12;
  color: #ff9aaa;
  font-size: 0.8rem;
  margin: 0.4rem 0;
}

/* ── Result ─────────────────────────────────────────────────────────────── */
.result {
  margin-top: 0.75rem;
  border: 1px solid #181b35;
  border-radius: 10px;
  overflow: hidden;
  background: #07091a;
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.45rem 0.75rem;
  border-bottom: 1px solid #181b35;
  font-size: 0.78rem;
  color: #5c6080;
  background: #0a0c1e;
}

.result-header button {
  padding: 0.18rem 0.5rem;
  font-size: 0.73rem;
  background: #0c0e22;
  border-color: #1e2244;
  color: #7c7f99;
}

pre {
  white-space: pre-wrap;
  word-break: break-word;
  padding: 0.75rem;
  margin: 0;
  font-size: 0.8rem;
  color: #c0c4ff;
  line-height: 1.6;
}

/* ── Model banner ───────────────────────────────────────────────────────── */
.model-banner {
  margin: 0 0 0.9rem;
  padding: 0.6rem 0.85rem;
  border-radius: 9px;
  background: #0c0e22;
  border: 1px solid #1e2244;
}

.model-banner--error {
  background: #150708;
  border-color: #4a1a1a;
}

.model-banner__header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.82rem;
  color: #8a8eb0;
}

.model-banner__message { flex: 1; }

.progress-track {
  margin-top: 0.4rem;
  height: 5px;
  background: #181b35;
  border-radius: 3px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: linear-gradient(90deg, #6f77ff, #a78bfa);
  border-radius: 3px;
  transition: width 0.4s ease;
  min-width: 4px;
}

/* ── Model selector ─────────────────────────────────────────────────────── */
.model-select-wrap {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

.model-dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #30324e;
  flex-shrink: 0;
  transition: background 0.3s;
}

.model-dot--ready {
  background: #4ade80;
  box-shadow: 0 0 5px #4ade8066;
}

.btn-load {
  background: #0c0e22;
  border-color: #4f56b0;
  color: #8b95d9;
  cursor: pointer;
  align-self: flex-end;
}

.btn-load:hover:not(:disabled) {
  background: #10122a;
}

.btn-load--download {
  border-color: #b45309;
  color: #fbbf24;
}

.btn-load--download:hover:not(:disabled) {
  background: #1c1207;
}

/* ── Model card ─────────────────────────────────────────────────────────── */
.model-card {
  background: #07091a;
  border: 1px solid #181b35;
  border-radius: 9px;
  padding: 0.55rem 0.85rem;
  margin-bottom: 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.78rem;
}

.model-card__row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.3rem;
}

.model-card__chip {
  background: #0c0e22;
  border: 1px solid #1e2244;
  border-radius: 5px;
  padding: 0.07rem 0.38rem;
  color: #8a8eb0;
  font-size: 0.75rem;
}

.model-card__label { color: #454868; }
.model-card__sep   { color: #1e2244; user-select: none; }

.model-card__hw {
  color: #3a3d58;
  font-size: 0.73rem;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-card__active {
  color: #6a60b8;
  font-weight: 500;
}

/* ── Badges ─────────────────────────────────────────────────────────────── */
.badge {
  padding: 0.07rem 0.38rem;
  border-radius: 5px;
  font-size: 0.73rem;
  font-weight: 600;
}

.badge--fast       { background: #07200f; color: #4ade80; }
.badge--medium     { background: #1f0e02; color: #fb923c; }
.badge--slow       { background: #1f0902; color: #f87171; }
.badge--very-slow  { background: #1f0303; color: #fca5a5; }

.badge--best       { background: #07200f; color: #4ade80; }
.badge--excellent  { background: #061629; color: #60a5fa; }
.badge--very-good  { background: #041e1e; color: #34d399; }
.badge--good       { background: #1f0e02; color: #fb923c; }
.badge--fair       { background: #1f0902; color: #f87171; }

/* ── Debug: model chips ─────────────────────────────────────────────────────── */
.model-debug {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 0.5rem;
  align-self: flex-end;
}

.model-chip {
  padding: 2px 7px;
  border-radius: 5px;
  font-size: 0.71rem;
  border: 1px solid #1e2244;
  background: #0c0e22;
  color: #2e3155;
  cursor: default;
  user-select: none;
  white-space: nowrap;
}

.model-chip--cached {
  border-color: #1a3d1a;
  color: #4ade80;
  background: #05110a;
}

.model-chip--active {
  border-color: #4338ca;
  color: #a5b4fc;
  background: #1e1b4b;
  font-weight: 600;
}
</style>
