<template>
  <!-- F86 — the open-source license window: one modeless window (the background stays usable), dragged by its title bar,
       closed with ✕ / ESC. Tabs: every voice engine of the catalog, then 'FlowGate 전체'. Opened only through
       `openLicenseWindow` (licenseWindow.ts); App.vue mounts it once. Every word comes from gui.licenses.*. -->
  <ModelessShell
    ref="shell"
    :open="win.open"
    :title="t('gui.licenses.window_title')"
    :close-label="t('gui.licenses.close')"
    kind="document"
    kind-color="#94a3b8"
    :initial-x="initialX"
    :initial-y="72"
    :initial-width="640"
    :initial-height="480"
    :min-width="380"
    :min-height="260"
    @close="closeLicenseWindow"
  >
    <div class="lw" data-test="license-window-body">
      <Tabs :tabs="tabs" :model-value="win.tab" :aria-label="t('gui.licenses.window_title')" class="lw-tabs" @update:model-value="selectLicenseTab" />

      <!-- FlowGate 전체: the web bundle's third-party license texts and the project LICENSE, read from the served files. -->
      <div v-if="win.tab === LICENSE_PROJECT_TAB" class="lw-pane" data-test="license-project">
        <section v-for="doc in projectDocs" :key="doc.key" class="lw-doc" :data-doc="doc.key">
          <h4>{{ t(`gui.licenses.${doc.key}`) }}</h4>
          <p v-if="doc.state === 'loading'" class="lw-muted" role="status">{{ t('gui.licenses.loading') }}</p>
          <p v-else-if="doc.state === 'failed'" class="lw-error" role="alert">{{ t('gui.licenses.load_failed') }}</p>
          <pre v-else class="lw-text">{{ doc.text }}</pre>
        </section>
      </div>

      <!-- An engine: one table of its files and their licenses, then how files are obtained and the engine's notices. -->
      <div v-else class="lw-pane" :data-engine="win.tab">
        <p v-if="!voice.loaded && !voice.loadError" class="lw-muted" role="status">{{ t('gui.licenses.loading') }}</p>
        <p v-else-if="!engine" class="lw-error" role="alert">{{ t('gui.licenses.load_failed') }}</p>
        <template v-else>
          <table class="lw-table" data-test="license-engine-table">
            <thead>
              <tr>
                <th>{{ t('gui.licenses.col_component') }}</th>
                <th>{{ t('gui.licenses.col_file') }}</th>
                <th>{{ t('gui.licenses.col_license') }}</th>
                <th>{{ t('gui.licenses.col_source') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(component, index) in engine.components ?? []" :key="`${component.filename}-${index}`">
                <td>{{ componentTypeLabel(component.componentType) }}</td>
                <td class="lw-file">{{ component.filename }}</td>
                <td>
                  <span class="lw-pill" :class="{ pending: isPending(component.license) }">{{ isPending(component.license) ? t('gui.licenses.pending') : component.license }}</span>
                </td>
                <td>
                  <a v-if="component.licenseUrl" class="lw-source" :href="component.licenseUrl" target="_blank" rel="noopener noreferrer">{{ t('gui.licenses.source_link') }}</a>
                </td>
              </tr>
            </tbody>
          </table>
          <p class="lw-note" data-test="license-direct-download">{{ t('gui.licenses.direct_download') }}</p>
          <p v-for="key in engine.noticeKeys ?? []" :key="key" class="lw-note" data-test="license-notice">{{ t(`gui.licenses.notice.${key}`) }}</p>
        </template>
      </div>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Tabs, { type TabItem } from '@/components/ui/Tabs.vue'
import { findService, loadVoice, useVoice } from '@/features/conversation/voice/voiceStore'
import { LICENSE_PROJECT_TAB, closeLicenseWindow, licenseWindowState as win, selectLicenseTab } from './licenseWindow'

const { t, te } = useI18n({ useScope: 'global' })
const { state: voice } = useVoice()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)

const initialX = typeof window === 'undefined' ? 120 : Math.max(16, Math.round(window.innerWidth / 2 - 320))

const tabs = computed<TabItem[]>(() => [
  ...voice.catalog.map((service) => ({ key: service.id, label: service.name })),
  { key: LICENSE_PROJECT_TAB, label: t('gui.licenses.tab_project') },
])

const engine = computed(() => (win.tab === LICENSE_PROJECT_TAB ? null : findService(voice.catalog, win.tab)))

/** `component_type.*` names every component kind the catalog uses; an unknown one reads as 기타. */
function componentTypeLabel(type: string): string {
  const key = `gui.licenses.component_type.${type}`
  return type && te(key) ? t(key) : t('gui.licenses.component_type.other')
}

/** No license yet (empty or the server's "pending" wording) shows 확인 중. */
function isPending(license: string | null | undefined): boolean {
  const value = (license ?? '').trim()
  return value === '' || /^pending/i.test(value)
}

// ── FlowGate 전체: read once per window session; a failed read is tried again on the next open. ──────────
type DocState = 'idle' | 'loading' | 'ready' | 'failed'
const projectDocs = reactive([
  { key: 'project_third_party', file: 'THIRD_PARTY_NOTICES.txt', state: 'idle' as DocState, text: '' },
  { key: 'project_license', file: 'LICENSE.txt', state: 'idle' as DocState, text: '' },
])

async function loadDoc(doc: (typeof projectDocs)[number]): Promise<void> {
  if (doc.state === 'loading' || doc.state === 'ready') return
  doc.state = 'loading'
  try {
    const response = await fetch(`${import.meta.env.BASE_URL}${doc.file}`, { credentials: 'same-origin' })
    if (!response.ok) throw new Error(String(response.status))
    const type = response.headers.get('content-type') ?? ''
    // A missing file can come back as the app's index.html (SPA fallback); that is not a license text.
    if (type.includes('text/html')) throw new Error('not a text file')
    doc.text = await response.text()
    doc.state = 'ready'
  } catch {
    doc.state = 'failed'
  }
}

watch(() => [win.open, win.tab] as const, ([open, tab]) => {
  if (!open) return
  if (tab === LICENSE_PROJECT_TAB) projectDocs.forEach((doc) => void loadDoc(doc))
  else void loadVoice()
}, { immediate: true })

// A second open request while the window is already open brings it to the front.
watch(() => win.seq, () => {
  if (win.open) void nextTick(() => shell.value?.bringToFront())
})
</script>

<style scoped>
.lw {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100%;
  font-family: var(--fg2-font);
  font-size: 13px;
  color: var(--fg2-text);
}

.lw-tabs {
  flex: none;
  flex-wrap: wrap;
}

.lw-pane {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.lw-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
}

.lw-table th {
  padding: 6px 8px;
  border-bottom: 1px solid var(--fg2-line-strong);
  color: var(--fg2-muted);
  font-size: 12px;
  font-weight: 600;
  text-align: left;
}

.lw-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--fg2-line);
  vertical-align: top;
}

.lw-file {
  overflow-wrap: anywhere;
}

.lw-pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font-size: 12px;
  white-space: nowrap;
}

.lw-pill.pending {
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.lw-source {
  color: var(--fg2-status-info-fg);
  text-decoration: none;
  white-space: nowrap;
}

.lw-source:hover {
  color: var(--fg2-text);
}

.lw-source:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
  border-radius: 3px;
}

.lw-note {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 12.5px;
  line-height: 1.55;
}

.lw-muted {
  margin: 0;
  color: var(--fg2-muted);
}

.lw-error {
  margin: 0;
  color: #fb7a80;
}

.lw-doc h4 {
  margin: 4px 0 6px;
  font-size: 13px;
  font-weight: 650;
}

.lw-text {
  max-height: 260px;
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: color-mix(in srgb, var(--fg2-bg) 70%, transparent);
  color: var(--fg2-text);
  font-family: var(--fg2-font);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
