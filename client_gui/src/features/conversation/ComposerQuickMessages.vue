<template>
  <!-- flowgate.v02test.0072 (시안 v7): one row of quick message buttons right above the chat input. Off = grey text
       and grey border; on = red (계속) or blue (1회) text and border; never a filled background, never a kind mark.
       Hidden while loading, when loading failed and without project.settings.read. Card 3: with project.settings.edit
       the right end of the row has ＋ 추가 and ✎ 편집 (✓ 완료 while editing); a right click on a button also opens its
       edit window. While editing the buttons are dotted: click = edit window, ✕ = delete (asks once), drag = order. -->
  <div
    v-if="visible"
    class="conv-quick"
    role="toolbar"
    :aria-label="t('gui.quickMessages.row_label')"
    data-test="conv-quick-row"
  >
    <div class="conv-quick-scroll">
      <span
        v-for="(item, index) in items"
        :key="item.id"
        class="conv-quick-item"
        :class="{ 'is-dragging': dragId === item.id, 'is-drop-target': dragId !== null && dragId !== item.id && dropIndex === index }"
        :draggable="editing"
        data-test="conv-quick-item"
        @dragstart="onDragStart($event, item)"
        @dragover="onDragOver($event, index)"
        @drop="onDrop($event, index)"
        @dragend="endDrag"
        @contextmenu="onContext($event, item)"
      >
        <button
          type="button"
          class="conv-quick-btn"
          :class="[isOn(item) ? `is-on-${item.kind}` : null, { 'is-editing': editing }]"
          :data-state="isOn(item) ? 'on' : 'off'"
          :aria-pressed="editing ? undefined : isOn(item)"
          :title="tip(item)"
          :disabled="disabled && !editing"
          data-test="conv-quick-btn"
          @click="onPress(item)"
          @keydown="onItemKey($event, item, index)"
        >{{ item.title }}</button>
        <button
          v-if="editing"
          type="button"
          class="conv-quick-del"
          :aria-label="t('gui.quickMessages.delete_item', { title: item.title })"
          data-test="conv-quick-del"
          @click.stop="confirmItem = item"
        >✕</button>
      </span>
      <span v-if="items.length === 0" class="conv-quick-empty" data-test="conv-quick-empty">{{ t('gui.quickMessages.empty') }}</span>
    </div>
    <span v-if="notice" class="conv-quick-notice" role="status" data-test="conv-quick-notice">{{ notice }}</span>
    <div v-if="canEdit" class="conv-quick-actions">
      <button type="button" class="conv-quick-act" data-test="conv-quick-add" @click="openDialog(null)">{{ t('gui.quickMessages.add') }}</button>
      <button
        v-if="items.length > 0 || editing"
        type="button"
        class="conv-quick-act"
        :class="{ 'is-active': editing }"
        data-test="conv-quick-edit"
        @click="emit('update:editing', !editing)"
      >{{ editing ? t('gui.quickMessages.done') : t('gui.quickMessages.edit') }}</button>
    </div>

    <QuickMessageDialog :open="dialogOpen" :item="dialogItem" :save="saveFromDialog" @cancel="dialogOpen = false" @saved="dialogOpen = false" />
    <ConfirmDialog
      :open="confirmItem !== null"
      danger
      :title="t('gui.quickMessages.delete_heading')"
      :message="confirmItem ? t('gui.quickMessages.delete_body', { title: confirmItem.title }) : ''"
      :confirm-label="t('gui.quickMessages.delete_confirm')"
      :cancel-label="t('gui.quickMessages.cancel')"
      :busy="deleting"
      @cancel="confirmItem = null"
      @confirm="doDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ConfirmDialog from '../../components/ui/ConfirmDialog.vue'
import QuickMessageDialog from './QuickMessageDialog.vue'
import type { QuickMessage, QuickMessageInput } from './quickMessages/api'
import { installQuickMessages } from './quickMessages/messages'
import type { QuickMoveResult } from './quickMessages/useQuickMessages'

const props = defineProps<{
  items: readonly QuickMessage[]
  visible: boolean
  isOn: (item: QuickMessage) => boolean
  disabled?: boolean
  /** project.settings.edit: ＋ 추가 / ✎ 편집 / right-click edit are shown. */
  canEdit?: boolean
  editing?: boolean
  /** Stores a new (`id` null) or changed button; true when stored. */
  save?: (input: QuickMessageInput, id: number | null) => Promise<boolean>
  /** Deletes a button; true when it is gone. */
  remove?: (id: number) => Promise<boolean>
  /** Puts a button at a 0-based position of the row; says whether it was saved, ignored (still saving) or went back. */
  move?: (id: number, toIndex: number) => Promise<QuickMoveResult>
}>()

const emit = defineEmits<{ toggle: [id: number]; 'update:editing': [value: boolean] }>()

installQuickMessages(useI18n({ useScope: 'global' }))
const { t } = useI18n()

const tip = (item: QuickMessage) =>
  t(item.kind === 'sticky' ? 'gui.quickMessages.tip_sticky' : 'gui.quickMessages.tip_once', { text: item.message })

// ── the add / edit window ──
const dialogOpen = ref(false)
const dialogItem = ref<QuickMessage | null>(null)

function openDialog(item: QuickMessage | null): void {
  if (!props.canEdit) return
  dialogItem.value = item
  dialogOpen.value = true
}

const saveFromDialog = (input: QuickMessageInput): Promise<boolean> =>
  props.save ? props.save(input, dialogItem.value?.id ?? null) : Promise.resolve(false)

/** A press toggles the button; while editing it opens the button's edit window instead. */
function onPress(item: QuickMessage): void {
  if (props.editing) openDialog(item)
  else emit('toggle', item.id)
}

/** A right click opens the edit window (with edit permission only; otherwise the browser's own menu stays). */
function onContext(event: MouseEvent, item: QuickMessage): void {
  if (!props.canEdit) return
  event.preventDefault()
  openDialog(item)
}

// ── a short notice in the row (delete / reorder failures) ──
const notice = ref('')
let noticeTimer: ReturnType<typeof setTimeout> | null = null

function showNotice(text: string): void {
  notice.value = text
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = '' }, 4000)
}

onBeforeUnmount(() => { if (noticeTimer) clearTimeout(noticeTimer) })

// ── delete (asks once) ──
const confirmItem = ref<QuickMessage | null>(null)
const deleting = ref(false)

async function doDelete(): Promise<void> {
  const item = confirmItem.value
  if (!item || deleting.value) return
  deleting.value = true
  try {
    const ok = props.remove ? await props.remove(item.id) : false
    if (!ok) showNotice(t('gui.quickMessages.delete_failed'))
  } finally {
    deleting.value = false
    confirmItem.value = null
  }
}

// ── order: drag a button onto another one, or Alt+← / Alt+→ on a focused button ──
const dragId = ref<number | null>(null)
const dropIndex = ref<number | null>(null)

function onDragStart(event: DragEvent, item: QuickMessage): void {
  if (!props.editing) return
  dragId.value = item.id
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(item.id))
  }
}

function onDragOver(event: DragEvent, index: number): void {
  if (dragId.value === null) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  dropIndex.value = index
}

function endDrag(): void {
  dragId.value = null
  dropIndex.value = null
}

async function onDrop(event: DragEvent, index: number): Promise<void> {
  const id = dragId.value
  if (id === null) return
  event.preventDefault()
  endDrag()
  await reorder(id, index)
}

async function reorder(id: number, toIndex: number): Promise<void> {
  if (!props.move) return
  const result = await props.move(id, toIndex)
  if (result === 'restored') showNotice(t('gui.quickMessages.reorder_failed'))
  else if (result === 'unrestored') showNotice(t('gui.quickMessages.reorder_unrestored'))
}

function onItemKey(event: KeyboardEvent, item: QuickMessage, index: number): void {
  if (!props.editing || !event.altKey || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return
  event.preventDefault()
  void reorder(item.id, index + (event.key === 'ArrowLeft' ? -1 : 1))
}
</script>

<style scoped>
.conv-quick {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-bottom: 5px;
}

.conv-quick-scroll {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  overflow-x: auto;
  scrollbar-width: thin;
}

.conv-quick-btn {
  flex: 0 0 auto;
  height: 22px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  font: 11px/20px var(--fg2-font);
  white-space: nowrap;
  cursor: pointer;
}

.conv-quick-btn.is-on-sticky {
  border-color: var(--fg2-status-danger-fg);
  color: var(--fg2-status-danger-fg);
}

.conv-quick-btn.is-on-once {
  border-color: var(--fg2-status-info-fg);
  color: var(--fg2-status-info-fg);
}

.conv-quick-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.conv-quick-btn:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.conv-quick-empty {
  font-size: 11px;
  color: var(--fg2-muted-2);
  white-space: nowrap;
}

.conv-quick-item {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border-radius: var(--fg2-control-radius-sm);
}

.conv-quick-item.is-dragging {
  opacity: 0.4;
}

/* Where a dragged button would land: a bar before the button under the pointer. */
.conv-quick-item.is-drop-target {
  box-shadow: -3px 0 0 0 var(--fg2-focus);
}

/* ✎ 편집: every button gets a dotted border (the colours of an on button stay). */
.conv-quick-btn.is-editing {
  border-style: dashed;
  cursor: grab;
}

.conv-quick-del {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--fg2-muted);
  font: 10px/16px var(--fg2-font);
  cursor: pointer;
}

.conv-quick-del:hover {
  color: var(--fg2-status-danger-fg);
}

.conv-quick-del:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.conv-quick-notice {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--fg2-status-danger-fg);
}

.conv-quick-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 4px;
}

.conv-quick-act {
  height: 22px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-text-2);
  font: 11px/20px var(--fg2-font);
  white-space: nowrap;
  cursor: pointer;
}

.conv-quick-act.is-active {
  border-color: var(--fg2-focus);
  color: var(--fg2-focus);
}

.conv-quick-act:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
