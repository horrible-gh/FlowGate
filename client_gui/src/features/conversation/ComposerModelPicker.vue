<template>
  <!-- the composer's compact AI picker: the selected
       provider's name on a small button, the project's providers in a popover above it.
       The list is the providers call as is; nothing here is a fixed model list.
       flowgate.v02test.0096 ①: the popover starts with 자주 쓰는 AI (my default AI, 📌 pinned AI, the last 3 used),
       then — after a separator — 내 AI and 시스템 AI under their own headings, each AI once. Opening it scrolls the
       selected AI into view; with more than 8 AI a search field (name, part match, ↑↓ / Enter / Esc) leads it. -->
  <div ref="rootEl" class="cmp-model">
    <button
      type="button"
      class="cmp-model-btn"
      data-test="conv-provider-btn"
      aria-haspopup="listbox"
      :title="label"
      :aria-label="`${t('gui.conversation.composer.provider')}: ${label}`"
      :aria-expanded="open"
      :disabled="disabled || providers.length === 0"
      @click="toggle"
    >
      <span class="cmp-model-mark" aria-hidden="true">◆</span>
      <span class="cmp-model-name" data-test="conv-provider-name">{{ label }}</span>
      <span class="cmp-model-arrow" aria-hidden="true">▾</span>
    </button>
    <div v-if="open" class="cmp-model-pop" data-test="conv-provider-pop" @keydown="onPopKey">
      <input
        v-if="searchable"
        ref="searchEl"
        v-model="query"
        type="search"
        class="cmp-model-search"
        data-test="conv-provider-search"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="true"
        :aria-controls="listId"
        :aria-activedescendant="activeId ? optionDomId(activeId) : undefined"
        :placeholder="t('gui.conversation.composer.provider_search')"
        :aria-label="t('gui.conversation.composer.provider_search')"
        @keydown="onSearchKey"
      />
      <div :id="listId" ref="listEl" class="cmp-model-list" role="listbox" :aria-label="t('gui.conversation.composer.provider')">
        <div v-if="!visibleIds.length" class="cmp-model-empty" data-test="conv-provider-empty">
          {{ t('gui.conversation.composer.provider_search_empty') }}
        </div>
        <template v-for="(section, index) in sections" :key="section.key">
          <div v-if="index > 0 && section.key !== 'frequent' && sections[index - 1].key === 'frequent'" class="cmp-model-sep" role="separator" />
          <div role="group" :aria-label="section.title" :data-test="`conv-provider-group-${section.key}`">
            <div class="cmp-model-head" aria-hidden="true">{{ section.title }}</div>
            <button
              v-for="p in section.items"
              :id="optionDomId(p.id)"
              :key="p.id"
              type="button"
              role="option"
              class="cmp-model-opt"
              :class="{ 'is-on': p.id === selectedId, 'is-active': p.id === activeId }"
              :aria-selected="p.id === selectedId"
              :title="p.name"
              data-test="conv-provider-option"
              :data-provider-id="p.id"
              :data-section="section.key"
              @click="pick(p.id)"
              @mousemove="activeId = p.id"
            >
              <span class="cmp-model-opt-name">{{ p.name }}</span>
              <span
                v-if="section.key === 'frequent' && p.id === defaultId"
                class="cmp-model-tag"
                data-test="conv-provider-default"
              >{{ t('gui.conversation.composer.provider_default') }}</span>
              <span
                v-if="isPinned(p.id)"
                class="cmp-model-pin"
                role="img"
                data-test="conv-provider-pinned"
                :aria-label="t('gui.conversation.composer.provider_pinned')"
                :title="t('gui.conversation.composer.provider_pinned')"
              >📌</span>
              <span class="cmp-model-check" aria-hidden="true">✓</span>
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import { frequentAiIds, useAiFavorites } from '@shared/aiFavorites'
import { usePopover } from './popover'
import type { ChatProvider } from './types'

/** More AI than this and the popover leads with a search field. */
const SEARCH_FROM = 8

const props = defineProps<{
  providers: readonly ChatProvider[]
  selectedId: string
  status: string
  disabled?: boolean
  /** My default AI (the providers call's default_provider_id); first in 자주 쓰는 AI. */
  defaultId?: string | null
}>()

const emit = defineEmits<{ select: [providerId: string] }>()

const { t } = useI18n()
const { user } = useCurrentUser()
const favorites = useAiFavorites(() => user.value?.username)

const rootEl = ref<HTMLElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
const searchEl = ref<HTMLInputElement | null>(null)
const { open, toggle, close } = usePopover(rootEl)
const query = ref('')
const activeId = ref<string | null>(null)
const listId = `cmp-model-list-${Math.random().toString(36).slice(2, 9)}`

const label = computed(() => {
  const selected = props.providers.find((p) => p.id === props.selectedId)
  if (selected) return selected.name
  if (props.status === 'loading') return t('gui.conversation.composer.provider_loading')
  if (props.status === 'error') return t('gui.conversation.composer.provider_failed')
  return t('gui.conversation.composer.provider_none')
})

const searchable = computed(() => props.providers.length > SEARCH_FROM)

interface Section {
  key: 'frequent' | 'mine' | 'system'
  title: string
  items: ChatProvider[]
}

function isPinned(id: string): boolean {
  return favorites.pinned.value.includes(id)
}

// 자주 쓰는 AI first, then 내 AI, then 시스템 AI; each AI once, the providers call's order kept inside a group.
// An AI the server did not mark as mine counts as a system AI (an older server marks none).
const sections = computed<Section[]>(() => {
  const byId = new Map(props.providers.map((p) => [p.id, p]))
  const frequent = frequentAiIds({
    defaultId: props.defaultId,
    pinned: favorites.pinned.value,
    recent: favorites.recent.value,
    available: byId.keys(),
  })
  const taken = new Set(frequent)
  const needle = query.value.trim().toLowerCase()
  const matches = (p: ChatProvider) => !needle || p.name.toLowerCase().includes(needle)
  const rest = props.providers.filter((p) => !taken.has(p.id))
  const all: Section[] = [
    { key: 'frequent', title: t('gui.conversation.composer.provider_frequent'), items: frequent.map((id) => byId.get(id)!) },
    { key: 'mine', title: t('gui.conversation.composer.provider_mine'), items: rest.filter((p) => p.owner === 'user') },
    { key: 'system', title: t('gui.conversation.composer.provider_system'), items: rest.filter((p) => p.owner !== 'user') },
  ]
  return all.map((s) => ({ ...s, items: s.items.filter(matches) })).filter((s) => s.items.length > 0)
})

const visibleIds = computed(() => sections.value.flatMap((s) => s.items.map((p) => p.id)))

function optionDomId(id: string): string {
  return `${listId}-${id.replace(/[^A-Za-z0-9_-]/g, '_')}`
}

function optionEl(id: string | null): HTMLElement | null {
  if (!id || !listEl.value) return null
  // optionDomId leaves only [A-Za-z0-9_-], so the attribute selector needs no escaping.
  return listEl.value.querySelector<HTMLElement>(`[id="${optionDomId(id)}"]`)
}

function reveal(id: string | null): void {
  const el = optionEl(id)
  // block: 'nearest' moves the list only as far as needed; jsdom has no scrollIntoView.
  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' })
}

// Opening shows the selected AI at once (scrolled into view) and puts the keyboard in the search field.
watch(open, async (isOpen) => {
  if (!isOpen) {
    query.value = ''
    return
  }
  activeId.value = visibleIds.value.includes(props.selectedId) ? props.selectedId : visibleIds.value[0] ?? null
  await nextTick()
  reveal(activeId.value)
  searchEl.value?.focus()
})

// A search keeps the highlight on a shown AI.
watch(visibleIds, (ids) => {
  if (!activeId.value || !ids.includes(activeId.value)) activeId.value = ids[0] ?? null
})

function move(delta: number): void {
  const ids = visibleIds.value
  if (!ids.length) return
  const at = activeId.value ? ids.indexOf(activeId.value) : -1
  const next = at < 0 ? (delta > 0 ? 0 : ids.length - 1) : Math.min(ids.length - 1, Math.max(0, at + delta))
  activeId.value = ids[next]
  reveal(activeId.value)
}

function onSearchKey(event: KeyboardEvent): void {
  // Esc with a search typed clears it first; the next Esc closes the popover (usePopover).
  if (event.key === 'Escape' && query.value) {
    event.preventDefault()
    event.stopPropagation()
    query.value = ''
  }
}

function onPopKey(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    move(event.key === 'ArrowDown' ? 1 : -1)
  } else if (event.key === 'Enter' && event.target === searchEl.value) {
    event.preventDefault()
    if (activeId.value) pick(activeId.value)
  }
}

function pick(providerId: string): void {
  close()
  favorites.recordUse(providerId)
  if (providerId !== props.selectedId) emit('select', providerId)
}
</script>

<style scoped>
.cmp-model {
  position: relative;
  /* sized by the name (up to 180px, never past ~45% of the composer) so a usual AI name is not cut; a longer one
     is clipped with an ellipsis and read in full from the button's tooltip. */
  flex: 0 1 auto;
  min-width: 52px;
  max-width: min(180px, 45cqw);
}

.cmp-model-btn {
  width: 100%;
  height: 27px;
  min-width: 0;
  padding: 0 7px;
  display: flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font: inherit;
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
}

.cmp-model-btn:hover:not(:disabled),
.cmp-model-btn[aria-expanded='true'] {
  border-color: var(--fg2-control-hover-border);
}

.cmp-model-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.cmp-model-mark {
  flex: 0 0 auto;
  color: var(--fg2-violet);
  font-size: 11px;
}

.cmp-model-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.cmp-model-arrow {
  flex: 0 0 auto;
  color: var(--fg2-muted-2);
  font-size: 9px;
}

.cmp-model-pop {
  position: absolute;
  left: 0;
  bottom: calc(100% + 4px);
  z-index: 30;
  /* wide enough for a full provider name; a longer one wraps instead of being cut.
     The picker is the first item of the composer row, so the popover starts at the composer's
     content edge; 100cqw (the composer is an inline-size container) keeps its right edge inside
     the panel instead of past the screen edge (360px in a ~300px panel ran ~69px off-screen). */
  box-sizing: border-box;
  width: max-content;
  min-width: min(220px, 100cqw);
  max-width: min(360px, 100cqw);
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
}

.cmp-model-search {
  box-sizing: border-box;
  width: 100%;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font: inherit;
  font-size: 12px;
}

.cmp-model-list {
  max-height: 300px;
  overflow: auto;
}

.cmp-model-head {
  padding: 6px 8px 3px;
  color: var(--fg2-muted-2);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.cmp-model-sep {
  height: 1px;
  margin: 4px 2px;
  background: var(--fg2-line-strong);
}

.cmp-model-empty {
  padding: 8px;
  color: var(--fg2-muted-2);
  font-size: 12px;
}

.cmp-model-opt {
  width: 100%;
  min-height: 32px;
  padding: 6px 8px;
  display: flex;
  align-items: center;
  gap: 7px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-text);
  text-align: left;
  font: inherit;
  font-size: 12px;
  line-height: 1.35;
  cursor: pointer;
}

.cmp-model-opt:hover,
.cmp-model-opt.is-active {
  background: var(--fg2-status-info-bg);
}

/* The selected provider is told apart from a hovered one: its own border, weight and check. */
.cmp-model-opt.is-on {
  background: var(--fg2-status-info-bg);
  box-shadow: inset 0 0 0 1px var(--fg2-status-info-border);
  font-weight: 700;
}

.cmp-model-opt-name {
  min-width: 0;
  flex: 1;
  white-space: normal;
  overflow-wrap: anywhere;
}

.cmp-model-tag {
  flex: 0 0 auto;
  padding: 0 5px;
  border: 1px solid var(--fg2-status-info-border);
  border-radius: 999px;
  color: var(--fg2-status-info-fg);
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
}

.cmp-model-pin {
  flex: 0 0 auto;
  font-size: 11px;
}

.cmp-model-check {
  flex: 0 0 auto;
  color: var(--fg2-status-info-fg);
  font-size: 13px;
  visibility: hidden;
}

.cmp-model-opt.is-on .cmp-model-check {
  visibility: visible;
}

.cmp-model-btn:focus-visible,
.cmp-model-opt:focus-visible,
.cmp-model-search:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
