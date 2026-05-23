<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import mermaid from 'mermaid'

// ── Configure marked: syntax highlighting + mermaid blocks ─────────────────
marked.use({
  renderer: {
    code({ text, lang } = {}) {
      if (lang === 'mermaid') {
        return `<div class="mermaid">${text}</div>`
      }
      const highlighted =
        lang && hljs.getLanguage(lang)
          ? hljs.highlight(text, { language: lang, ignoreIllegals: true }).value
          : hljs.highlightAuto(text).value
      return `<pre><code class="hljs">${highlighted}</code></pre>`
    },
  },
})

// ── Initialize Mermaid ──────────────────────────────────────────────────────
mermaid.initialize({
  startOnLoad: false,
  theme: 'neutral',
  flowchart: { htmlLabels: false },
  sequence: { useMaxWidth: true },
})

const file = ref(null)
const mode = ref('plain')
const loading = ref(false)
const output = ref('')
const error = ref('')
const activeSection = ref('speech')
const docSubSection = ref('pdf-md')
const speakerRecognition = ref(false)
const audioTypes = '.mp3,.ogg,.wav,.m4a,.flac,.mp4'

// Language selection
const language = ref(null)           // null = auto-detect
const availableLanguages = ref([])   // populated from API

const LANGUAGE_LABELS = {
  af: 'Afrikaans', ar: 'العربية', bg: 'Български', ca: 'Català',
  cs: 'Čeština', cy: 'Cymraeg', da: 'Dansk', de: 'Deutsch',
  el: 'Ελληνικά', en: 'English', es: 'Español', et: 'Eesti',
  fa: 'فارسی', fi: 'Suomi', fr: 'Français', gl: 'Galego',
  he: 'עברית', hi: 'हिन्दी', hr: 'Hrvatski', hu: 'Magyar',
  hy: 'Հայերեն', id: 'Indonesia', is: 'Íslenska', it: 'Italiano',
  ja: '日本語', ka: 'ქართული', kk: 'Қазақша', ko: '한국어',
  lt: 'Lietuvių', lv: 'Latviešu', mk: 'Македонски', ms: 'Melayu',
  nl: 'Nederlands', no: 'Norsk', pl: 'Polski', pt: 'Português',
  ro: 'Română', ru: 'Русский', sk: 'Slovenčina', sl: 'Slovenščina',
  sq: 'Shqip', sr: 'Српски', sv: 'Svenska', sw: 'Kiswahili',
  th: 'ภาษาไทย', tr: 'Türkçe', uk: 'Українська', ur: 'اردو',
  vi: 'Tiếng Việt', zh: '中文',
}

function langLabel(code) {
  return LANGUAGE_LABELS[code] ? `${LANGUAGE_LABELS[code]} (${code})` : code.toUpperCase()
}

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
    if (data.languages?.length) availableLanguages.value = data.languages
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

onMounted(() => { fetchModelInfo(); fetchSystemInfo(); connectModelStatus(); fetchTtsVoices(); fetchTtsModelInfo(); connectTtsModelStatus() })
onUnmounted(() => {
  eventSource?.close()
  ttsEventSource?.close()
  if (_recordingTimer) clearInterval(_recordingTimer)
  if (_mediaRecorder && isRecording.value) _mediaRecorder.stop()
  _stopLevelMeter()
})

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
    let requestUrl = endpoint.value
    if (!isPdf.value) {
      const params = new URLSearchParams({ mode: mode.value })
      if (language.value) params.set('language', language.value)
      requestUrl = `${endpoint.value}?${params.toString()}`
    }
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

const copied = ref(false)

async function copyOutput() {
  if (!output.value) return
  await navigator.clipboard.writeText(output.value)
  copied.value = true
  setTimeout(() => { copied.value = false }, 1500)
}

// ── Mikrofon-Aufnahme ────────────────────────────────────────────────────────

const isRecording = ref(false)
const recordingDuration = ref(0)
const levelBars = ref(new Array(28).fill(0))
let _mediaRecorder = null
let _recordingChunks = []
let _recordingTimer = null
let _audioCtx = null
let _analyser = null
let _analyserRaf = null

function formatDuration(s) {
  const m = Math.floor(s / 60).toString().padStart(2, '0')
  const sec = (s % 60).toString().padStart(2, '0')
  return `${m}:${sec}`
}

function _startLevelMeter(stream) {
  const BAR_COUNT = levelBars.value.length
  _audioCtx = new AudioContext()
  _analyser = _audioCtx.createAnalyser()
  _analyser.fftSize = 256
  _analyser.smoothingTimeConstant = 0.72
  _audioCtx.createMediaStreamSource(stream).connect(_analyser)
  const dataArr = new Uint8Array(_analyser.frequencyBinCount)
  const step = Math.floor(_analyser.frequencyBinCount / BAR_COUNT)
  function tick() {
    _analyserRaf = requestAnimationFrame(tick)
    _analyser.getByteFrequencyData(dataArr)
    for (let i = 0; i < BAR_COUNT; i++) {
      let sum = 0
      for (let j = 0; j < step; j++) sum += dataArr[i * step + j]
      levelBars.value[i] = Math.round((sum / step / 255) * 100)
    }
  }
  tick()
}

function _stopLevelMeter() {
  if (_analyserRaf) { cancelAnimationFrame(_analyserRaf); _analyserRaf = null }
  _audioCtx?.close(); _audioCtx = null; _analyser = null
  levelBars.value = new Array(levelBars.value.length).fill(0)
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    _recordingChunks = []
    recordingDuration.value = 0
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : ''
    _mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {})
    _mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) _recordingChunks.push(e.data)
    }
    _mediaRecorder.onstop = async () => {
      _stopLevelMeter()
      stream.getTracks().forEach(t => t.stop())
      const type = mimeType || 'audio/webm'
      const blob = new Blob(_recordingChunks, { type })
      const ext = type.includes('webm') ? 'webm' : 'ogg'
      file.value = new File([blob], `recording.${ext}`, { type })
      error.value = ''
      await convert()
    }
    _mediaRecorder.start(250)
    isRecording.value = true
    _recordingTimer = setInterval(() => { recordingDuration.value++ }, 1000)
    _startLevelMeter(stream)
  } catch (err) {
    error.value = 'Mikrofon-Zugriff verweigert: ' + (err instanceof Error ? err.message : String(err))
  }
}

function stopRecording() {
  if (_mediaRecorder && isRecording.value) {
    clearInterval(_recordingTimer)
    _recordingTimer = null
    isRecording.value = false
    _mediaRecorder.stop()
  }
}

// ── PDF → Markdown ───────────────────────────────────────────────────────────

const pdfInputMode  = ref('file')      // 'file' | 'url'
const pdfFile       = ref(null)
const pdfUrl        = ref('')
const pdfMarkdown   = ref('')
const pdfLoading    = ref(false)
const pdfError      = ref('')
const pdfCopied     = ref(false)
const showPdfPreview = ref(false)
const pdfObjectUrl  = ref('')          // blob URL for PDF iframe
const pdfTimeout    = ref(0)           // 0 = Server-Default
const pdfWorkers    = ref(0)           // 0 = Server-Default
const pdfElapsed    = ref(0)           // seconds since conversion started
let _pdfTimer       = null
const pdfEffectiveTimeout = computed(() => pdfTimeout.value > 0 ? pdfTimeout.value : 300)
const pdfTimeoutBarStyle  = computed(() => ({
  width: `${Math.min(100, (pdfElapsed.value / pdfEffectiveTimeout.value) * 100)}%`,
}))

const pdfDropLabel = computed(() =>
  pdfFile.value ? `Ausgewählt: ${pdfFile.value.name}` : 'PDF hier ablegen oder klicken…',
)

const pdfRendered = computed(() =>
  pdfMarkdown.value ? marked.parse(pdfMarkdown.value) : '',
)

function onPdfSelect(e) {
  const f = e.target?.files?.[0]
  if (f) { pdfFile.value = f; pdfError.value = ''; _updatePdfObjectUrl(f) }
}

function onPdfDrop(e) {
  e.preventDefault()
  const f = e.dataTransfer?.files?.[0]
  if (f) { pdfFile.value = f; pdfError.value = ''; _updatePdfObjectUrl(f) }
}

function _updatePdfObjectUrl(f) {
  if (pdfObjectUrl.value) URL.revokeObjectURL(pdfObjectUrl.value)
  pdfObjectUrl.value = URL.createObjectURL(f)
}

async function convertPdf() {
  pdfError.value = ''
  pdfMarkdown.value = ''
  pdfLoading.value = true
  pdfElapsed.value = 0
  _pdfTimer = setInterval(() => { pdfElapsed.value++ }, 1000)
  try {
    let res
    if (pdfInputMode.value === 'url') {
      if (!pdfUrl.value.trim()) { pdfError.value = 'Bitte eine URL eingeben.'; return }
      res = await fetch(`${apiBaseUrl}/api/v1/convert/pdf-from-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: pdfUrl.value.trim(),
          ...(pdfTimeout.value > 0 ? { timeout: pdfTimeout.value } : {}),
          ...(pdfWorkers.value > 0 ? { workers: pdfWorkers.value } : {}),
        }),
      })
    } else {
      if (!pdfFile.value) { pdfError.value = 'Bitte ein PDF auswählen.'; return }
      const fd = new FormData()
      fd.append('file', pdfFile.value)
      const params = new URLSearchParams()
      if (pdfTimeout.value > 0) params.set('timeout', String(pdfTimeout.value))
      if (pdfWorkers.value > 0) params.set('workers', String(pdfWorkers.value))
      const qs = params.toString() ? '?' + params.toString() : ''
      res = await fetch(`${apiBaseUrl}/api/v1/convert/pdf-to-markdown${qs}`, { method: 'POST', body: fd })
    }
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Konvertierung fehlgeschlagen.')
    pdfMarkdown.value = data.result ?? ''
  } catch (err) {
    pdfError.value = err instanceof Error ? err.message : 'Unbekannter Fehler'
  } finally {
    clearInterval(_pdfTimer)
    _pdfTimer = null
    pdfElapsed.value = 0
    pdfLoading.value = false
  }
}

async function copyPdfMarkdown() {
  if (!pdfMarkdown.value) return
  await navigator.clipboard.writeText(pdfMarkdown.value)
  pdfCopied.value = true
  setTimeout(() => { pdfCopied.value = false }, 1500)
}

// ── Markdown → PDF ──────────────────────────────────────────────────────────
const mdInput       = ref('')
const mdLoading     = ref(false)
const mdError       = ref('')
const mdPdfUrl      = ref('')         // blob URL for PDF iframe
const mdPdfBlob     = ref(null)
const mdElapsed     = ref(0)
let _mdTimer        = null
const MD_TIMEOUT    = 60
const mdTimeoutBarStyle = computed(() => ({
  width: `${Math.min(100, (mdElapsed.value / MD_TIMEOUT) * 100)}%`,
}))
const mdPreviewRef  = ref(null)       // template ref for the preview pane
let _mermaidIdCtr   = 0

const mdRendered = computed(() =>
  mdInput.value ? marked.parse(mdInput.value) : '',
)

const mdDropLabel = computed(() =>
  mdInput.value ? 'Andere Markdown-Datei ablegen…' : 'Markdown-Datei hier ablegen oder klicken…',
)

function onMdFileSelect(e) {
  const f = e.target?.files?.[0]
  if (!f) return
  const reader = new FileReader()
  reader.onload = (ev) => { mdInput.value = ev.target.result }
  reader.readAsText(f, 'utf-8')
}

function onMdDrop(e) {
  e.preventDefault()
  const f = e.dataTransfer?.files?.[0]
  if (!f) return
  const reader = new FileReader()
  reader.onload = (ev) => { mdInput.value = ev.target.result }
  reader.readAsText(f, 'utf-8')
}

watch(mdRendered, async () => {
  await nextTick()
  const container = mdPreviewRef.value
  if (!container) return
  const nodes = Array.from(container.querySelectorAll('.mermaid:not([data-rendered])'))
  for (const node of nodes) {
    const code = node.textContent?.trim() || ''
    if (!code) continue
    try {
      const { svg } = await mermaid.render(`mermaid-${++_mermaidIdCtr}`, code)
      node.innerHTML = svg
      node.dataset.rendered = '1'
    } catch (err) {
      node.innerHTML = `<pre style="color:#f87171;font-size:0.8em">${err.message}</pre>`
      node.dataset.rendered = '1'
    }
  }
})

// CSS embedded into the HTML sent to the backend (WeasyPrint-compatible)
const _PDF_BODY_CSS = `
  body{font-family:'Liberation Sans',Arial,sans-serif;font-size:11pt;line-height:1.6;color:#1a1a1a}
  h1{font-size:1.9em;border-bottom:2px solid #e0e0e0;padding-bottom:.25em;margin-top:1em}
  h2{font-size:1.5em;border-bottom:1px solid #e0e0e0;padding-bottom:.2em;margin-top:1em}
  h3,h4,h5,h6{margin-top:1em}
  p{margin:.4em 0 .9em}
  code{font-family:monospace;font-size:.88em;background:#f4f4f4;padding:.1em .3em;border-radius:3px}
  pre{background:#f4f4f4;padding:1em;border-radius:4px;overflow:hidden;margin:.5em 0 1em}
  pre code{background:transparent;padding:0}
  blockquote{border-left:4px solid #d0d0d0;margin:0 0 1em;padding:.5em 1em;color:#555}
  table{border-collapse:collapse;width:100%;margin-bottom:1em}
  th,td{border:1px solid #d0d0d0;padding:.4em .8em;text-align:left}
  th{background:#f0f0f0;font-weight:600}
  img,svg{max-width:100%;height:auto}
  a{color:#2563eb}
  hr{border:none;border-top:1px solid #e0e0e0;margin:1.5em 0}
  ul,ol{padding-left:1.5em;margin:.4em 0 .9em}
  li{margin-bottom:.25em}
  .hljs{background:#f4f4f4;color:#333}
  .hljs-keyword{color:#a626a4;font-weight:bold}
  .hljs-string{color:#50a14f}
  .hljs-comment{color:#a0a0a0;font-style:italic}
  .hljs-number{color:#986801}
  .hljs-built_in{color:#c18401}
  .hljs-title{color:#4078f2}
  .hljs-attr{color:#986801}
  .hljs-variable{color:#e45649}
  .hljs-literal{color:#0184bb}
  .hljs-operator{color:#0184bb}
`

async function generateMdPdf() {
  if (!mdInput.value.trim()) return
  mdError.value = ''
  mdPdfUrl.value = ''
  mdPdfBlob.value = null
  mdLoading.value = true
  mdElapsed.value = 0
  _mdTimer = setInterval(() => { mdElapsed.value++ }, 1000)
  try {
    await nextTick()
    const previewEl = mdPreviewRef.value
    const bodyHtml = previewEl ? previewEl.innerHTML : marked.parse(mdInput.value)
    const html = `<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<style>@page{margin:2cm;size:A4}${_PDF_BODY_CSS}</style>
</head><body>${bodyHtml}</body></html>`
    const res = await fetch(`${apiBaseUrl}/api/v1/convert/markdown-to-pdf`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ html }),
    })
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      throw new Error(d.detail || 'PDF-Generierung fehlgeschlagen.')
    }
    const blob = await res.blob()
    mdPdfBlob.value = blob
    mdPdfUrl.value = URL.createObjectURL(blob)
  } catch (err) {
    mdError.value = err instanceof Error ? err.message : 'Unbekannter Fehler'
  } finally {
    clearInterval(_mdTimer)
    _mdTimer = null
    mdElapsed.value = 0
    mdLoading.value = false
  }
}

function downloadMdPdf() {
  if (!mdPdfBlob.value) return
  const a = document.createElement('a')
  a.href = URL.createObjectURL(mdPdfBlob.value)
  a.download = 'document.pdf'
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

// ── TTS model state ─────────────────────────────────────────────────────────

const availableTtsModels = ref([])
const activeTtsModel = ref('')
const selectedTtsModel = ref('')
const ttsModelPhase = ref('idle')    // idle | loading | ready | error
const ttsModelProgress = ref(0)
const ttsModelMessage = ref('')
let ttsEventSource = null

const ttsModelReady = computed(() => ttsModelPhase.value === 'ready')
const ttsModelLoading = computed(() => ttsModelPhase.value === 'loading')
const ttsModelError = computed(() => ttsModelPhase.value === 'error')
const ttsProgressBarStyle = computed(() => ({ width: `${ttsModelProgress.value}%` }))
const selectedTtsModelMeta = computed(() =>
  availableTtsModels.value.find(m => m.name === selectedTtsModel.value) ?? null)

async function fetchTtsModelInfo() {
  try {
    const res = await fetch(`${apiBaseUrl}/api/v1/tts/model/info`)
    if (!res.ok) return
    const data = await res.json()
    availableTtsModels.value = data.models ?? []
    activeTtsModel.value = data.active ?? ''
    if (!selectedTtsModel.value) selectedTtsModel.value = data.active ?? ''
  } catch (_) {}
}

async function applyTtsModelSelection() {
  if (!selectedTtsModel.value || selectedTtsModel.value === activeTtsModel.value) return
  await fetch(`${apiBaseUrl}/api/v1/tts/model/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: selectedTtsModel.value }),
  })
  ttsEventSource?.close()
  ttsEventSource = null
  connectTtsModelStatus()
}

function connectTtsModelStatus() {
  if (ttsEventSource) return
  ttsEventSource = new EventSource(`${apiBaseUrl}/api/v1/tts/model/status`)
  ttsEventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      ttsModelPhase.value = data.phase
      ttsModelProgress.value = data.progress ?? 0
      ttsModelMessage.value = data.message ?? ''
      if (data.model) activeTtsModel.value = data.model
      if (data.phase === 'ready' || data.phase === 'error') {
        ttsEventSource.close(); ttsEventSource = null
        fetchTtsModelInfo()
      }
    } catch (_) {}
  }
  ttsEventSource.onerror = () => {
    if (ttsModelPhase.value !== 'ready') {
      ttsEventSource?.close(); ttsEventSource = null
      setTimeout(connectTtsModelStatus, 2000)
    }
  }
}

// ── TTS state ─────────────────────────────────────────────────────────────

const ttsVoices = ref([])        // [{ id, name, lang }]
const selectedVoice = ref('')
const ttsText = ref('')
const ttsState = ref('idle')     // idle | loading | streaming | done | error
const ttsError = ref('')
const audioSrc = ref('')
const ttsTaskId = ref('')
const audioEl = ref(null)

const ttsVoicesByLang = computed(() => {
  const langLabels = {
    'en-us': '🇺🇸 English (US)',
    'en-gb': '🇬🇧 English (UK)',
    'de':    '🇩🇪 Deutsch',
    'fr-fr': '🇫🇷 Français',
    'it':    '🇮🇹 Italiano',
  }
  const groups = new Map()
  for (const v of ttsVoices.value) {
    const label = langLabels[v.lang] ?? v.lang
    if (!groups.has(label)) groups.set(label, [])
    groups.get(label).push(v)
  }
  return groups
})

async function fetchTtsVoices() {
  try {
    const res = await fetch(`${apiBaseUrl}/api/v1/convert/text-to-speech/models`)
    if (!res.ok) return
    const data = await res.json()
    ttsVoices.value = data.voices ?? []
    if (ttsVoices.value.length > 0 && !selectedVoice.value) {
      selectedVoice.value = ttsVoices.value[0].id
    }
  } catch (_) {}
}

function startTts() {
  if (!ttsText.value.trim() || !selectedVoice.value) return
  const taskId = crypto.randomUUID()
  ttsTaskId.value = taskId
  ttsError.value = ''
  ttsState.value = 'loading'
  const params = new URLSearchParams({
    text: ttsText.value,
    voice_id: selectedVoice.value,
    task_id: taskId,
  })
  // Set src synchronously so browser play() stays within the user-gesture context
  audioSrc.value = `${apiBaseUrl}/api/v1/convert/text-to-speech/stream?${params.toString()}`
}

watch(audioSrc, async (src) => {
  if (!src) return
  await nextTick()
  if (audioEl.value) {
    audioEl.value.load()
    audioEl.value.play().catch(() => { /* autoplay blocked – controls still visible */ })
  }
}, { flush: 'post' })

function onTtsPlaying() { ttsState.value = 'streaming' }
function onTtsEnded()  { ttsState.value = 'done' }
function onTtsError()  {
  ttsState.value = 'error'
  ttsError.value = 'Audio-Streaming fehlgeschlagen. Bitte erneut versuchen.'
}

async function downloadOgg() {
  try {
    const url = `${apiBaseUrl}/api/v1/convert/text-to-speech/download/${ttsTaskId.value}`
    const res = await fetch(url)
    if (!res.ok) {
      ttsError.value = 'OGG-Datei noch nicht bereit—bitte kurz warten.'
      return
    }
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `tts-${ttsTaskId.value.slice(0, 8)}.ogg`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(a.href)
  } catch (_) {
    ttsError.value = 'Download fehlgeschlagen.'
  }
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
        <button
          class="nav-item"
          :class="{ 'nav-item--active': activeSection === 'documents' }"
          @click="activeSection = 'documents'"
        >
          <span class="nav-item__icon">📄</span> Documents
        </button>
        <template v-if="activeSection === 'documents'">
          <button
            class="nav-item nav-item--sub"
            :class="{ 'nav-item--active': docSubSection === 'pdf-md' }"
            @click="docSubSection = 'pdf-md'"
          >
            <span class="nav-item__icon">🔄</span> PDF → Markdown
          </button>
          <button
            class="nav-item nav-item--sub"
            :class="{ 'nav-item--active': docSubSection === 'md-pdf' }"
            @click="docSubSection = 'md-pdf'"
          >
            <span class="nav-item__icon">📝</span> Markdown → PDF
          </button>
        </template>
      </div>
    </nav>

    <!-- ───────────────────────── Main content ───────────────────────────── -->
    <main class="main-content">

      <!-- ══ Speech ══════════════════════════════════════════════════════ -->
      <section v-if="activeSection === 'speech'">
        <div class="speech-grid">

          <!-- ── STT Panel ─────────────────────────────────────────── -->
          <div class="speech-panel">
            <div class="speech-panel__header">
              <span class="speech-panel__icon">🎤</span>
              <h2 class="speech-panel__title">Speech-to-Text</h2>
            </div>
            <div class="speech-panel__body">

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
                <div class="option-row">
                  <span class="option-label">Sprache</span>
                  <select class="lang-select" v-model="language">
                    <option :value="null">Automatisch</option>
                    <option v-for="lang in availableLanguages" :key="lang" :value="lang">
                      {{ langLabel(lang) }}
                    </option>
                  </select>
                </div>
              </div>

              <label class="dropzone" @dragover.prevent @drop="onDrop">
                <input type="file" :accept="audioTypes" @change="onSelect" />
                <span>{{ dropLabel }}</span>
              </label>

              <div class="record-divider"><span>– oder –</span></div>

              <!-- Mikrofon-Aufnahme -->
              <div class="record-row">
                <button
                  v-if="!isRecording"
                  class="btn-record"
                  :disabled="loading || !modelReady"
                  @click="startRecording"
                >
                  🎙 Aufnahme starten
                </button>
                <button
                  v-else
                  class="btn-record btn-record--stop"
                  @click="stopRecording"
                >
                  <span class="rec-dot" />
                  {{ formatDuration(recordingDuration) }} &ndash; Stopp
                </button>
              </div>

              <!-- Level Meter -->
              <div v-if="isRecording" class="level-meter" aria-hidden="true">
                <div
                  v-for="(h, i) in levelBars"
                  :key="i"
                  class="level-bar"
                  :style="{ height: Math.max(h, 3) + '%' }"
                />
              </div>

              <p v-if="error" class="error">{{ error }}</p>

              <button class="btn-convert" :disabled="loading || !modelReady" @click="convert">
                <span v-if="loading" class="spinner" />
                {{ loading ? 'Konvertiere…' : 'Konvertieren' }}
              </button>

              <section v-if="output" class="result">
                <div class="result-header">
                  <span>Ergebnis</span>
                  <button
                    class="btn-copy"
                    :class="{ 'btn-copy--done': copied }"
                    @click="copyOutput"
                  >{{ copied ? '✓ Kopiert' : 'Kopieren' }}</button>
                </div>
                <pre>{{ output }}</pre>
              </section>
            </div>
          </div>

          <!-- ── TTS Panel ─────────────────────────────────────────── -->
          <div class="speech-panel">
            <div class="speech-panel__header">
              <span class="speech-panel__icon">🔊</span>
              <h2 class="speech-panel__title">Text-to-Speech</h2>
            </div>
            <div class="speech-panel__body">

              <!-- TTS model loading banner -->
              <div v-if="!ttsModelReady && ttsModelPhase !== 'idle'" class="model-banner"
                   :class="{ 'model-banner--error': ttsModelError }">
                <div class="model-banner__header">
                  <span v-if="ttsModelLoading" class="spinner" aria-hidden="true" />
                  <span v-if="ttsModelError">⚠</span>
                  <span class="model-banner__message">{{ ttsModelMessage }}</span>
                </div>
                <div v-if="ttsModelLoading" class="progress-track">
                  <div class="progress-bar" :style="ttsProgressBarStyle" />
                </div>
              </div>

              <!-- TTS model selector row -->
              <div class="model-row">
                <label class="model-row__label">
                  TTS-Modell
                  <div class="model-select-wrap">
                    <select v-model="selectedTtsModel">
                      <option v-for="m in availableTtsModels" :key="m.name" :value="m.name">
                        {{ m.name }}{{ m.name === activeTtsModel ? ' ★' : '' }}
                      </option>
                    </select>
                    <span class="model-dot"
                      :class="{ 'model-dot--ready': selectedTtsModelMeta?.cached }"
                      :title="selectedTtsModelMeta?.cached ? 'Im Cache' : 'Noch nicht heruntergeladen'" />
                  </div>
                </label>
                <button v-if="selectedTtsModel && selectedTtsModel !== activeTtsModel"
                  class="btn-load"
                  :class="{ 'btn-load--download': !selectedTtsModelMeta?.cached }"
                  :disabled="ttsModelLoading"
                  @click="applyTtsModelSelection">
                  <span v-if="ttsModelLoading" class="spinner" />
                  {{ selectedTtsModelMeta?.cached ? 'Aktivieren' : 'Herunterladen' }}
                </button>
                <div class="model-debug">
                  <span v-for="m in availableTtsModels" :key="m.name" class="model-chip"
                    :class="{ 'model-chip--active': m.name === activeTtsModel, 'model-chip--cached': m.cached && m.name !== activeTtsModel }">
                    {{ m.name }}
                  </span>
                </div>
              </div>

              <!-- TTS model info card -->
              <div v-if="selectedTtsModelMeta" class="model-card">
                <div class="model-card__row">
                  <span class="model-card__chip">{{ selectedTtsModelMeta.precision }}</span>
                  <span class="model-card__chip">{{ formatSize(selectedTtsModelMeta.size_mb) }}</span>
                </div>
                <div class="model-card__row">
                  <span class="model-card__label">Qualität</span>
                  <span class="badge" :class="qualityClass(selectedTtsModelMeta.quality)">{{ selectedTtsModelMeta.quality }}</span>
                  <span class="model-card__sep">·</span>
                  <span class="model-card__label">Geschwindigkeit</span>
                  <span class="badge" :class="speedClass(selectedTtsModelMeta.speed)">{{ selectedTtsModelMeta.speed }}</span>
                </div>
              </div>

              <!-- Voice selector -->
              <div class="tts-options">
                <div class="option-row">
                  <span class="option-label">Stimme</span>
                  <select
                    class="lang-select"
                    v-model="selectedVoice"
                    :disabled="ttsVoices.length === 0 || ttsState === 'loading' || ttsState === 'streaming'"
                  >
                    <option value="" disabled>Stimme wählen…</option>
                    <template v-for="[ttsLangLabel, ttsLangVoices] in ttsVoicesByLang" :key="ttsLangLabel">
                      <optgroup :label="ttsLangLabel">
                        <option v-for="v in ttsLangVoices" :key="v.id" :value="v.id">{{ v.name }}</option>
                      </optgroup>
                    </template>
                  </select>
                </div>
              </div>

              <!-- Input textarea -->
              <textarea
                class="tts-textarea"
                v-model="ttsText"
                placeholder="Text hier eingeben…"
                rows="5"
                :disabled="ttsState === 'loading' || ttsState === 'streaming'"
              />

              <!-- Error message -->
              <p v-if="ttsError" class="error">{{ ttsError }}</p>

              <!-- Status banner -->
              <div v-if="ttsState === 'loading'" class="tts-status">
                <span class="spinner" aria-hidden="true"></span>Modell wird geladen…
              </div>
              <div v-else-if="ttsState === 'streaming'" class="tts-status tts-status--active">
                <span class="spinner" aria-hidden="true"></span>Wird synthetisiert…
              </div>

              <!-- Synthesize button -->
              <button
                class="btn-convert"
                :disabled="!ttsText.trim() || !selectedVoice || ttsState === 'loading' || ttsState === 'streaming'"
                @click="startTts"
              >
                <span
                  v-if="ttsState === 'loading' || ttsState === 'streaming'"
                  class="spinner"
                  aria-hidden="true"
                ></span>
                {{
                  ttsState === 'loading'   ? 'Modell lädt…' :
                  ttsState === 'streaming' ? 'Generiert…'   :
                  'Synthesisieren & Streamen'
                }}
              </button>

              <!-- Audio player + OGG download -->
              <div v-if="audioSrc" class="tts-player">
                <audio
                  ref="audioEl"
                  controls
                  :src="audioSrc"
                  @playing="onTtsPlaying"
                  @ended="onTtsEnded"
                  @error="onTtsError"
                />
                <button
                  v-if="ttsState === 'done'"
                  class="btn-download"
                  @click="downloadOgg"
                >
                  ⬇ Als OGG herunterladen
                </button>
              </div>

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

        <!-- ── PDF → Markdown ──────────────────────────────────────────── -->
        <div v-if="docSubSection === 'pdf-md'" class="pdf-workspace">

          <!-- Input panel -->
          <div class="pdf-input-panel">
            <div class="speech-panel__header">
              <span class="speech-panel__icon">🔄</span>
              <h2 class="speech-panel__title">PDF → Markdown</h2>
            </div>

            <!-- Source toggle -->
            <div class="mode-segmented" style="margin-bottom: 0.75rem">
              <button :class="{ active: pdfInputMode === 'file' }" @click="pdfInputMode = 'file'">Datei</button>
              <button :class="{ active: pdfInputMode === 'url'  }" @click="pdfInputMode = 'url'">URL</button>
            </div>

            <!-- File drop zone -->
            <label v-if="pdfInputMode === 'file'" class="dropzone" @dragover.prevent @drop="onPdfDrop">
              <input type="file" accept=".pdf" @change="onPdfSelect" />
              <span>{{ pdfDropLabel }}</span>
            </label>

            <!-- URL input -->
            <input
              v-else
              class="pdf-url-input"
              type="url"
              v-model="pdfUrl"
              placeholder="https://example.com/document.pdf"
            />

            <p v-if="pdfError" class="error">{{ pdfError }}</p>

            <!-- PDF preview toggle -->
            <div class="option-row" style="margin-top: 0.5rem">
              <span class="option-label">Original-PDF einblenden</span>
              <label class="toggle">
                <input type="checkbox" v-model="showPdfPreview"
                  :disabled="pdfInputMode === 'url' && !pdfMarkdown" />
                <span class="toggle__slider" />
              </label>
            </div>

            <!-- Advanced options: timeout + workers -->
            <div class="pdf-adv-options">
              <label class="pdf-adv-label">
                Timeout (s)
                <input class="pdf-adv-input" type="number" v-model.number="pdfTimeout"
                  min="0" max="3600" placeholder="Server-Default" />
              </label>
              <label class="pdf-adv-label">
                Threads
                <input class="pdf-adv-input" type="number" v-model.number="pdfWorkers"
                  min="0" max="32" placeholder="Server-Default" />
              </label>
            </div>

            <button
              class="btn-convert"
              :disabled="pdfLoading || (pdfInputMode === 'file' && !pdfFile) || (pdfInputMode === 'url' && !pdfUrl.trim())"
              @click="convertPdf"
              style="margin-top: 0.75rem"
            >
              <span v-if="pdfLoading" class="spinner" />
              {{ pdfLoading ? 'Konvertiere…' : 'Konvertieren' }}
            </button>

            <!-- Timeout progress bar -->
            <div v-if="pdfLoading" class="pdf-progress-track">
              <div class="pdf-progress-bar" :style="pdfTimeoutBarStyle" />
            </div>
            <p v-if="pdfLoading" class="pdf-progress-label">
              {{ pdfElapsed }}s&nbsp;/&nbsp;{{ pdfEffectiveTimeout }}s
            </p>
          </div>

          <!-- Output area -->
          <div v-if="pdfMarkdown || pdfLoading" class="pdf-output-area" :class="{ 'pdf-output-area--3col': showPdfPreview && pdfObjectUrl }">

            <!-- Markdown editor -->
            <div class="pdf-col">
              <div class="pdf-col__header">
                <span>Markdown</span>
                <button class="btn-copy" :class="{ 'btn-copy--done': pdfCopied }" @click="copyPdfMarkdown">
                  {{ pdfCopied ? '✓ Kopiert' : 'Kopieren' }}
                </button>
              </div>
              <textarea class="pdf-md-editor" v-model="pdfMarkdown" spellcheck="false" />
            </div>

            <!-- Live preview -->
            <div class="pdf-col">
              <div class="pdf-col__header"><span>Vorschau</span></div>
              <div class="pdf-md-preview markdown-body" v-html="pdfRendered" />
            </div>

            <!-- Optional PDF iframe -->
            <div v-if="showPdfPreview && pdfObjectUrl" class="pdf-col">
              <div class="pdf-col__header"><span>Original PDF</span></div>
              <iframe class="pdf-iframe" :src="pdfObjectUrl" />
            </div>
          </div>

          <!-- Loading state before result -->
          <div v-else-if="pdfLoading" class="pdf-converting">
            <span class="spinner" />
            <span>marker_single läuft… das kann je nach PDF-Größe etwas dauern.</span>
          </div>

        </div>

        <!-- ── Markdown → PDF ──────────────────────────────────────────── -->
        <div v-else-if="docSubSection === 'md-pdf'" class="mdpdf-workspace">

          <!-- Left: editor panel -->
          <div class="mdpdf-editor-pane">
            <div class="speech-panel__header">
              <span class="speech-panel__icon">📝</span>
              <h2 class="speech-panel__title">Markdown → PDF</h2>
            </div>

            <!-- File drop zone -->
            <label class="dropzone" @dragover.prevent @drop="onMdDrop">
              <input type="file" accept=".md,.txt,.markdown" @change="onMdFileSelect" />
              <span>{{ mdDropLabel }}</span>
            </label>

            <!-- Markdown textarea -->
            <textarea
              class="mdpdf-textarea"
              v-model="mdInput"
              spellcheck="false"
              placeholder="# Mein Dokument&#10;&#10;Hier Markdown eingeben…&#10;&#10;```mermaid&#10;graph TD&#10;  A[Start] --&gt; B[Ende]&#10;```"
            />

            <p v-if="mdError" class="error">{{ mdError }}</p>

            <button
              class="btn-convert"
              :disabled="mdLoading || !mdInput.trim()"
              @click="generateMdPdf"
              style="margin-top: 0.5rem"
            >
              <span v-if="mdLoading" class="spinner" />
              {{ mdLoading ? 'Generiere PDF…' : 'PDF generieren' }}
            </button>

            <!-- Progress bar -->
            <div v-if="mdLoading" class="pdf-progress-track">
              <div class="pdf-progress-bar" :style="mdTimeoutBarStyle" />
            </div>
            <p v-if="mdLoading" class="pdf-progress-label">
              {{ mdElapsed }}s&nbsp;/&nbsp;{{ MD_TIMEOUT }}s
            </p>
          </div>

          <!-- Middle: live Markdown preview -->
          <div class="mdpdf-preview-pane">
            <div class="pdf-col__header">
              <span>Vorschau</span>
            </div>
            <div ref="mdPreviewRef" class="mdpdf-markdown markdown-body" v-html="mdRendered" />
          </div>

          <!-- Right: PDF preview (only when PDF exists) -->
          <div v-if="mdPdfUrl" class="mdpdf-pdf-pane">
            <div class="pdf-col__header">
              <span>PDF</span>
              <button class="btn-copy" @click="downloadMdPdf">⬇ Download</button>
            </div>
            <iframe class="pdf-iframe" :src="mdPdfUrl" />
          </div>

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

/* ── Speech two-panel layout ─────────────────────────────────────────────── */
.speech-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 1.25rem;
  align-items: start;
}

.speech-panel {
  background: #0c0e22;
  border: 1px solid #1e2244;
  border-radius: 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.speech-panel__header {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.9rem 1.1rem;
  border-bottom: 1px solid #181b35;
  background: #07091a;
}

.speech-panel__icon {
  font-size: 1.15rem;
}

.speech-panel__title {
  font-size: 1rem;
  font-weight: 600;
  color: #c0c4ff;
  margin: 0;
}

.speech-panel__body {
  padding: 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

/* ── Card grid (kept for future sections) ────────────────────────────────── */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 1.1rem;
  align-items: start;
}

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

.ext-card__icon { font-size: 1.05rem; }

.ext-card__title {
  font-size: 0.92rem;
  font-weight: 600;
  color: #c0c4ff;
  margin: 0;
}

.ext-card__body { padding: 1rem; }

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

/* ── Language select ────────────────────────────────────────────────────── */
.lang-select {
  background: #0c0e22;
  border: 1px solid #1e2244;
  border-radius: 7px;
  color: #a5b4fc;
  font-size: 0.76rem;
  padding: 0.28rem 0.55rem;
  font-family: inherit;
  cursor: pointer;
  outline: none;
}

.lang-select:focus {
  border-color: #4f5db0;
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

/* ── Record ─────────────────────────────────────────────────────────────── */
.record-divider {
  display: flex;
  align-items: center;
  text-align: center;
  margin: 0.5rem 0;
  color: #3a3d5c;
  font-size: 0.75rem;
}
.record-divider::before,
.record-divider::after {
  content: '';
  flex: 1;
  border-top: 1px solid #1e2140;
}
.record-divider span {
  padding: 0 0.6rem;
}

.record-row {
  display: flex;
  justify-content: center;
  margin-bottom: 0.5rem;
}

.btn-record {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.5rem 1.2rem;
  border-radius: 8px;
  border: 1.5px solid #4f46e5;
  background: transparent;
  color: #a5b4fc;
  font-size: 0.88rem;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.btn-record:hover:not(:disabled) {
  background: #1e1b4b;
  color: #c7d2fe;
}
.btn-record:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.btn-record--stop {
  border-color: #ef4444;
  color: #fca5a5;
  animation: pulse-border 1.4s ease-in-out infinite;
}
.btn-record--stop:hover {
  background: #2d0a0a;
  color: #fca5a5;
}

.rec-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ef4444;
  flex-shrink: 0;
  animation: blink 1s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.15; }
}

@keyframes pulse-border {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239,68,68,0.35); }
  50%       { box-shadow: 0 0 0 5px rgba(239,68,68,0); }
}

.level-meter {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 48px;
  padding: 4px 6px;
  background: #0b0d1f;
  border: 1px solid #1e2140;
  border-radius: 6px;
  margin-bottom: 0.5rem;
  overflow: hidden;
}

.level-bar {
  flex: 1;
  min-height: 3%;
  border-radius: 2px 2px 0 0;
  background: linear-gradient(to top, #22c55e 0%, #facc15 65%, #ef4444 100%);
  transition: height 60ms linear;
}

/* ── Documents / PDF ────────────────────────────────────────────────────── */
.nav-item--sub {
  padding-left: 1.6rem;
  font-size: 0.81rem;
  color: #4a5080;
}
.nav-item--sub.nav-item--active {
  color: #a5b4fc;
  background: #10122a;
}

.pdf-workspace {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  height: 100%;
}

.pdf-input-panel {
  background: #0d0f26;
  border: 1px solid #1e2140;
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
}

.pdf-url-input {
  width: 100%;
  box-sizing: border-box;
  padding: 0.5rem 0.75rem;
  background: #0b0d1e;
  border: 1px solid #252840;
  border-radius: 8px;
  color: #e0e0f0;
  font-size: 0.88rem;
  font-family: inherit;
  margin-bottom: 0.25rem;
}
.pdf-url-input:focus {
  outline: none;
  border-color: #4f46e5;
}

.pdf-adv-options {
  display: flex;
  gap: 0.75rem;
  margin-top: 0.6rem;
}
.pdf-adv-label {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.78rem;
  color: #7578a0;
  flex: 1;
}
.pdf-adv-input {
  padding: 0.3rem 0.5rem;
  background: #0b0d1e;
  border: 1px solid #252840;
  border-radius: 6px;
  color: #c8cadf;
  font-size: 0.82rem;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
}
.pdf-adv-input:focus {
  outline: none;
  border-color: #4f46e5;
}

.pdf-output-area {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
  flex: 1;
  min-height: 0;
}
.pdf-output-area--3col {
  grid-template-columns: 1fr 1fr 1fr;
}

.pdf-col {
  display: flex;
  flex-direction: column;
  background: #0d0f26;
  border: 1px solid #1e2140;
  border-radius: 10px;
  overflow: hidden;
  min-height: 500px;
}

.pdf-col__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.45rem 0.85rem;
  background: #0b0d1f;
  border-bottom: 1px solid #1e2140;
  font-size: 0.8rem;
  color: #7578a0;
  flex-shrink: 0;
}

.pdf-md-editor {
  flex: 1;
  resize: none;
  border: none;
  background: #090b1c;
  color: #c8cadf;
  font-family: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
  font-size: 0.82rem;
  line-height: 1.6;
  padding: 0.85rem;
  tab-size: 2;
}
.pdf-md-editor:focus { outline: none; }

.pdf-md-preview {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 1.25rem;
  font-size: 0.88rem;
  line-height: 1.7;
  color: #d4d6ee;
}

/* minimal markdown-body styles */
.markdown-body h1, .markdown-body h2, .markdown-body h3 {
  color: #a5b4fc;
  margin: 1em 0 0.4em;
  line-height: 1.3;
}
.markdown-body h1 { font-size: 1.4em; border-bottom: 1px solid #1e2140; padding-bottom: 0.25em; }
.markdown-body h2 { font-size: 1.15em; }
.markdown-body h3 { font-size: 1em; }
.markdown-body p  { margin: 0.6em 0; }
.markdown-body ul, .markdown-body ol { padding-left: 1.4em; margin: 0.5em 0; }
.markdown-body li { margin: 0.2em 0; }
.markdown-body code {
  background: #141628;
  padding: 0.15em 0.35em;
  border-radius: 4px;
  font-family: ui-monospace, monospace;
  font-size: 0.88em;
  color: #93c5fd;
}
.markdown-body pre {
  background: #0a0c1e;
  border: 1px solid #1e2140;
  border-radius: 6px;
  padding: 0.75rem 1rem;
  overflow-x: auto;
  margin: 0.75em 0;
}
.markdown-body pre code { background: none; padding: 0; color: #b0c4de; }
.markdown-body blockquote {
  border-left: 3px solid #4f46e5;
  padding-left: 0.75rem;
  margin: 0.5em 0;
  color: #7578a0;
}
.markdown-body table { border-collapse: collapse; width: 100%; margin: 0.75em 0; }
.markdown-body th, .markdown-body td { border: 1px solid #1e2140; padding: 0.4em 0.65em; }
.markdown-body th { background: #0b0d1f; color: #a5b4fc; }
.markdown-body img { max-width: 100%; border-radius: 4px; }
.markdown-body a { color: #818cf8; }

.pdf-iframe {
  flex: 1;
  border: none;
  width: 100%;
}

.pdf-converting {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: #7578a0;
  font-size: 0.88rem;
  padding: 1.5rem;
}
.pdf-progress-track {
  margin-top: 0.75rem;
  height: 6px;
  background: #1e2040;
  border-radius: 3px;
  overflow: hidden;
}
.pdf-progress-bar {
  height: 100%;
  background: linear-gradient(to right, #22c55e, #eab308, #ef4444);
  background-size: 100% 100%;
  border-radius: 3px;
  transition: width 0.9s linear;
}
.pdf-progress-label {
  margin-top: 0.3rem;
  font-size: 0.78rem;
  color: #7578a0;
  text-align: right;
}

/* ── Markdown → PDF ──────────────────────────────────────────────────────── */
.mdpdf-workspace {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: auto auto;
  gap: 1rem;
  min-height: 70vh;
}

.mdpdf-editor-pane {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  background: #0c0e22;
  border: 1px solid #1e2244;
  border-radius: 16px;
  overflow: hidden;
  padding: 0;
}

.mdpdf-editor-pane .speech-panel__header {
  flex-shrink: 0;
}

.mdpdf-editor-pane .dropzone {
  margin: 0.75rem 1.1rem 0;
  flex-shrink: 0;
}

.mdpdf-editor-pane .error {
  margin: 0 1.1rem;
}

.mdpdf-editor-pane .btn-convert {
  margin: 0 1.1rem 0.5rem;
  width: auto;
}

.mdpdf-editor-pane .pdf-progress-track {
  margin: 0 1.1rem;
}

.mdpdf-editor-pane .pdf-progress-label {
  margin: 0.3rem 1.1rem 0;
}

.mdpdf-textarea {
  flex: 1;
  min-height: 240px;
  margin: 0 1.1rem;
  background: #080919;
  color: #c8cef4;
  border: 1px solid #1e2140;
  border-radius: 8px;
  padding: 0.75rem;
  font-family: ui-monospace, 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 0.83rem;
  line-height: 1.6;
  resize: none;
  box-sizing: border-box;
  transition: border-color 0.2s;
}

.mdpdf-textarea:focus {
  outline: none;
  border-color: #4f5db0;
}

.mdpdf-preview-pane {
  display: flex;
  flex-direction: column;
  background: #0c0e22;
  border: 1px solid #1e2244;
  border-radius: 16px;
  overflow: hidden;
}

.mdpdf-pdf-pane {
  display: flex;
  flex-direction: column;
  background: #0c0e22;
  border: 1px solid #1e2244;
  border-radius: 16px;
  overflow: hidden;
  grid-column: 1 / -1;   /* spans both columns */
  height: 840px;
}

.mdpdf-pdf-pane .pdf-col__header {
  flex-shrink: 0;
}

.mdpdf-pdf-pane .pdf-iframe {
  flex: 1;
  border: none;
  width: 100%;
}

.mdpdf-markdown {
  flex: 1;
  padding: 1rem 1.25rem;
  overflow-y: auto;
  font-size: 0.88rem;
}

/* Mermaid SVGs inside the preview */
.mdpdf-markdown .mermaid svg {
  max-width: 100%;
  height: auto;
}

/* ── TTS ────────────────────────────────────────────────────────────────── */
.tts-options {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}

.tts-textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  padding: 0.6rem 0.75rem;
  border: 1.5px solid #2a2c40;
  border-radius: 6px;
  background: #1a1c2e;
  color: #c0c3e0;
  font-size: 0.9rem;
  line-height: 1.5;
  font-family: inherit;
  margin-bottom: 0.75rem;
  transition: border-color 0.2s;
}

.tts-textarea:focus {
  outline: none;
  border-color: #6366f1;
}

.tts-textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.tts-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.45rem 0.75rem;
  border-radius: 6px;
  background: #1e2035;
  color: #888ab8;
  font-size: 0.82rem;
  margin-bottom: 0.75rem;
}

.tts-status--active {
  color: #7c7fff;
  background: #1c1e3a;
}

.tts-player {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  margin-top: 0.75rem;
}

.tts-player audio {
  width: 100%;
  border-radius: 6px;
}

.btn-download {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 6px;
  background: #22243e;
  color: #818cf8;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.18s, color 0.18s;
  align-self: flex-start;
}

.btn-download:hover {
  background: #2d2f52;
  color: #a5b4fc;
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

.btn-copy {
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.btn-copy--done {
  background: #14301a !important;
  border-color: #2a6b38 !important;
  color: #4ade80 !important;
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
