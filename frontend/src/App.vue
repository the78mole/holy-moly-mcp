<script setup>
import { computed, ref } from 'vue'

const file = ref(null)
const mode = ref('plain')
const loading = ref(false)
const output = ref('')
const error = ref('')
const uiText = {
  emptyDropLabel: 'Datei hier ablegen oder klicken, um eine Datei auszuwählen',
  selectedPrefix: 'Ausgewählt',
  pickFileError: 'Bitte zuerst eine Datei auswählen.',
  genericError: 'Konvertierung fehlgeschlagen.',
  unknownError: 'Unbekannter Fehler',
}

const acceptedTypes = '.mp3,.ogg,.wav,.m4a,.pdf'
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

function onDrop(event) {
  event.preventDefault()
  const dropped = event.dataTransfer?.files?.[0]
  if (dropped) {
    file.value = dropped
    error.value = ''
  }
}

function onSelect(event) {
  const selected = event.target?.files?.[0]
  if (selected) {
    file.value = selected
    error.value = ''
  }
}

async function convert() {
  if (!file.value) {
    error.value = uiText.pickFileError
    return
  }

  loading.value = true
  error.value = ''
  output.value = ''

  try {
    const formData = new FormData()
    formData.append('file', file.value)

    const requestUrl = isPdf.value
      ? endpoint.value
      : `${endpoint.value}?mode=${encodeURIComponent(mode.value)}`

    const response = await fetch(requestUrl, {
      method: 'POST',
      body: formData,
    })

    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || uiText.genericError)
    }

    output.value = data.result || ''
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : uiText.unknownError
  } finally {
    loading.value = false
  }
}

async function copyOutput() {
  if (!output.value) {
    return
  }
  await navigator.clipboard.writeText(output.value)
}
</script>

<template>
  <main class="shell">
    <h1>Holy Moly Converter</h1>
    <p class="subtitle">X-to-AI und AI-to-X Konvertierung für Audio &amp; PDF</p>

    <label
      class="dropzone"
      @dragover.prevent
      @drop="onDrop"
    >
      <input type="file" :accept="acceptedTypes" @change="onSelect" />
      <span>{{ dropLabel }}</span>
    </label>

    <div class="controls">
      <label v-if="!isPdf">
        Modus
        <select v-model="mode">
          <option value="plain">plain</option>
          <option value="minutes">minutes</option>
        </select>
      </label>
      <button :disabled="loading" @click="convert">
        <span v-if="loading" class="spinner" />
        {{ loading ? 'Konvertiere...' : 'Konvertieren' }}
      </button>
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
</style>
