<template>
  <!-- flowgate.v02.0038 — Header search assist (0001-R §2.2). Focus opens a panel with the
       searchable scopes and clickable examples; the chosen scope stays on the input as a chip
       and in the placeholder after the panel closes. Enter hands { scope, query } out — there
       is no search API or result screen yet (later Feature). -->
  <div ref="root" class="global-search" role="search" data-test="designer-search" @click="onRootClick">
    <svg class="global-search-icon" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.5" />
      <path d="M10.5 10.5L14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
    </svg>
    <button
      type="button"
      class="global-search-chip"
      :aria-label="t('gui.designer.top.search.scope_aria', { scope: scopeLabel(scope) })"
      :aria-expanded="open ? 'true' : 'false'"
      aria-haspopup="true"
      data-test="designer-search-scope-chip"
      :data-scope="scope"
      @click.stop="openPanel"
    >
      {{ scopeLabel(scope) }} <span aria-hidden="true">▾</span>
    </button>
    <input
      ref="input"
      v-model="query"
      class="global-search-input"
      type="search"
      autocomplete="off"
      :placeholder="t(`gui.designer.top.search.placeholders.${scope}`)"
      :aria-label="t('gui.designer.top.search.label')"
      data-test="designer-search-input"
      @focus="openPanel"
      @keydown.enter.prevent="submit"
      @keydown.esc.prevent="close"
    />

    <div v-if="open" class="global-search-pop" data-test="designer-search-assist">
      <section class="global-search-section">
        <div class="global-search-title">{{ t('gui.designer.top.search.scopes_title') }}</div>
        <div class="global-search-scopes" role="radiogroup" :aria-label="t('gui.designer.top.search.scopes_title')">
          <button
            v-for="key in DESIGNER_SEARCH_SCOPES"
            :key="key"
            type="button"
            role="radio"
            class="global-search-scope"
            :class="{ 'is-on': key === scope }"
            :aria-checked="key === scope ? 'true' : 'false'"
            data-test="designer-search-scope"
            :data-scope="key"
            @click="selectScope(key)"
          >
            {{ scopeLabel(key) }}
          </button>
        </div>
      </section>
      <section class="global-search-section">
        <div class="global-search-title">{{ t('gui.designer.top.search.examples_title') }}</div>
        <div class="global-search-examples">
          <button
            v-for="example in DESIGNER_SEARCH_EXAMPLES"
            :key="example"
            type="button"
            class="global-search-example"
            data-test="designer-search-example"
            :data-example="example"
            @click="useExample(t(`gui.designer.top.search.example_values.${example}`))"
          >
            {{ t(`gui.designer.top.search.examples.${example}`) }}
          </button>
        </div>
        <div class="global-search-help">{{ t('gui.designer.top.search.help') }}</div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  DESIGNER_SEARCH_EXAMPLES,
  DESIGNER_SEARCH_SCOPES,
  type DesignerSearchRequest,
  type DesignerSearchScope,
} from './designerTop'

const emit = defineEmits<{ search: [request: DesignerSearchRequest] }>()

const { t } = useI18n()

const root = ref<HTMLElement | null>(null)
const input = ref<HTMLInputElement | null>(null)

// Screen state of this part only: what is typed, which scope is chosen, whether the panel shows.
const query = ref('')
const scope = ref<DesignerSearchScope>('all')
const open = ref(false)

const scopeLabel = (key: DesignerSearchScope) => t(`gui.designer.top.search.scopes.${key}`)

function openPanel() {
  open.value = true
  input.value?.focus()
}

function close() {
  open.value = false
  input.value?.blur()
}

// A click anywhere on the bar (outside the panel's own buttons) focuses the input, as drawn.
function onRootClick(event: MouseEvent) {
  if ((event.target as Element | null)?.closest('.global-search-pop')) return
  openPanel()
}

function selectScope(key: DesignerSearchScope) {
  scope.value = key
  input.value?.focus()
}

function useExample(value: string) {
  query.value = value
  const el = input.value
  if (!el) return
  el.focus()
  el.setSelectionRange(value.length, value.length)
}

function submit() {
  const text = query.value.trim()
  if (text) emit('search', { scope: scope.value, query: text })
}

function onDocumentPointerDown(event: Event) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) open.value = false
}

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocumentPointerDown))
</script>

<style scoped>
.global-search {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: #0d1d31;
  color: var(--fg2-muted);
  box-sizing: border-box;
}

.global-search-icon {
  flex: none;
  width: 14px;
  height: 14px;
}

.global-search-chip {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 8px;
  border: 1px solid #2c5277;
  border-radius: var(--fg2-control-radius-pill);
  background: #112944;
  color: #a9d2f7;
  font: inherit;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}

.global-search-input {
  flex: 1 1 auto;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: #d9e5f1;
  font: inherit;
  font-size: 12px;
}

.global-search-input::placeholder {
  color: #7189a2;
}

.global-search-pop {
  position: absolute;
  top: 40px;
  right: 0;
  left: 0;
  z-index: 10;
  padding: 10px;
  border: 1px solid #31577f;
  border-radius: var(--fg2-control-radius-lg);
  background: #0c1c2e;
  box-shadow: 0 14px 36px rgba(0, 0, 0, 0.6);
}

.global-search-section + .global-search-section {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--fg2-line);
}

.global-search-title {
  margin-bottom: 7px;
  color: #7891aa;
  font-size: 11px;
  font-weight: 800;
}

.global-search-scopes {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.global-search-scope {
  height: 27px;
  padding: 0 10px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  background: #10223a;
  color: #91a8bf;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.global-search-scope.is-on {
  border-color: #3277d4;
  background: #15345a;
  color: #d8ebff;
}

.global-search-examples {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.global-search-example {
  min-width: 0;
  height: 31px;
  padding: 0 9px;
  overflow: hidden;
  border: 1px solid #203a56;
  border-radius: 6px;
  background: #0d1d31;
  color: #9fb3c8;
  font: inherit;
  font-size: 11px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.global-search-example:hover {
  border-color: #315b82;
  background: #112941;
  color: #e0edf8;
}

.global-search-help {
  margin-top: 7px;
  color: #607b98;
  font-size: 10.5px;
}

.global-search-chip:focus-visible,
.global-search-scope:focus-visible,
.global-search-example:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
