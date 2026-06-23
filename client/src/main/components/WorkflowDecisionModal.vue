<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box wdm-box">

        <!-- ── Header ── -->
        <div class="modal-hd">
          <span class="modal-title">
            <i class="fa-solid fa-diagram-next" style="color:#7c3aed; margin-right:6px;"></i>
            {{ mode === 'edit' ? t('main.workflow_edit_modal.title') : t('main.workflow_decision_modal.title') }}
            <span v-if="mode !== 'edit'" class="wdm-doc-class-badge">{{ docClass }}</span>
          </span>
          <button class="modal-close" type="button" @click="close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- ── Body ── -->
        <div class="modal-bd wdm-body">

          <!-- Loading (edit mode) -->
          <div v-if="mode === 'edit' && loading" class="wem-loading">
            <i class="fa-solid fa-spinner fa-spin"></i>
            {{ t('main.workflow_edit_modal.loading') }}
          </div>

          <!-- Error (edit mode) -->
          <div v-else-if="mode === 'edit' && loadError" class="wem-error">
            <i class="fa-solid fa-circle-exclamation"></i>
            {{ t('main.workflow_edit_modal.error_load') }}
          </div>

          <template v-else>

            <!-- Locked section (edit mode only) -->
            <div v-if="mode === 'edit'" class="wem-locked-section">
              <div class="wem-section-title wem-locked-title">
                <i class="fa-solid fa-lock"></i>
                {{ t('main.workflow_edit_modal.locked_section_title') }}
              </div>
              <div v-if="lockedItems.length === 0" class="wem-locked-empty">
                <i class="fa-regular fa-circle-dot"></i>
                {{ t('main.workflow_edit_modal.locked_empty') }}
              </div>
              <div v-else class="wem-locked-list">
                <div
                  v-for="(item, idx) in lockedItems"
                  :key="item.id"
                  class="wem-locked-item"
                >
                  <span class="wem-seq-num">{{ idx + 1 }}</span>
                  <span class="doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
                  <span class="wem-locked-label">{{ docTypeStore.getLabel(item.type) }}</span>
                  <span class="wem-status-badge" :class="`status-${item.status}`">
                    <i :class="item.status === 'done' ? 'fa-solid fa-circle-check' : 'fa-regular fa-circle-dot'"></i>
                    {{ item.status === 'done'
                      ? t('main.workflow_edit_modal.locked_badge_done')
                      : t('main.workflow_edit_modal.locked_badge_in_progress') }}
                  </span>
                </div>
              </div>
            </div>

            <!-- All-locked notice (edit mode): prior steps done — new steps can still be appended -->
            <div v-if="mode === 'edit' && allDone" class="wem-all-done">
              <i class="fa-solid fa-circle-check"></i>
              {{ t('main.workflow_edit_modal.all_done') }}
            </div>

            <!-- Preset + layout: always available so steps can be appended after locked items -->

          <!-- Preset section -->
          <div class="wdm-preset-section">
            <div class="wdm-preset-title">
              <i class="fa-solid fa-wand-magic-sparkles"></i>
              {{ t('main.workflow_decision_modal.preset_label') }}
            </div>
            <div class="wdm-preset-btns">
              <button
                v-for="p in PRESETS"
                :key="p.key"
                type="button"
                class="wdm-preset-btn"
                @click="applyPreset(p.key)"
              >{{ t(`main.workflow_decision_modal.${p.key}`) }}</button>
            </div>
          </div>

          <!-- Two-column layout -->
          <div class="wdm-layout">

            <!-- LEFT: doc type picker -->
            <div class="wdm-left">
              <div class="wdm-panel-title">
                <i class="fa-solid fa-list-check"></i>
                {{ t('main.workflow_decision_modal.type_picker_title') }}
              </div>

              <!-- Selectable categories -->
              <div v-for="cat in CATEGORIES" :key="cat.key" class="wdm-category">
                <div class="wdm-cat-label" :class="`cat-${cat.key}`">
                  {{ t(`main.workflow_decision_modal.cat_${cat.key}`) }}
                </div>
                <div class="wdm-items">
                  <button
                    v-for="item in cat.items"
                    :key="item.type"
                    type="button"
                    class="wdm-type-btn"
                    :class="{ 'in-seq': typeSeqCounts[item.type] > 0, 'is-dragging': draggedType === item.type }"
                    draggable="true"
                    @click="addToSeq(item.type)"
                    @dragstart="handleTypeDragStart($event, item.type)"
                    @dragend="handleTypeDragEnd"
                  >
                    <span class="doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
                    <span class="wdm-type-name">{{ docTypeStore.getLabel(item.type) }}</span>
                    <span v-if="item.autoHintKey" class="wdm-auto-hint">{{ t(`main.workflow_decision_modal.${item.autoHintKey}`) }}</span>
                    <span class="wdm-seq-badge" :style="typeSeqCounts[item.type] > 0 ? '' : 'display:none'">×{{ typeSeqCounts[item.type] }}</span>
                    <i class="fa-solid fa-plus wdm-add-ico"></i>
                  </button>
                </div>
              </div>

              <!-- Auto-only (display only) -->
              <div class="wdm-category">
                <div class="wdm-cat-label cat-auto">{{ t('main.workflow_decision_modal.cat_auto') }}</div>
                <div class="wdm-items">
                  <div v-for="a in AUTO_ONLY" :key="a.type" class="wdm-auto-item-btn">
                    <span class="doc-tag" :class="`c-${a.type}`">{{ a.type }}</span>
                    <span class="wdm-type-name">{{ t(`main.workflow_decision_modal.type_${a.type}`) }}</span>
                    <span class="wdm-auto-hint">{{ t(`main.workflow_decision_modal.${a.hintKey}`) }}</span>
                  </div>
                </div>
              </div>

              <!-- Auto mapping note -->
              <div class="wdm-note">
                <i class="fa-solid fa-bolt"></i>
                <div>
                  <strong>{{ t('main.workflow_decision_modal.auto_map_title') }}</strong><br>
                  {{ t('main.workflow_decision_modal.auto_map_desc') }}
                </div>
              </div>
            </div><!-- /wdm-left -->

            <!-- RIGHT: sequence editor -->
            <div class="wdm-right">
              <div class="wdm-panel-title">
                <i class="fa-solid fa-sort"></i>
                {{ t('main.workflow_decision_modal.seq_editor_title') }}
                <span class="wdm-seq-count">{{ sequence.length }}{{ t('main.workflow_decision_modal.count_suffix') }}</span>
                <button
                  type="button"
                  class="wdm-clear-btn"
                  :disabled="sequence.length === 0"
                  @click="clearSeq"
                >
                  <i class="fa-solid fa-trash-can"></i>
                  {{ t('main.workflow_decision_modal.clear_all') }}
                </button>
              </div>

              <!-- Sequence list -->
              <div
                class="wdm-seq-editor"
                :class="{ 'is-type-dragging': draggedType !== null }"
                @dragover.prevent="handleEditorDragOver"
                @drop.prevent="handleEditorDrop"
              >
                <div v-if="sequence.length === 0" class="wdm-empty-state">
                  <i class="fa-solid fa-arrow-left"></i>
                  <span>{{ t('main.workflow_decision_modal.seq_empty') }}</span>
                </div>
                <div
                  v-for="(item, idx) in sequence"
                  :key="item.id"
                  class="wdm-seq-item"
                  :class="{
                    'is-auto': item.isAuto,
                    'is-dragging': draggedId === item.id,
                    'drag-over': !item.isAuto && item.id === dragOverId && draggedId !== item.id,
                    'type-drag-over': !item.isAuto && item.id === typeDragOverId,
                  }"
                  :draggable="!item.isAuto"
                  @dragstart="!item.isAuto && handleDragStart($event, item.id)"
                  @dragover.prevent="handleDragOver($event, item.id)"
                  @drop.prevent="handleDrop(item.id)"
                  @dragend="handleDragEnd"
                >
                  <i v-if="!item.isAuto" class="fa-solid fa-grip-vertical wdm-drag-handle"></i>
                  <span v-else class="wdm-drag-spacer"></span>
                  <span class="wdm-seq-num">{{ (mode === 'edit' ? lockedItems.length : 0) + idx + 1 }}</span>
                  <span class="doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
                  <span class="wdm-seq-label">{{ docTypeStore.getLabel(item.type) }}</span>
                  <span v-if="item.isAuto" class="wdm-auto-badge">
                    <i class="fa-solid fa-bolt" style="font-size:.58rem;"></i>
                    {{ t('main.workflow_decision_modal.auto_badge', { parent: parentTypeOf(item) }) }}
                  </span>
                  <div class="wdm-seq-btns">
                    <template v-if="!item.isAuto">
                      <button
                        type="button"
                        class="wdm-seq-btn"
                        :title="t('main.workflow_decision_modal.move_up')"
                        :disabled="manualIndexOf(item) === 0"
                        @click="moveUp(item.id)"
                      ><i class="fa-solid fa-chevron-up"></i></button>
                      <button
                        type="button"
                        class="wdm-seq-btn"
                        :title="t('main.workflow_decision_modal.move_down')"
                        :disabled="manualIndexOf(item) === manualItems.length - 1"
                        @click="moveDown(item.id)"
                      ><i class="fa-solid fa-chevron-down"></i></button>
                      <button
                        type="button"
                        class="wdm-seq-btn del"
                        :title="t('main.workflow_decision_modal.remove')"
                        @click="removeFromSeq(item.id)"
                      ><i class="fa-solid fa-trash-can"></i></button>
                    </template>
                    <template v-else>
                      <span style="width:82px; display:inline-block;"></span>
                    </template>
                  </div>
                </div>
                <!-- Drop zone: append to end -->
                <div
                  v-if="draggedId !== null || draggedType !== null"
                  class="wdm-drop-end"
                  :class="{ 'drag-over': dragOverId === -1 || typeDragOverId === -1 }"
                  @dragover.prevent="handleDropEndDragOver"
                  @drop.prevent="handleDrop(-1)"
                ></div>
              </div>

              <!-- Preview bar -->
              <div class="wdm-preview-wrap">
                <div class="wdm-preview-label">
                  <i class="fa-solid fa-eye"></i>
                  {{ t('main.workflow_decision_modal.preview_label') }}
                </div>
                <div class="wdm-preview">
                  <span v-if="(mode !== 'edit' && sequence.length === 0) || (mode === 'edit' && lockedItems.length === 0 && sequence.length === 0)" class="wdm-preview-empty">
                    {{ mode === 'edit' ? t('main.workflow_edit_modal.preview_empty') : t('main.workflow_decision_modal.preview_empty') }}
                  </span>
                  <!-- locked items (edit mode) -->
                  <template v-if="mode === 'edit'">
                    <template v-for="(item, idx) in lockedItems" :key="`l-${item.id}`">
                      <span class="wdm-prev-step wem-prev-locked">
                        <span class="doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
                      </span>
                      <span v-if="idx < lockedItems.length - 1 || sequence.length > 0" class="wdm-prev-arrow">
                        <i class="fa-solid fa-chevron-right"></i>
                      </span>
                    </template>
                  </template>
                  <!-- pending items -->
                  <template v-for="(item, idx) in sequence" :key="item.id">
                    <span class="wdm-prev-step" :class="{ 'is-auto': item.isAuto }">
                      <span class="doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
                    </span>
                    <span v-if="idx < sequence.length - 1" class="wdm-prev-arrow">
                      <i class="fa-solid fa-chevron-right"></i>
                    </span>
                  </template>
                </div>
              </div>
            </div><!-- /wdm-right -->

          </div><!-- /wdm-layout -->

          </template>
        </div><!-- /modal-bd -->

        <!-- ── Footer ── -->
        <div class="modal-ft">
          <span class="wdm-footer-note">
            <i class="fa-solid fa-circle-info"></i>
            {{ mode === 'edit' ? t('main.workflow_edit_modal.footer_note') : t('main.workflow_decision_modal.footer_note') }}
          </span>
          <button type="button" class="btn btn-secondary" @click="close">{{ t('common.cancel') }}</button>
          <button
            v-if="mode !== 'edit'"
            type="button"
            class="btn btn-primary"
            :disabled="sequence.length === 0 || submitting"
            @click="confirm"
          >
            <i class="fa-solid fa-check"></i>
            {{ t('main.workflow_decision_modal.confirm') }}
          </button>
          <button
            v-if="mode === 'edit' && !loading && !loadError"
            type="button"
            class="btn btn-primary"
            :disabled="saving || wouldEmptyDecided"
            :title="wouldEmptyDecided ? t('main.workflow_edit_modal.cannot_empty') : ''"
            @click="save"
          >
            <i class="fa-solid fa-floppy-disk"></i>
            {{ t('main.workflow_edit_modal.save') }}
          </button>
        </div>

      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, patchRequest } from '@shared/api'
import { useToast } from './common/useToast'
import { useDocTypeStore } from '../stores/docTypeStore'

export interface SequenceItem {
  id: number
  type: string
  label: string
  isAuto: boolean
  autoOfId: number | null
}

export interface WfdConfirmPayload {
  sequence: SequenceItem[]
  docClass: string
}

const props = defineProps<{
  visible: boolean
  docClass?: string
  mode?: 'create' | 'edit'
  docId?: string
  /** True while the parent's decision POST is in flight — disables confirm to block
   *  the repeated-click 409 burst (R0001 / NR0003 item 2). */
  submitting?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'confirmed': [payload: WfdConfirmPayload]
  'saved': []
}>()

const { t } = useI18n()
const { showToast } = useToast()
const docTypeStore = useDocTypeStore()

// ── Static config ──────────────────────────────────────────────────────────────

const AUTO_MAP: Record<string, string[]> = {
  N:  ['NR'],
  T:  ['TR'],
  TS: ['TSR'],
}

const AUTO_TYPES = new Set(['NR', 'TR', 'TSR'])

const CATEGORIES = [
  {
    key: 'req',
    items: [{ type: 'M', autoHintKey: '' }],
  },
  {
    // CH (Conversation): general-series auto-complete type, sibling of M
    // (migration 047, AUTO_COMPLETE_TYPES). Surfaced here so the type is visible
    // and selectable in the workflow-decision / sequence-edit dialogs (TR0010 rev1).
    key: 'conversation',
    items: [{ type: 'CH', autoHintKey: '' }],
  },
  {
    key: 'instruction',
    items: [
      { type: 'DS', autoHintKey: '' },
      { type: 'N',  autoHintKey: 'auto_hint_NR' },
      { type: 'T',  autoHintKey: 'auto_hint_TR' },
      { type: 'TS', autoHintKey: 'auto_hint_TSR' },
    ],
  },
  {
    key: 'design',
    items: [
      { type: 'D',  autoHintKey: '' },
      { type: 'P',  autoHintKey: '' },
      { type: 'L',  autoHintKey: '' },
      { type: 'DB', autoHintKey: '' },
    ],
  },
  {
    key: 'action',
    items: [
      { type: 'C', autoHintKey: '' },
    ],
  },
]

const AUTO_ONLY = [
  { type: 'NR',  hintKey: 'auto_only_hint_NR' },
  { type: 'TR',  hintKey: 'auto_only_hint_TR' },
  { type: 'TSR', hintKey: 'auto_only_hint_TSR' },
]

const PRESETS: Array<{ key: string; types: string[] }> = [
  { key: 'preset_standard', types: ['DS', 'D', 'P', 'L', 'DB', 'T', 'TS'] },
  { key: 'preset_bugfix',   types: ['N', 'T', 'TS'] },
  { key: 'preset_simple',   types: ['N', 'T'] },
  { key: 'preset_design',   types: ['DS', 'D', 'P', 'L', 'DB'] },
]

// ── Reactive state ─────────────────────────────────────────────────────────────

const sequence = ref<SequenceItem[]>([])
const idCounter = ref(0)

// ── Edit-mode state ────────────────────────────────────────────────────────────

interface ServerItem {
  id: number
  type: string
  label: string
  status: 'pending' | 'in_progress' | 'done'
  sort_order: number
}

const loading = ref(false)
const loadError = ref(false)
const saving = ref(false)
const lockedItems = ref<ServerItem[]>([])

// ── Computed ───────────────────────────────────────────────────────────────────

const manualItems = computed(() => sequence.value.filter(s => !s.isAuto))

const allDone = computed(() =>
  props.mode === 'edit' && lockedItems.value.length > 0 && sequence.value.length === 0 && !loading.value && !loadError.value
)

// 0119 B0001 (NR0003 §6-A): block a Save that would leave a decided workflow with ZERO
// items (no locked step + no pending step). That produces the unrecoverable zombie
// sequence the bug describes. Mirrors the server guard (invalid_sequence_empty) and the
// create-mode [Confirm] disable (sequence.length === 0). A shrink that keeps ≥1 locked
// step is still allowed (locked + AC remain).
const wouldEmptyDecided = computed(() =>
  props.mode === 'edit' && lockedItems.value.length === 0 && sequence.value.length === 0 && !loading.value && !loadError.value
)

const typeSeqCounts = computed((): Record<string, number> => {
  const m: Record<string, number> = {}
  for (const s of sequence.value) {
    if (!s.isAuto) m[s.type] = (m[s.type] || 0) + 1
  }
  return m
})

// ── Helpers ────────────────────────────────────────────────────────────────────

function manualIndexOf(item: SequenceItem): number {
  return manualItems.value.findIndex(s => s.id === item.id)
}

function parentTypeOf(item: SequenceItem): string {
  if (!item.autoOfId) return ''
  return sequence.value.find(s => s.id === item.autoOfId)?.type ?? ''
}

function findBlockEnd(startIdx: number): number {
  const seq = sequence.value
  const manualId = seq[startIdx].id
  let end = startIdx
  while (end + 1 < seq.length && seq[end + 1].isAuto && seq[end + 1].autoOfId === manualId) {
    end++
  }
  return end
}

function buildEntries(type: string): SequenceItem[] {
  const manualId = ++idCounter.value
  const entries: SequenceItem[] = [
    { id: manualId, type, label: docTypeStore.getLabel(type), isAuto: false, autoOfId: null },
  ]
  const autos = AUTO_MAP[type]
  if (autos) {
    for (const autoType of autos) {
      entries.push({ id: ++idCounter.value, type: autoType, label: docTypeStore.getLabel(autoType), isAuto: true, autoOfId: manualId })
    }
  }
  return entries
}

// ── Sequence operations ────────────────────────────────────────────────────────

function applyPreset(key: string) {
  const preset = PRESETS.find(p => p.key === key)
  if (!preset) return
  idCounter.value = 0
  const newSeq: SequenceItem[] = []
  for (const type of preset.types) {
    newSeq.push(...buildEntries(type))
  }
  sequence.value = newSeq
}

function addToSeq(type: string) {
  sequence.value = [...sequence.value, ...buildEntries(type)]
}

function removeFromSeq(id: number) {
  sequence.value = sequence.value.filter(s => s.id !== id && s.autoOfId !== id)
}

function moveUp(id: number) {
  const seq = sequence.value
  const idx = seq.findIndex(s => s.id === id)
  if (idx <= 0) return
  const blockEnd = findBlockEnd(idx)
  const block = seq.splice(idx, blockEnd - idx + 1)
  let prevStart = idx - 1
  while (prevStart > 0 && seq[prevStart].isAuto) prevStart--
  seq.splice(prevStart, 0, ...block)
}

function moveDown(id: number) {
  const seq = sequence.value
  const idx = seq.findIndex(s => s.id === id)
  if (idx < 0) return
  const blockEnd = findBlockEnd(idx)
  if (blockEnd >= seq.length - 1) return
  const block = seq.splice(idx, blockEnd - idx + 1)
  const nextBlockEnd = findBlockEnd(idx)
  seq.splice(nextBlockEnd + 1, 0, ...block)
}

function clearSeq() {
  sequence.value = []
}

// ── DnD ───────────────────────────────────────────────────────────────────────

const draggedId = ref<number | null>(null)
const dragOverId = ref<number | null>(null)
const draggedType = ref<string | null>(null)
const typeDragOverId = ref<number | null>(null)

function handleTypeDragStart(event: DragEvent, type: string) {
  draggedType.value = type
  draggedId.value = null
  dragOverId.value = null
  typeDragOverId.value = -1
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'copy'
    event.dataTransfer.dropEffect = 'copy'
    event.dataTransfer.setData('text/plain', type)
  }
}

function handleTypeDragEnd() {
  draggedType.value = null
  typeDragOverId.value = null
}

function handleDragStart(event: DragEvent, id: number) {
  draggedId.value = id
  draggedType.value = null
  typeDragOverId.value = null
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
  }
}

function handleDragOver(_event: DragEvent, id: number) {
  const item = sequence.value.find(s => s.id === id)
  if (!item) return
  const targetId = item.isAuto ? (item.autoOfId ?? id) : id
  if (draggedType.value !== null) {
    typeDragOverId.value = targetId
    return
  }
  if (draggedId.value === null) return
  // Remap auto items to their parent so the indicator always appears on a manual item
  dragOverId.value = targetId
}

function handleDrop(id: number) {
  if (draggedType.value !== null) {
    insertTypeAt(draggedType.value, normalizeDropTargetId(id))
    draggedType.value = null
    typeDragOverId.value = null
    return
  }
  const fromId = draggedId.value
  draggedId.value = null
  dragOverId.value = null
  if (fromId === null) return
  if (id === -1) {
    moveBlockToEnd(fromId)
  } else {
    const item = sequence.value.find(s => s.id === id)
    const toId = item?.isAuto ? (item.autoOfId ?? id) : id
    if (fromId !== toId) moveBlock(fromId, toId)
  }
}

function handleDragEnd() {
  draggedId.value = null
  dragOverId.value = null
}

function handleEditorDragOver(event: DragEvent) {
  if (draggedType.value === null) return
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'copy'
  }
  if (sequence.value.length === 0) typeDragOverId.value = -1
}

function handleEditorDrop() {
  if (draggedType.value === null) return
  insertTypeAt(draggedType.value, typeDragOverId.value ?? -1)
  draggedType.value = null
  typeDragOverId.value = null
}

function handleDropEndDragOver() {
  if (draggedType.value !== null) {
    typeDragOverId.value = -1
    return
  }
  dragOverId.value = -1
}

function normalizeDropTargetId(id: number): number {
  if (id === -1) return -1
  const item = sequence.value.find(s => s.id === id)
  return item?.isAuto ? (item.autoOfId ?? id) : id
}

function findBlockEndInArr(arr: SequenceItem[], startIdx: number): number {
  const manualId = arr[startIdx].id
  let end = startIdx
  while (end + 1 < arr.length && arr[end + 1].isAuto && arr[end + 1].autoOfId === manualId) {
    end++
  }
  return end
}

function moveBlock(fromId: number, toId: number) {
  const seq = [...sequence.value]
  const fromIdx = seq.findIndex(s => s.id === fromId)
  if (fromIdx < 0) return
  const fromEnd = findBlockEndInArr(seq, fromIdx)
  const block = seq.splice(fromIdx, fromEnd - fromIdx + 1)
  const toIdx = seq.findIndex(s => s.id === toId)
  seq.splice(toIdx >= 0 ? toIdx : seq.length, 0, ...block)
  sequence.value = seq
}

function moveBlockToEnd(fromId: number) {
  const seq = [...sequence.value]
  const fromIdx = seq.findIndex(s => s.id === fromId)
  if (fromIdx < 0) return
  const fromEnd = findBlockEndInArr(seq, fromIdx)
  const block = seq.splice(fromIdx, fromEnd - fromIdx + 1)
  seq.push(...block)
  sequence.value = seq
}

function insertTypeAt(type: string, targetId: number) {
  const entries = buildEntries(type)
  if (targetId === -1) {
    sequence.value = [...sequence.value, ...entries]
    return
  }
  const seq = [...sequence.value]
  const targetIdx = seq.findIndex(s => s.id === targetId)
  if (targetIdx < 0) {
    sequence.value = [...seq, ...entries]
    return
  }
  seq.splice(targetIdx, 0, ...entries)
  sequence.value = seq
}

// ── Dialog actions ─────────────────────────────────────────────────────────────

function close() {
  emit('update:visible', false)
}

function confirm() {
  if (sequence.value.length === 0 || props.submitting) return
  emit('confirmed', { sequence: [...sequence.value], docClass: props.docClass ?? 'R' })
  emit('update:visible', false)
}

// ── Edit-mode: DB load & save ──────────────────────────────────────────────────

function dbItemsToSequence(items: ServerItem[]): SequenceItem[] {
  const result: SequenceItem[] = []
  let lastManualId: number | null = null
  for (const it of items) {
    const id = ++idCounter.value
    if (AUTO_TYPES.has(it.type)) {
      result.push({ id, type: it.type, label: it.label || docTypeStore.getLabel(it.type), isAuto: true, autoOfId: lastManualId })
    } else {
      lastManualId = id
      result.push({ id, type: it.type, label: it.label || docTypeStore.getLabel(it.type), isAuto: false, autoOfId: null })
    }
  }
  return result
}

async function loadSequence() {
  if (!props.docId) return
  loading.value = true
  loadError.value = false
  lockedItems.value = []
  sequence.value = []
  idCounter.value = 0
  try {
    const res = await getRequest<any>('/api/v1/workflow/sequence', { doc_id: props.docId })
    const data = (res.data as any)
    const items: ServerItem[] = data?.items ?? data?.sequence ?? []
    lockedItems.value = items.filter(it => it.status !== 'pending')
    const pendingItems = items.filter(it => it.status === 'pending')
    sequence.value = dbItemsToSequence(pendingItems)
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function save() {
  if (saving.value || wouldEmptyDecided.value) return
  saving.value = true
  try {
    await patchRequest('/api/v1/workflow/sequence', {
      doc_id: props.docId,
      items: sequence.value.map(it => ({ type: it.type, label: it.label })),
    })
    showToast(t('main.workflow_edit_modal.toast_saved'), 'success')
    emit('saved')
    emit('update:visible', false)
  } catch {
    showToast(t('main.workflow_edit_modal.error_save'), 'error')
  } finally {
    saving.value = false
  }
}

// Reset / load state when dialog opens
watch(
  () => props.visible,
  (v) => {
    if (v) {
      if (props.mode === 'edit') {
        loadSequence()
      } else {
        sequence.value = []
        idCounter.value = 0
      }
    }
  },
)
</script>

<style scoped>
/* ── Viewport-constrained modal (small screen scroll) ── */
.wdm-box {
  width: 880px;
  max-width: 96vw;
  height: 85vh;
  max-height: 85vh;
}

.wdm-box .modal-hd,
.wdm-box .modal-ft {
  flex-shrink: 0;
}

.wdm-body {
  padding: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

/* ── Preset section ── */
.wdm-preset-section {
  background: #f5f3ff;
  border-top: 1px solid #ddd6fe;
  border-bottom: 1px solid #ddd6fe;
  padding: 12px 14px;
  flex-shrink: 0;
}

.wdm-preset-title {
  font-size: .67rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: #7c3aed;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.wdm-preset-btns {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px;
}

.wdm-preset-btn {
  padding: 6px 10px;
  border: 1px solid #ddd6fe;
  background: white;
  border-radius: var(--r-sm);
  text-align: left;
  cursor: pointer;
  transition: all var(--tr);
  font-size: .75rem;
  font-weight: 500;
  color: #7c3aed;
}

.wdm-preset-btn:hover { background: #f5f3ff; border-color: #c4b5fd; }

/* ── Two-column layout ── */
.wdm-layout {
  display: grid;
  grid-template-columns: 270px 1fr;
  min-height: 0;
  overflow: hidden;
  flex: 1;
}

/* ── Left panel ── */
.wdm-left {
  border-right: 1px solid var(--border);
  padding: 16px 14px;
  overflow-y: auto;
  background: var(--surface-h);
}

.wdm-left::-webkit-scrollbar { width: 4px; }
.wdm-left::-webkit-scrollbar-thumb { background: var(--border-d); border-radius: 2px; }

.wdm-panel-title {
  font-size: .67rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--text-m);
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 14px;
}

.wdm-category { margin-bottom: 14px; }

.wdm-cat-label {
  font-size: .63rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .1em;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 2px 8px;
  margin-bottom: 6px;
  display: inline-block;
}

.wdm-items { display: flex; flex-direction: column; gap: 3px; }

.wdm-type-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
  text-align: left;
  cursor: pointer;
  transition: all var(--tr);
  width: 100%;
}

.wdm-type-btn:hover { border-color: var(--primary); background: var(--primary-l); }
.wdm-type-btn:hover .wdm-add-ico { opacity: 1; }
.wdm-type-btn.in-seq { border-color: var(--primary); }
.wdm-type-btn.is-dragging {
  opacity: .55;
  border-color: var(--primary);
  background: var(--primary-l);
  cursor: grabbing;
}

.wdm-type-name { flex: 1; font-size: .8rem; font-weight: 500; color: var(--text); }

.wdm-seq-badge {
  font-size: .6rem;
  font-weight: 700;
  background: var(--primary);
  color: white;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  padding: 0 4px;
  text-align: center;
  line-height: 16px;
  flex-shrink: 0;
}

.wdm-add-ico { color: var(--primary); font-size: .72rem; opacity: .45; transition: opacity var(--tr); flex-shrink: 0; }

.wdm-auto-item-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border: 1px dashed var(--border);
  border-radius: var(--r);
  background: transparent;
  width: 100%;
}

.wdm-auto-item-btn .wdm-type-name { color: var(--text-m); font-style: italic; }

.wdm-note {
  background: var(--warning-l);
  border: 1px solid #fde68a;
  border-radius: var(--r);
  padding: 10px 12px;
  font-size: .75rem;
  color: #92400e;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 6px;
  line-height: 1.5;
}

.wdm-note i { margin-top: 1px; flex-shrink: 0; color: var(--warning); }

/* ── Right panel ── */
.wdm-right {
  display: flex;
  flex-direction: column;
  padding: 16px;
  overflow: hidden;
}

.wdm-seq-count {
  margin-left: auto;
  font-size: .72rem;
  font-weight: 700;
  background: var(--primary-l);
  color: var(--primary);
  padding: 1px 9px;
  border-radius: 10px;
}

.wdm-clear-btn {
  font-size: .63rem;
  font-weight: 600;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--text-m);
  cursor: pointer;
  transition: all var(--tr);
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: 6px;
}

.wdm-clear-btn:hover:not(:disabled) { background: var(--danger-l); color: var(--danger); border-color: #fca5a5; }
.wdm-clear-btn:disabled { opacity: .3; pointer-events: none; }

.wdm-seq-editor {
  flex: 1;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface-h);
  padding: 6px;
  margin-bottom: 12px;
  min-height: 180px;
}

.wdm-seq-editor.is-type-dragging {
  border-color: var(--primary);
}

.wdm-seq-editor::-webkit-scrollbar { width: 4px; }
.wdm-seq-editor::-webkit-scrollbar-thumb { background: var(--border-d); border-radius: 2px; }

.wdm-empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 20px;
  color: var(--text-m);
  font-size: .8rem;
  text-align: center;
  height: 100%;
}

.wdm-empty-state i { font-size: 1.5rem; opacity: .3; }

.wdm-seq-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
  margin-bottom: 4px;
  transition: box-shadow var(--tr);
}

.wdm-seq-item:last-child { margin-bottom: 0; }
.wdm-seq-item:hover { box-shadow: var(--sh-sm); }

.wdm-seq-item.is-auto {
  background: #fffbeb;
  border-color: #fde68a;
  border-left: 3px solid var(--warning);
  padding-left: 7px;
}

.wdm-seq-num {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--bg);
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: .68rem;
  font-weight: 700;
  color: var(--text-s);
  flex-shrink: 0;
}

.wdm-seq-item.is-auto .wdm-seq-num { background: #fef3c7; border-color: #fde68a; color: #92400e; font-style: italic; }

.wdm-seq-label { flex: 1; font-size: .8rem; font-weight: 500; color: var(--text); }
.wdm-seq-item.is-auto .wdm-seq-label { color: var(--text-s); font-style: italic; }

.wdm-auto-badge {
  font-size: .63rem;
  color: var(--warning);
  background: var(--warning-l);
  padding: 1px 7px;
  border-radius: 10px;
  font-weight: 700;
  white-space: nowrap;
}

.wdm-seq-btns { display: flex; gap: 2px; flex-shrink: 0; }

.wdm-seq-btn {
  width: 26px;
  height: 26px;
  border-radius: var(--r-sm);
  border: 1px solid var(--border);
  background: var(--surface);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: .68rem;
  color: var(--text-m);
  cursor: pointer;
  transition: all var(--tr);
}

.wdm-seq-btn:hover { background: var(--bg); color: var(--text); border-color: var(--border-d); }
.wdm-seq-btn.del:hover { background: var(--danger-l); color: var(--danger); border-color: #fca5a5; }
.wdm-seq-btn:disabled { opacity: .25; pointer-events: none; }

/* ── Preview bar ── */
.wdm-preview-wrap { flex-shrink: 0; }

.wdm-preview-label {
  font-size: .67rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-m);
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.wdm-preview {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 3px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 8px 12px;
  min-height: 38px;
}

.wdm-preview-empty { color: var(--text-m); font-size: .78rem; font-style: italic; }

.wdm-prev-step { display: inline-flex; align-items: center; gap: 3px; }
.wdm-prev-step.is-auto .doc-tag { opacity: .7; outline: 1px dashed rgba(217,119,6,.4); outline-offset: 1px; }

.wdm-prev-arrow { color: var(--border-d); font-size: .62rem; }

/* ── Footer ── */
/* ── DnD ── */
.wdm-drag-handle {
  color: var(--text-m);
  font-size: .72rem;
  opacity: .3;
  cursor: grab;
  flex-shrink: 0;
  transition: opacity var(--tr);
}

.wdm-seq-item:not(.is-auto):hover .wdm-drag-handle { opacity: .65; }

.wdm-drag-spacer {
  width: 10px;
  flex-shrink: 0;
  display: inline-block;
}

.wdm-seq-item.is-dragging {
  opacity: .35;
  box-shadow: none;
  background: rgba(37, 99, 235, .08);
  border-color: #2563eb;
}

.wdm-seq-item.drag-over {
  border-top: 2px solid var(--primary);
  margin-top: -1px;
}

.wdm-seq-item.type-drag-over {
  border-top: 2px solid var(--primary);
  margin-top: -1px;
  background: var(--primary-l);
}

.wdm-drop-end {
  height: 6px;
  border-radius: var(--r);
  transition: all var(--tr);
  margin-top: 2px;
}

.wdm-drop-end.drag-over {
  height: 18px;
  border: 2px dashed var(--primary);
  background: var(--primary-l);
  border-radius: var(--r);
}

/* ── Footer note ── */
.wdm-footer-note {
  font-size: .78rem;
  color: var(--text-m);
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
}

/* ── Edit mode: Loading / error ── */
.wem-loading,
.wem-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 24px 20px;
  font-size: .8125rem;
  color: var(--text-m);
}
.wem-error { color: #dc2626; }

/* ── Edit mode: Locked section ── */
.wem-locked-section {
  border-bottom: 1px solid var(--border-d, #e2e8f0);
  padding: 12px 14px;
  background: #f8fafc;
  flex-shrink: 0;
}

.wem-section-title {
  font-size: .72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .04em;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.wem-locked-title { color: #64748b; }

.wem-locked-empty {
  font-size: .78rem;
  color: var(--text-m);
  padding: 4px 2px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.wem-locked-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 124px;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  scrollbar-color: #94a3b8 #e2e8f0;
}

.wem-locked-list::-webkit-scrollbar {
  width: 8px;
}

.wem-locked-list::-webkit-scrollbar-track {
  background: #e2e8f0;
  border-radius: 4px;
}

.wem-locked-list::-webkit-scrollbar-thumb {
  background: #94a3b8;
  border-radius: 4px;
}

.wem-locked-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: .8rem;
  padding: 4px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  opacity: .85;
}

.wem-locked-label {
  flex: 1;
  color: var(--text-m);
  font-size: .78rem;
}

.wem-status-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: .72rem;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 99px;
}
.wem-status-badge.status-done {
  background: #dcfce7;
  color: #16a34a;
}
.wem-status-badge.status-in_progress {
  background: #dbeafe;
  color: #2563eb;
}

/* ── Edit mode: All done message ── */
.wem-all-done {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  font-size: .8125rem;
  color: #16a34a;
  font-weight: 500;
}

/* ── Edit mode: Sequence number in locked list ── */
.wem-seq-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  font-size: .68rem;
  font-weight: 700;
  color: var(--text-m);
  background: #f1f5f9;
  border-radius: 4px;
  margin-right: 2px;
}

/* ── Edit mode: Preview locked items ── */
.wem-prev-locked { opacity: .65; }
.wem-prev-locked .doc-tag { filter: grayscale(.3); }
</style>
