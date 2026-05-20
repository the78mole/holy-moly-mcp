<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const file = ref(null)
const mode = ref('plain')
const loading = ref(false)
const output = ref('')
const error = ref('')

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

const acceptedTypes = '.mp3,.ogg,.wav,.m4a,.flac,.mp4,.pdf'
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
  <main class="shell">
    <h1>Holy Moly Converter</h1>
    <p class="subtitle">X-to-AI und AI-to-X Konvertierung für Audio &amp; PDF</p>

    <!-- Model status banner -->
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

    <label
      class="dropzone"
      @dragover.prevent
      @drop="onDrop"
    >
      <input type="file" :accept="acceptedTypes" @change="onSelect" />
      <span>{{ dropLabel }}</span>
    </label>

    <div class="controls">
      <label>
        Modell
        <div class="model-select-wrap">
          <select v-model="selectedModel">
            <option
              v-for="m in availableModels"
              :key="m.name"
              :value="m.name"
            >
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
        :disabled="modelLoading"
        @click="applyModelSelection"
      >
        <span v-if="modelLoading" class="spinner" />
        Laden
      </button>
      <label v-if="!isPdf">
        Modus
        <select v-model="mode">
          <option value="plain">plain</option>
          <option value="minutes">minutes</option>
        </select>
      </label>
      <button :disabled="loading || !modelReady" @click="convert">
        <span v-if="loading" class="spinner" />
        {{ loading ? 'Konvertiere...' : 'Konvertieren' }}
      </button>
    </div>

    <!-- Model info card -->
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

    <p v-if="error" class="error">{{ error }}</p>

    <section v-if="output" class="result">
      <div class="result-header">
        <h2>Ergebnis</h2>
        <button @click="copyOutput">Copy to Clipboard</button>
      </div>
      <pre>{{ output }}</pre>
    </section>

    <section class="future">
      <h2>PDF-to-Markdown (Phase 2)</h2>
      <p>PDF Upload ist UI-seitig vorbereitet. Backend liefert aktuell einen Platzhalter.</p>
    </section>
  </main>
</template>

<style scoped>
.shell {
  max-width: 840px;
  margin: 0 auto;
  padding: 2rem 1rem 3rem;
  font-family: Inter, system-ui, sans-serif;
  color: #e9e9ee;
}

.subtitle {
  margin-top: -0.25rem;
  color: #b8b9c9;
}

.dropzone {
  display: block;
  border: 2px dashed #6f77ff;
  border-radius: 14px;
  padding: 2.5rem 1rem;
  margin: 1.5rem 0;
  text-align: center;
  background: #161830;
  cursor: pointer;
}

.dropzone input {
  display: none;
}

.controls {
  display: flex;
  align-items: end;
  gap: 1rem;
  margin-bottom: 1rem;
}

select,
button {
  border: 1px solid #3b3f66;
  border-radius: 10px;
  background: #1f2347;
  color: inherit;
  padding: 0.6rem 0.9rem;
}

button {
  cursor: pointer;
}

button:disabled {
  opacity: 0.7;
  cursor: wait;
}

.spinner {
  display: inline-block;
  width: 0.8rem;
  height: 0.8rem;
  margin-right: 0.35rem;
  border: 2px solid #d9dbff;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.error {
  padding: 0.8rem;
  border-radius: 8px;
  background: #522830;
  color: #ffbbc3;
}

.result,
.future {
  margin-top: 1.25rem;
  border: 1px solid #2d3153;
  border-radius: 12px;
  padding: 1rem;
  background: #14162b;
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: #0e1022;
  padding: 1rem;
  border-radius: 8px;
}

/* Model loading banner */
.model-banner {
  margin: 1rem 0;
  padding: 0.75rem 1rem;
  border-radius: 10px;
  background: #1a1e3c;
  border: 1px solid #3b3f66;
}

.model-banner--error {
  background: #2e1a1a;
  border-color: #7a3333;
}

.model-banner__header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
  color: #b8b9c9;
}

.model-banner__message {
  flex: 1;
}

.progress-track {
  margin-top: 0.5rem;
  height: 6px;
  background: #2d3153;
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

/* Model selector */
.model-select-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

.model-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #555;
  flex-shrink: 0;
  transition: background 0.3s ease;
}

.model-dot--ready {
  background: #4ade80;
  box-shadow: 0 0 6px #4ade8088;
}

/* Load button */
.btn-load {
  background: #1e2244;
  border-color: #6f77ff;
  color: #a5b4fc;
  cursor: pointer;
}

.btn-load:hover:not(:disabled) {
  background: #2d3460;
}

/* Model info card */
.model-card {
  background: #0f1128;
  border: 1px solid #2d3153;
  border-radius: 10px;
  padding: 0.65rem 1rem;
  margin-bottom: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  font-size: 0.82rem;
}

.model-card__row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.model-card__chip {
  background: #1f2347;
  border: 1px solid #3b3f66;
  border-radius: 6px;
  padding: 0.1rem 0.45rem;
  color: #b8b9c9;
}

.model-card__label {
  color: #7c7f99;
}

.model-card__sep {
  color: #3b3f66;
  user-select: none;
}

.model-card__hw {
  color: #6b6f88;
  font-size: 0.78rem;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-card__active {
  color: #a78bfa;
  font-weight: 500;
}

/* Speed / quality badges */
.badge {
  padding: 0.1rem 0.45rem;
  border-radius: 6px;
  font-size: 0.78rem;
  font-weight: 600;
}

.badge--fast       { background: #14532d; color: #4ade80; }
.badge--medium     { background: #431a05; color: #fb923c; }
.badge--slow       { background: #431407; color: #f87171; }
.badge--very-slow  { background: #450a0a; color: #fca5a5; }

.badge--best       { background: #14532d; color: #4ade80; }
.badge--excellent  { background: #1e3a5f; color: #60a5fa; }
.badge--very-good  { background: #0d3d3d; color: #34d399; }
.badge--good       { background: #431a05; color: #fb923c; }
.badge--fair       { background: #431407; color: #f87171; }
</style>
