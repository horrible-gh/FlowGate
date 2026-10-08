<template>
  <!-- flowgate.v02.0031 — top of the Conversation (R0001 §2): chat list, editable current
       title, new chat. Nothing of this lives in the composer below. flowgate.v02.0047: with
       no chat open the title is the draft's — the one its first send creates the chat with. -->
  <div ref="rootEl" class="conv-head" data-test="conv-head">
    <button
      type="button"
      class="conv-head-btn"
      data-test="conv-list-btn"
      :title="t('gui.conversation.header.list')"
      :aria-label="t('gui.conversation.header.list')"
      :aria-expanded="listOpen"
      :disabled="!hasScope"
      @click="toggleList"
    >
      <span aria-hidden="true">☰</span>
    </button>
    <input
      ref="titleEl"
      v-model="titleDraft"
      class="conv-title-input"
      data-test="conv-title-input"
      :aria-label="t('gui.conversation.header.title_label')"
      :title="t('gui.conversation.header.title_edit')"
      :placeholder="hasScope ? t('gui.conversation.header.no_chat') : t('gui.conversation.header.no_flow')"
      :disabled="(!activeChat && !draft) || renaming || readOnly"
      maxlength="100"
      @focus="editBase = titleDraft"
      @keydown.enter.prevent="titleEl?.blur()"
      @keydown.esc.prevent="revertTitle"
      @blur="commitTitle"
    />
    <button
      type="button"
      class="conv-head-btn conv-head-btn--new"
      data-test="conv-new-btn"
      :title="t('gui.conversation.header.new')"
      :aria-label="t('gui.conversation.header.new')"
      :disabled="!hasScope || creating || readOnly"
      @click="emit('create')"
    >
      <span aria-hidden="true">{{ creating ? '…' : '＋' }}</span>
    </button>

    <div v-if="listOpen" class="conv-list-pop" data-test="conv-list-pop" role="listbox" :aria-label="t('gui.conversation.header.list')">
      <p v-if="chatsStatus === 'loading'" class="conv-list-state">{{ t('gui.conversation.header.list_loading') }}</p>
      <p v-else-if="chatsStatus === 'error'" class="conv-list-state conv-list-state--error">
        {{ t('gui.conversation.header.list_failed') }}
        <button type="button" class="conv-list-retry" @click="emit('reload')">{{ t('gui.conversation.retry') }}</button>
      </p>
      <p v-else-if="chats.length === 0" class="conv-list-state">{{ t('gui.conversation.header.list_empty') }}</p>
      <button
        v-for="chat in chats"
        :key="chat.id"
        type="button"
        role="option"
        class="conv-list-item"
        :class="{ 'is-on': chat.id === activeChat?.id }"
        :aria-selected="chat.id === activeChat?.id"
        data-test="conv-list-item"
        :data-chat-id="chat.id"
        @click="pick(chat.id)"
      >
        <span class="conv-list-mark" aria-hidden="true">●</span>
        <span class="conv-list-name">{{ chat.title }}</span>
        <span class="conv-list-meta">{{ metaOf(chat) }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { chatListDate } from './timeline'
import type { ChatSummary } from './types'

const props = defineProps<{
  chats: readonly ChatSummary[]
  chatsStatus: string
  activeChat: ChatSummary | null
  hasScope: boolean
  creating: boolean
  renaming: boolean
  readOnly?: boolean
  /** No chat is open and the composer starts a new one. */
  draft?: boolean
  /** The title shown: the open chat's, or the draft's. */
  title: string
}>()

const emit = defineEmits<{
  select: [chatId: string]
  rename: [title: string]
  create: []
  reload: []
}>()

const { t } = useI18n()

const rootEl = ref<HTMLElement | null>(null)
const titleEl = ref<HTMLInputElement | null>(null)
const listOpen = ref(false)
const titleDraft = ref(props.title)
// The text the current edit started from.
const editBase = ref(props.title)

// The input follows the shown title (switch, rename confirmed or failed, new chat, AI title)
// unless it is being edited.
watch(
  () => [props.activeChat?.id, props.title, props.renaming, props.draft] as const,
  () => {
    if (document.activeElement === titleEl.value) return
    titleDraft.value = props.title
    editBase.value = props.title
  },
)

// Enter blurs, blur commits — one rename per edit. An empty title is never sent, and an edit
// that changed nothing sends nothing (a title that arrived meanwhile then shows).
function commitTitle(): void {
  if (!props.activeChat && !props.draft) return
  const next = titleDraft.value.trim()
  if (!next || next === editBase.value.trim()) {
    titleDraft.value = props.title
    return
  }
  if (next !== props.title) emit('rename', next)
}

// Esc drops the edit; the blur that follows then has nothing to commit.
function revertTitle(): void {
  titleDraft.value = props.title
  editBase.value = props.title
  titleEl.value?.blur()
}

function toggleList(): void {
  listOpen.value = !listOpen.value
  if (listOpen.value) emit('reload')
}

function pick(chatId: string): void {
  listOpen.value = false
  if (chatId !== props.activeChat?.id) emit('select', chatId)
}

function metaOf(chat: ChatSummary): string {
  if (chat.id === props.activeChat?.id) return t('gui.conversation.header.current')
  const d = chatListDate(chat.updatedAt)
  if (!d) return ''
  if (d.kind === 'yesterday') return t('gui.conversation.header.yesterday')
  return d.text
}

function onDocPointer(event: PointerEvent): void {
  if (listOpen.value && rootEl.value && !rootEl.value.contains(event.target as Node)) listOpen.value = false
}

function onDocKey(event: KeyboardEvent): void {
  if (listOpen.value && event.key === 'Escape') listOpen.value = false
}

onMounted(() => {
  document.addEventListener('pointerdown', onDocPointer)
  document.addEventListener('keydown', onDocKey)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointer)
  document.removeEventListener('keydown', onDocKey)
})
</script>

<style scoped>
.conv-head {
  position: relative;
  flex: 0 0 auto;
  height: 42px;
  padding: 6px 7px;
  display: flex;
  align-items: center;
  gap: 6px;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
}

.conv-head-btn {
  flex: 0 0 auto;
  width: 29px;
  height: 29px;
  padding: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-muted);
  display: grid;
  place-items: center;
  font-size: 13px;
  cursor: pointer;
}

.conv-head-btn:hover:not(:disabled) {
  color: var(--fg2-text);
  border-color: var(--fg2-control-hover-border);
}

.conv-head-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.conv-head-btn--new {
  border-color: var(--fg2-blue);
  color: var(--fg2-status-info-fg);
  font-size: 16px;
}

.conv-head-btn:focus-visible,
.conv-list-item:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.conv-title-input {
  min-width: 0;
  flex: 1;
  height: 29px;
  border: 1px solid transparent;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-text);
  padding: 0 7px;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  outline: 0;
  text-overflow: ellipsis;
}

.conv-title-input:hover:not(:disabled) {
  border-color: var(--fg2-line);
}

.conv-title-input:focus {
  border-color: var(--fg2-focus);
  background: var(--fg2-control-surface);
}

.conv-list-pop {
  position: absolute;
  left: 7px;
  right: 7px;
  top: 38px;
  z-index: 20;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
  padding: 5px;
  max-height: 220px;
  overflow: auto;
}

.conv-list-item {
  width: 100%;
  min-height: 32px;
  padding: 6px 8px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  text-align: left;
  display: flex;
  align-items: center;
  gap: 7px;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.conv-list-item:hover {
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
}

.conv-list-item.is-on {
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
}

.conv-list-mark {
  flex: 0 0 auto;
  color: var(--fg2-status-info-fg);
  font-size: 8px;
}

.conv-list-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-list-meta {
  flex: 0 0 auto;
  color: var(--fg2-muted-2);
  font-size: 10px;
}

.conv-list-state {
  margin: 0;
  padding: 8px;
  color: var(--fg2-muted);
  font-size: 11px;
}

.conv-list-state--error {
  color: var(--fg2-status-danger-fg);
}

.conv-list-retry {
  margin-left: 6px;
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  cursor: pointer;
  text-decoration: underline;
}
</style>
