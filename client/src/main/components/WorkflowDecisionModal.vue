<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box wdm-box">

        <!-- ── Header ── -->
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="flow-arrow" style="color:#7c3aed; margin-right:6px;" />
            {{ mode === 'edit' ? t('main.workflow_edit_modal.title') : t('main.workflow_decision_modal.title') }}
            <span v-if="mode !== 'edit'" class="wdm-doc-class-badge">{{ docClass }}</span>
          </span>
          <button class="modal-close" type="button" @click="close">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- ── 0399 D0010 §3.3 / §6.2 — what was poured, and that it is not saved yet ──
             The whole point of this strip: pressing a mode in [Apply Work Plan] changed
             nothing. It says so, and it offers the one-step way back (L0011 §3.3). -->
        <div v-if="pourSession" class="wdm-banner">
          <AppIcon name="clipboard-text" />
          <span>
            {{ t(`main.work_plan_pour.banner_${pourSession.mode}`, {
              doc: pourSession.wpShortCode,
              n: pourSession.planStepCount,
              d: pourSession.rowCountChange.deleted,
            }) }}
            <!-- Mockup fgh29xnk v3 · screen 3: states, on the same line, how much a person edited after the pour. -->
            <template v-if="pourEditsText">
              {{ t('main.work_plan_pour.banner_edited', { edits: pourEditsText }) }}
            </template>
            {{ t('main.work_plan_pour.banner_unsaved') }}
          </span>
          <span class="wdm-banner-spacer"></span>
          <button type="button" class="wdm-undo" @click="revertPour">
            <AppIcon name="arrow-counter-clockwise" />
            {{ t('main.work_plan_pour.undo') }}
          </button>
        </div>
        <!-- Mockup screen 3's second strip: lists steps with no note not just by count but by
             name and reason. Counts from the current list rather than the count the server
             sent when it poured — because it's a number that must drop the moment a person
             fills the field. -->
        <div v-if="pourSession && missingNoteRows.length > 0" class="wdm-banner wdm-banner--warn">
          <AppIcon name="chat-slash" />
          <span>
            {{ t('main.work_plan_pour.notify_note_missing', {
              n: missingNoteRows.length, list: missingNoteText,
            }) }}
          </span>
        </div>
        <div
          v-for="note in pourNotifications"
          :key="note.code"
          class="wdm-banner wdm-banner--warn"
        >
          <AppIcon name="warning" />
          <span>{{ notificationText(note) }}</span>
        </div>

        <!-- ── Body ── -->
        <div class="modal-bd wdm-body">

          <!-- Loading (edit mode) -->
          <div v-if="mode === 'edit' && loading" class="wem-loading">
            <AppIcon name="spinner" spin />
            {{ t('main.workflow_edit_modal.loading') }}
          </div>

          <!-- Error (edit mode) -->
          <div v-else-if="mode === 'edit' && loadError" class="wem-error">
            <AppIcon name="warning-circle" />
            {{ t('main.workflow_edit_modal.error_load') }}
          </div>

          <template v-else>

            <div
              v-if="mode === 'edit' && metaContractMissing"
              class="wem-error wem-meta-contract-warning"
            >
              <AppIcon name="warning-circle" />
              <span>{{ t('main.workflow_edit_modal.meta_contract_missing') }}</span>
              <button type="button" class="btn btn-secondary wem-reload-btn" @click="loadSequence">
                {{ t('main.workflow_edit_modal.reload') }}
              </button>
            </div>

            <!-- Locked section (edit mode only) -->
            <div v-if="mode === 'edit'" class="wem-locked-section">
              <div class="wem-section-title wem-locked-title">
                <AppIcon name="lock" />
                {{ t('main.workflow_edit_modal.locked_section_title') }}
              </div>
              <div v-if="lockedItems.length === 0" class="wem-locked-empty">
                <AppIcon name="radio-button" />
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
                  <span
                    v-if="item.provider_id"
                    class="wdm-provider-chip"
                    :class="{ 'is-unavailable': item.provider_registered === false }"
                  >
                    <template v-if="item.provider_registered === false">⚠ {{ t('main.workflow_edit_modal.provider_unavailable', { name: item.provider_display_name || item.provider_id }) }}</template>
                    <template v-else>🤖 {{ item.provider_display_name || item.provider_id }}</template>
                  </span>
                  <span class="wem-status-badge" :class="`status-${item.status}`">
                    <AppIcon :name="item.status === 'done' ? 'check-circle' : 'radio-button'" />
                    {{ item.status === 'done'
                      ? t('main.workflow_edit_modal.locked_badge_done')
                      : t('main.workflow_edit_modal.locked_badge_in_progress') }}
                  </span>
                </div>
              </div>
            </div>

            <!-- All-locked notice (edit mode): prior steps done — new steps can still be appended -->
            <div v-if="mode === 'edit' && allDone" class="wem-all-done">
              <AppIcon name="check-circle" />
              {{ t('main.workflow_edit_modal.all_done') }}
            </div>

            <!-- Preset + layout: always available so steps can be appended after locked items -->

          <!-- Preset section -->
          <div class="wdm-preset-section">
            <div class="wdm-preset-title">
              <AppIcon name="magic-wand" />
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
                <AppIcon name="list-checks" />
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
                    <AppIcon name="plus" class="wdm-add-ico" />
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
                <AppIcon name="lightning" />
                <div>
                  <strong>{{ t('main.workflow_decision_modal.auto_map_title') }}</strong><br>
                  {{ t('main.workflow_decision_modal.auto_map_desc') }}
                </div>
              </div>

              <div v-if="mode === 'edit'" class="wdm-note wdm-provider-rule">
                <AppIcon name="info" />
                <div>{{ t('main.workflow_edit_modal.provider_readonly_rule') }}</div>
              </div>

              <!-- 0399 D0010 §6.2 — the rule for the notes, stated where the rows are made.
                   It is here and not in the banner because it answers a question the person
                   only has while editing: what happens to the note if I move this row. -->
              <div v-if="pourSession" class="wdm-note">
                <AppIcon name="chat-circle-dots" />
                <div>
                  <strong>{{ t('main.work_plan_pour.note_rule_title') }}</strong><br>
                  {{ t('main.work_plan_pour.note_rule_desc') }}
                </div>
              </div>
            </div><!-- /wdm-left -->

            <!-- RIGHT: sequence editor -->
            <div class="wdm-right">
              <div class="wdm-panel-title">
                <AppIcon name="arrows-down-up" />
                {{ t('main.workflow_decision_modal.seq_editor_title') }}
                <span class="wdm-seq-count">{{ sequence.length }}{{ t('main.workflow_decision_modal.count_suffix') }}</span>
                <button
                  type="button"
                  class="wdm-clear-btn"
                  :disabled="sequence.length === 0"
                  @click="clearSeq"
                >
                  <AppIcon name="trash" />
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
                  <AppIcon name="arrow-left" />
                  <span>{{ t('main.workflow_decision_modal.seq_empty') }}</span>
                </div>
                <div
                  v-for="(item, idx) in sequence"
                  :key="item.id"
                  class="wdm-seq-item"
                  :class="{
                    'is-auto': item.isAuto,
                    'from-plan': item.origin === 'plan' && !item.typeChanged,
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
                  <AppIcon name="dots-six-vertical" class="wdm-drag-handle" v-if="!item.isAuto" />
                  <span v-else class="wdm-drag-spacer"></span>
                  <span class="wdm-seq-num">{{ (mode === 'edit' ? lockedItems.length : 0) + idx + 1 }}</span>
                  <span class="doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
                  <!-- 0399 T0016 / D0010 §3.4 — the type can be swapped without deleting the
                       row. Only manual rows may change type; auto followers are derived. -->
                  <select
                    v-if="!item.isAuto && mode === 'edit'"
                    class="wdm-type-select"
                    :aria-label="t('main.work_plan_pour.change_type_label')"
                    :value="item.type"
                    @change="changeRowType(item, ($event.target as HTMLSelectElement).value)"
                  >
                    <option v-for="opt in SELECTABLE_TYPES" :key="opt" :value="opt">{{ opt }}</option>
                  </select>
              <!-- 0399 D0010 §3.4 — the note travels with the row. It is rendered ON the
                       row for exactly that reason: move the row and the note moves with it,
                       delete the row and the note goes too, with nothing to keep in sync. -->
                  <span class="wdm-seq-main">
                    <span class="wdm-seq-label">{{ docTypeStore.getLabel(item.type) }}</span>
                    <span
                      v-if="isNoteEditable(item) && mode === 'edit'"
                      class="wdm-seq-msg"
                      :class="{ 'wdm-seq-msg--empty': !item.note }"
                    >
                      <AppIcon :name="item.note ? 'chat-circle-dots' : 'warning-circle'" />
                      <input
                        type="text"
                        class="wdm-note-input"
                        :class="{ 'wdm-note-input--over': (item.note ?? '').length > noteMaxChars }"
                        v-model="item.note"
                        :placeholder="t('main.work_plan_pour.note_empty')"
                        @input="item.noteSource = null"
                      />
                      <small class="wdm-note-count" :class="{ 'wdm-note-count--over': (item.note ?? '').length > noteMaxChars }">
                        {{ (item.note ?? '').length > noteMaxChars
                          ? t('main.work_plan.note_char_over', { current: (item.note ?? '').length, max: noteMaxChars })
                          : t('main.work_plan.note_char_count', { current: (item.note ?? '').length, max: noteMaxChars }) }}
                      </small>
                    </span>
                  </span>
                  <span
                    v-if="item.origin === 'plan' && item.sourceDocId && !item.typeChanged"
                    class="wdm-plan-badge"
                    :title="item.noteSource === 'defaults' ? t('main.work_plan_pour.defaults_note_title') : undefined"
                  >
                    {{ t('main.work_plan_pour.from_plan', { doc: shortCodeOf(item.sourceDocId) }) }}
                  </span>
                  <span v-else-if="item.typeChanged" class="wdm-changed-badge">
                    {{ t('main.work_plan_pour.type_changed_badge') }}
                  </span>
                  <span
                    v-if="item.providerId"
                    class="wdm-provider-chip"
                    :class="{ 'is-unavailable': item.providerRegistered === false }"
                  >
                    <template v-if="item.providerRegistered === false">⚠ {{ t('main.workflow_edit_modal.provider_unavailable', { name: item.providerDisplayName || item.providerId }) }}</template>
                    <template v-else>🤖 {{ item.providerDisplayName || item.providerId }}</template>
                  </span>
                  <span v-if="item.isAuto" class="wdm-auto-badge">
                    <AppIcon name="lightning" style="font-size:.58rem;" />
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
                      ><AppIcon name="caret-up" /></button>
                      <button
                        type="button"
                        class="wdm-seq-btn"
                        :title="t('main.workflow_decision_modal.move_down')"
                        :disabled="manualIndexOf(item) === manualItems.length - 1"
                        @click="moveDown(item.id)"
                      ><AppIcon name="caret-down" /></button>
                      <button
                        type="button"
                        class="wdm-seq-btn del"
                        :title="t('main.workflow_decision_modal.remove')"
                        @click="removeFromSeq(item.id)"
                      ><AppIcon name="trash" /></button>
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
                  <AppIcon name="eye" />
                  {{ t('main.workflow_decision_modal.preview_label') }}
                  <!-- Mockup fgh29xnk v3 · screen 3 bottom caption. The line TR0015 §8 deferred. -->
                  <template v-if="pourDiffText">— {{ pourDiffText }}</template>
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
                        <AppIcon name="caret-right" />
                      </span>
                    </template>
                  </template>
                  <!-- pending items -->
                  <template v-for="(item, idx) in sequence" :key="item.id">
                    <span class="wdm-prev-step" :class="{ 'is-auto': item.isAuto }">
                      <span class="doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
                    </span>
                    <span v-if="idx < sequence.length - 1" class="wdm-prev-arrow">
                      <AppIcon name="caret-right" />
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
          <!-- 0268 TR0005 rev1: the provider picker sits at the far left of the footer, ahead of
               the note and every button, so the run target is read before any action is chosen. -->
          <!-- 0399 D0010 §3.7 / L0011 §4.4 — when this project has no usable provider the
               picker is NOT drawn greyed out, it is not drawn at all, and the save still
               works: moving a plan into the sequence never needed a provider. The one
               source of truth is the project's effective provider list (NR0008 §8) — never
               the plan body's candidate list, which is empty in plans whose providers are
               registered and working. -->
          <AiProviderSelect
            v-if="mode === 'edit' && !loading && !loadError && providerRowVisible"
            class="wdm-provider"
            :providers="providerStore.providers"
            :model-value="providerStore.selectedProviderId"
            :loading="providerStore.loading"
            :errored="!!providerStore.error"
            hide-label
            @update:model-value="(v: string) => providerStore.selectProvider(v)"
          />

          <button type="button" class="btn btn-secondary" @click="close">{{ t('common.cancel') }}</button>
          <button
            v-if="mode !== 'edit'"
            type="button"
            class="btn btn-primary"
            :disabled="sequence.length === 0 || submitting"
            @click="confirm"
          >
            <AppIcon name="check" />
            {{ t('main.workflow_decision_modal.confirm') }}
          </button>
          <!-- 0268 B0001: mention-copy and AI invoke are not either/or, they run in parallel.
               This button used to be labelled "AI에게 수정 요청" with a robot icon while only
               writing the clipboard,
               which is exactly what hid the missing invoke path — it is now named for what it
               does, and the real in-app call sits beside it. -->
          <button
            v-if="mode === 'edit' && !loading && !loadError"
            type="button"
            class="btn btn-secondary"
            :disabled="requestingAiEdit || invokingAi || issuing"
            @click="requestAiSequenceEdit"
          >
            <AppIcon name="copy" />
            {{ t('main.workflow_edit_modal.mention_copy') }}
          </button>
          <button
            v-if="mode === 'edit' && !loading && !loadError"
            type="button"
            class="btn btn-secondary"
            :disabled="requestingAiEdit || invokingAi || issuing"
            @click="invokeAiSequenceEdit"
          >
            <AppIcon :name="invokingAi ? 'spinner' : 'robot'" :spin="invokingAi" />
            {{ t('main.workflow_edit_modal.invoke_ai') }}
          </button>
          <button
            v-if="mode === 'edit' && !loading && !loadError"
            type="button"
            class="btn btn-primary"
            :disabled="saving || wouldEmptyDecided || metaContractMissing"
            :title="metaContractMissing
              ? t('main.workflow_edit_modal.cannot_save_missing_meta')
              : wouldEmptyDecided ? t('main.workflow_edit_modal.cannot_empty') : ''"
            @click="save"
          >
            <AppIcon name="floppy-disk" />
            {{ t('main.workflow_edit_modal.save') }}
          </button>
        </div>

      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { extractApiErrorMessage, getRequest, patchRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'
import { useDocTypeStore } from '../stores/docTypeStore'
import { useAiProviderStore } from '../stores/aiProvider'
import { useFlowGateToken, type IssuedToken } from '../composables/useFlowGateToken'
import { copyToClipboardDeferred, ClipboardAbort } from '../utils/clipboard'

export interface SequenceItem {
  id: number
  type: string
  label: string
  isAuto: boolean
  autoOfId: number | null
  // 0399 D0010 §3.4 / L0011 §2.1: the note and its origin live ON the row. The save
  // renumbers every pending row, so anything held beside the row — keyed on position or
  // on item_seq — would attach itself to the wrong step the first time somebody reorders.
  note: string
  noteSource: 'step' | 'pair' | 'defaults' | null
  origin: 'plan' | 'manual' | 'auto'
  planKey: string | null
  sourceDocId: string | null
  sourceRevisionNo: number | null
  providerId: string | null
  providerDisplayName: string | null
  providerRegistered: boolean | null
  // 0399 T0016 / D0010 §3.4: "줄의 문서 종류를 다른 것으로 바꾸면 그 멘트는 더 이상 그
  // 단계 이야기가 아니므로 비운다." — set the moment changeRowType() runs, never reset.
  typeChanged: boolean
}

/** One row of P0013 ①'s response. */
export interface PourRow {
  type: string
  label: string
  status: 'pending' | 'in_progress' | 'done'
  locked: boolean
  poured: boolean
  note: string
  note_source: 'step' | 'pair' | 'defaults' | null
  origin: 'plan' | 'manual' | 'auto'
  plan_key: string | null
  source_doc_id: string | null
  source_revision_no: number | null
  provider_id: string | null
  provider_display_name: string | null
  provider_registered: boolean | null
}

export interface PourNotification {
  code: string
  severity: string
  count: number
  types?: string[]
  row_indexes?: number[]
  items?: Array<Record<string, unknown>>
}

/** What [Apply Work Plan] hands this dialog: a starting state, not a saved change. */
export interface PourPayload {
  wpDocId: string
  // 0403 NR0004 F2 — the plan revision at the moment this dialog was opened. Sent back unchanged on save.
  wpRevisionNo: number
  wpShortCode: string
  // 0403 NR0004 F4 — the document that has (or will have) the sequence. In a group with no
  // workflow yet, the parent document the screen is holding can be empty, so the owner the
  // candidate response reported is used instead.
  workflowDocId: string | null
  mode: 'append' | 'replace_after'
  planStepCount: number
  rows: PourRow[]
  rowCountChange: { before: number; after: number; deleted: number; added: number }
  notifications: PourNotification[]
  workflowTag: string
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
  /** 0399 — set when the dialog was opened by [Apply Work Plan]. Absent on every other
   *  entrance, and absent means this behaves exactly as it did before. */
  poured?: PourPayload | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'confirmed': [payload: WfdConfirmPayload]
  'saved': []
}>()

const { t } = useI18n()
const { showToast } = useToast()
const docTypeStore = useDocTypeStore()

// R0001 group 0208: delegate the pending-sequence edit to an AI worker.
const { requestSequenceEdit, composeMention, issuing } = useFlowGateToken()
const requestingAiEdit = ref(false)

// 0268 B0001: the in-app half of the same delegation. Both entrances issue the identical
// workflow_sequence_edit token and mention server-side — only the delivery differs.
const providerStore = useAiProviderStore()
const invokingAi = ref(false)

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
    // 0395 T0021 / D0007 §7: the work plan (WP) is "요건정의 다음에 오는 일반 칸" — a step
    // that occupies a workflow slot (it is deliberately absent from
    // NON_SLOT_WORKFLOW_TYPES on the server), so it has to be placeable here. Without
    // this entry the only way to plan a group was a button that vanished half the
    // time (NR0020), which is what "어디서 설정하지" was asking about.
    key: 'plan',
    items: [{ type: 'WP', autoHintKey: '' }],
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
  // 0395 T0021: the 'action' category ([Commit] / C) was removed on instruction. C is
  // still a registered document type — this only stops it being placed as a workflow
  // step; sequences that already contain one keep rendering it.
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
  // 0399 L0011 §2.1: read for EVERY row, not just poured ones. The save replaces the whole
  // pending block, so a note this read skipped is a note the next save erases.
  note?: string
  source_doc_id?: string | null
  source_revision_no?: number | null
  provider_id?: string | null
  provider_display_name?: string | null
  provider_registered?: boolean | null
}

const loading = ref(false)
const loadError = ref(false)
// Canonical value comes from GET /workflow/sequence; fallback keeps old mocks readable.
const noteMaxChars = ref(1000)
const metaContractMissing = ref(false)
const saving = ref(false)
const lockedItems = ref<ServerItem[]>([])

// 0399 — the pour session. Present only between "a mode was chosen" and "saved / reverted /
// closed"; L0011 §3.3 fixes UNDO_DEPTH at 1, so there is one of these and never a stack.
const pourSession = ref<PourPayload | null>(null)
// note_missing is recomputed live below, so the server's snapshot of it would only ever
// disagree with what the rows on screen say once somebody starts typing.
const pourNotifications = computed(() =>
  (pourSession.value?.notifications ?? []).filter(n => n.code !== 'note_missing'),
)

// Mockup fgh29xnk v3 · screen 3 — to render "그 뒤 직접 2줄 지움 · 1줄 타입 바꿈 · 1줄 추가"
// we need to remember what things looked like right after the pour. The row's id is
// assigned by applyPour, so that's enough — a deleted row disappears, and an added row
// gets a new id absent from the list.
const pourBaselineIds = ref<Set<number>>(new Set())

const pourManualRows = computed(() => sequence.value.filter(s => !s.isAuto))
// 0408 M0019 re-rejection 2: every row carries its own mention except TSR, which the server
// assembles ("TSR 단계에는 공급자와 멘트를 적지 않습니다" — work_plan_service).
function isNoteEditable(item: SequenceItem): boolean {
  return item.type !== 'TSR'
}

const pourEditsText = computed(() => {
  if (!pourSession.value) return ''
  const now = new Set(pourManualRows.value.map(s => s.id))
  let deleted = 0
  for (const id of pourBaselineIds.value) if (!now.has(id)) deleted += 1
  const retyped = pourManualRows.value.filter(s => s.typeChanged).length
  const added = pourManualRows.value.filter(s => !pourBaselineIds.value.has(s.id)).length
  const parts: string[] = []
  if (deleted > 0) parts.push(t('main.work_plan_pour.edit_deleted', { n: deleted }))
  if (retyped > 0) parts.push(t('main.work_plan_pour.edit_type_changed', { n: retyped }))
  if (added > 0) parts.push(t('main.work_plan_pour.edit_added', { n: added }))
  return parts.join(' · ')
})

const pourDiffText = computed(() => {
  const session = pourSession.value
  if (!session) return ''
  const planned = session.planStepCount
  // A retyped row is still a row this plan put there, so it counts as applied and is
  // reported once more, separately, as retyped — exactly the way the mockup counts it.
  const applied = pourManualRows.value.filter(s => s.origin === 'plan').length
  const parts = [t('main.work_plan_pour.preview_diff', { planned, applied })]
  const removed = Math.max(planned - applied, 0)
  const retyped = pourManualRows.value.filter(s => s.typeChanged).length
  const added = pourManualRows.value.filter(s => !pourBaselineIds.value.has(s.id)).length
  if (removed > 0) parts.push(t('main.work_plan_pour.diff_removed', { n: removed }))
  if (retyped > 0) parts.push(t('main.work_plan_pour.diff_type_changed', { n: retyped }))
  if (added > 0) parts.push(t('main.work_plan_pour.diff_added', { n: added }))
  return parts.join(' · ')
})

const missingNoteRows = computed(() =>
  pourManualRows.value.filter(s => !(s.note ?? '').trim()),
)

const missingNoteText = computed(() =>
  missingNoteRows.value
    .map((row) => {
      const name = `${row.type} ${row.label || docTypeStore.getLabel(row.type)}`
      if (row.typeChanged) {
        return `${name}(${t('main.work_plan_pour.note_missing_reason_type_changed')})`
      }
      if (row.origin === 'manual') {
        return `${name}(${t('main.work_plan_pour.note_missing_reason_manual')})`
      }
      return name
    })
    .join(', '),
)

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

// 0399 L0011 §2.7: a row a person adds here starts with an empty note and no plan behind
// it. There is nowhere else it could come from — this row was not in any plan.
function blankRowFields(): Pick<SequenceItem, 'note' | 'noteSource' | 'origin' | 'planKey' | 'sourceDocId' | 'sourceRevisionNo' | 'providerId' | 'providerDisplayName' | 'providerRegistered' | 'typeChanged'> {
  return {
    note: '', noteSource: null, origin: 'manual', planKey: null,
    sourceDocId: null, sourceRevisionNo: null,
    providerId: null, providerDisplayName: null, providerRegistered: null,
    typeChanged: false,
  }
}

// 0408 M0019 re-rejection 2: a report row a person creates here starts with an empty note (there is
// no plan behind it yet) but it is a note-carrying row like any other — under [Auto Approval] this
// is the row an AI worker runs, so its mention is the one that gets delivered.
function buildAutoEntries(manualId: number, type: string, parent?: SequenceItem): SequenceItem[] {
  const autos = AUTO_MAP[type]
  if (!autos) return []
  return autos.map(autoType => ({
    id: ++idCounter.value, type: autoType, label: docTypeStore.getLabel(autoType),
    isAuto: true, autoOfId: manualId, ...blankRowFields(), origin: 'auto' as const,
    providerId: autoType === 'TSR' ? null : (parent?.providerId ?? null),
    providerDisplayName: autoType === 'TSR' ? null : (parent?.providerDisplayName ?? null),
    providerRegistered: autoType === 'TSR' ? null : (parent?.providerRegistered ?? null),
  }))
}

function buildEntries(type: string): SequenceItem[] {
  const manualId = ++idCounter.value
  const entries: SequenceItem[] = [
    { id: manualId, type, label: docTypeStore.getLabel(type), isAuto: false, autoOfId: null, ...blankRowFields() },
  ]
  entries.push(...buildAutoEntries(manualId, type))
  return entries
}

// The types a row can be switched to. Excludes AUTO_ONLY (NR/TR/TSR follow their parent
// automatically — a person never picks them directly, here or in the left panel).
const SELECTABLE_TYPES = CATEGORIES.flatMap(cat => cat.items.map(i => i.type))

// 0399 T0016 / D0010 §3.4: changing a row's type empties its note (it is no longer that
// step's message) and, if the row followed an instruction type, rebuilds its auto-linked
// followers (TR/TSR) for the new type — removing stale ones, adding missing ones.
function changeRowType(item: SequenceItem, newType: string) {
  if (newType === item.type || item.isAuto) return
  item.type = newType
  item.label = docTypeStore.getLabel(newType)
  item.note = ''
  item.noteSource = null
  item.typeChanged = true
  const seq = sequence.value.filter(s => !(s.isAuto && s.autoOfId === item.id))
  const idx = seq.findIndex(s => s.id === item.id)
  if (idx >= 0) seq.splice(idx + 1, 0, ...buildAutoEntries(item.id, newType, item))
  sequence.value = seq
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
    const carried = {
      note: it.note ?? '',
      noteSource: null,
      planKey: null,
      sourceDocId: it.source_doc_id ?? null,
      sourceRevisionNo: it.source_revision_no ?? null,
      providerId: it.provider_id ?? null,
      providerDisplayName: it.provider_display_name ?? null,
      providerRegistered: it.provider_registered ?? null,
      typeChanged: false,
    }
    if (AUTO_TYPES.has(it.type)) {
      // 0408 M0019 re-rejection 2·3: the report row keeps the note stored ON it. Blanking it here
      // meant a save that changed nothing still erased whatever the plan had written for
      // NR/TR — and [Auto Approval] delivers exactly that row's note to its worker.
      result.push({
        id, type: it.type, label: it.label || docTypeStore.getLabel(it.type),
        isAuto: true, autoOfId: lastManualId, ...carried, origin: 'auto',
      })
    } else {
      lastManualId = id
      result.push({
        id, type: it.type, label: it.label || docTypeStore.getLabel(it.type),
        isAuto: false, autoOfId: null, ...carried,
        // L0011 §2.1 origin_of_loaded_row: no stored source means a person put it there.
        origin: it.source_doc_id ? 'plan' : 'manual',
      })
    }
  }
  return result
}

async function loadSequence() {
  if (!props.docId) return
  loading.value = true
  loadError.value = false
  metaContractMissing.value = false
  lockedItems.value = []
  sequence.value = []
  idCounter.value = 0
  try {
    const res = await getRequest<any>('/api/v1/workflow/sequence', { doc_id: props.docId })
    const data = (res.data as any)
    noteMaxChars.value = Number(data?.note_max_chars) || 1000
    // 0406 T0013: the canonical handler answers `items`. A body without that key is a
    // pre-0406 duplicate-route response, so keep reading its rows — a list the user can SEE
    // beats an empty dialog — but treat the shape itself as the missing contract. Dropping
    // to `[]` here would silence the guard below and let a save wipe the whole pending block.
    const legacyShape = !Array.isArray(data?.items)
    const items: ServerItem[] = data?.items ?? data?.sequence ?? []
    lockedItems.value = items.filter(it => it.status !== 'pending')
    const pendingItems = items.filter(it => it.status === 'pending')
    metaContractMissing.value = legacyShape || pendingItems.some(row =>
      !['note', 'source_doc_id', 'source_revision_no', 'provider_id', 'provider_display_name'].every(key =>
        Object.prototype.hasOwnProperty.call(row, key),
      ),
    )
    // Missing provenance also makes origin inference wrong; never let that normalized row save.
    sequence.value = dbItemsToSequence(pendingItems)
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

// ── 0399 — opening on a poured plan ────────────────────────────────────────────

/** Build the editing state straight from P0013 ①'s rows. No GET: the response already
 *  describes the whole sequence, locked rows included, merged the way the mode says. */
function applyPour(payload: PourPayload) {
  loading.value = false
  loadError.value = false
  metaContractMissing.value = false
  idCounter.value = 0
  lockedItems.value = payload.rows
    .filter(row => row.locked)
    .map((row, index) => ({
      id: -(index + 1),
      type: row.type,
      label: row.label,
      status: row.status,
      sort_order: index,
      note: row.note,
      source_doc_id: row.source_doc_id,
      source_revision_no: row.source_revision_no,
      provider_id: row.provider_id,
      provider_display_name: row.provider_display_name,
      provider_registered: row.provider_registered,
    }))
  sequence.value = payload.rows
    .filter(row => !row.locked)
    .map(row => ({
      id: ++idCounter.value,
      type: row.type,
      label: row.label || docTypeStore.getLabel(row.type),
      isAuto: row.origin === 'auto',
      autoOfId: null,
      note: row.note,
      noteSource: row.note_source ?? null,
      origin: row.origin,
      planKey: row.plan_key,
      sourceDocId: row.source_doc_id,
      sourceRevisionNo: row.source_revision_no,
      providerId: row.provider_id ?? null,
      providerDisplayName: row.provider_display_name ?? null,
      providerRegistered: row.provider_registered ?? null,
      typeChanged: false,
    }))
  // The report rows arrive from the server already paired; relink each to the row above it
  // so moving/deleting an instruction keeps carrying its report, exactly as a hand-built
  // list does. Their identity is positional in the response — there is no id to match on.
  let lastManualId: number | null = null
  for (const item of sequence.value) {
    if (item.isAuto) item.autoOfId = lastManualId
    else lastManualId = item.id
  }
  pourBaselineIds.value = new Set(sequence.value.filter(s => !s.isAuto).map(s => s.id))
  pourSession.value = payload
}

/** L0011 §3.3 — one step, and it goes all the way back: the pour AND every edit made after
 *  it. Keeping the later edits would leave rows that were shaped around plan rows which are
 *  no longer there, i.e. a list nobody wrote. Nothing was saved, so re-reading the stored
 *  sequence IS the pre-pour state. */
async function revertPour() {
  pourSession.value = null
  pourBaselineIds.value = new Set()
  await loadSequence()
}

function shortCodeOf(docId: string): string {
  const tail = docId.split('.').pop() ?? docId
  const [seq, code] = tail.split('-')
  return code ? `${code}${seq}` : tail
}

function notificationText(note: PourNotification): string {
  const key = `main.work_plan_pour.notify_${note.code}`
  return t(key, {
    n: note.count,
    types: (note.types ?? []).join(' · '),
  })
}

async function save() {
  if (saving.value || wouldEmptyDecided.value || metaContractMissing.value) return
  saving.value = true
  try {
    await patchRequest('/api/v1/workflow/sequence', {
      doc_id: props.docId,
      items: sequence.value.map(it => ({
        type: it.type,
        label: it.label,
        note: it.note,
        source_doc_id: it.sourceDocId,
        source_revision_no: it.sourceRevisionNo,
        provider_id: it.providerId,
        provider_display_name: it.providerDisplayName,
      })),
      // P0013 ②: sent only when a plan was poured. On an ordinary save there is no earlier
      // snapshot to be stale against, and demanding one would break every other caller.
      expected_workflow_tag: pourSession.value?.workflowTag,
      // 0403 NR0004 F2·F3·F4: only carried on a pour save, by the same rule. The fingerprint
      // only sees whether the sequence moved, so it couldn't catch a case where the plan
      // changed without touching the workflow. This value is the evidence for that judgment,
      // and at the same time the evidence that records which plan this save poured, in the
      // apply history.
      expected_plan: pourSession.value
        ? {
            wp_doc_id: pourSession.value.wpDocId,
            wp_revision_no: pourSession.value.wpRevisionNo,
            mode: pourSession.value.mode,
          }
        : undefined,
    })
    showToast(t('main.workflow_edit_modal.toast_saved'), 'success')
    pourSession.value = null
    pourBaselineIds.value = new Set()
    emit('saved')
    emit('update:visible', false)
  } catch (e: any) {
    if (e?.response?.data?.error === 'sequence_changed') {
      showToast(t('main.work_plan_pour.error_sequence_changed'), 'error')
    } else if (e?.response?.data?.error === 'wp_changed') {
      // 0403 NR0004 F2: the workflow stayed the same but the plan changed. The rows currently
      // on screen came from the stale plan, so don't overwrite — tell the user to reopen and
      // pour the latest plan instead.
      showToast(t('main.work_plan_pour.error_wp_changed'), 'error')
    } else {
      showToast(
        extractApiErrorMessage(e, t('main.workflow_edit_modal.error_save')),
        'error',
      )
    }
  } finally {
    saving.value = false
  }
}

// R0001 group 0208: hand the pending-sequence edit to an AI worker instead of editing here.
// Issues a workflow_sequence_edit token + mention and copies the mention to the clipboard
// (deferred so the click's clipboard activation survives the token round-trip — group 0133).
// The worker then applies the change via PATCH /workflow/sequence; locked steps stay immutable.
async function requestAiSequenceEdit() {
  if (!props.docId || requestingAiEdit.value) return
  requestingAiEdit.value = true
  let token: IssuedToken | null = null
  try {
    const ok = await copyToClipboardDeferred(async () => {
      token = await requestSequenceEdit(props.docId!)
      if (!token) throw new ClipboardAbort()
      return composeMention(token)
    })
    if (ok) showToast(t('main.workflow_edit_modal.toast_ai_mention_copied'), 'success')
    else if (token) showToast(t('main.workflow_edit_modal.error_ai_mention_copy'), 'warning')
  } finally {
    requestingAiEdit.value = false
  }
}

// 0268 B0001 (NR0003 defect 1): run the very same delegation in-app instead of by clipboard.
// POSTs action_scope 'workflow_sequence_edit' to /ai-invoke/start, where the server mints the
// token through request_sequence_edit — the same issuer the copy path calls — so the worker
// reads a byte-identical prompt. The raw token never reaches the browser on this path.
async function invokeAiSequenceEdit() {
  if (!props.docId || invokingAi.value) return
  const parts = props.docId.split('.')
  if (parts.length < 4) {
    showToast(t('main.workflow_edit_modal.error_ai_invoke'), 'error')
    return
  }
  invokingAi.value = true
  try {
    await providerStore.ensureLoaded(parts[0])
    await postRequest('/api/v1/ai-invoke/start', {
      project: parts[0],
      module: parts[1],
      group: parts[2],
      doc_ref: props.docId,
      action_scope: 'workflow_sequence_edit',
      mode: 'single',
      provider_id: providerStore.selectedProviderId || undefined,
    })
    showToast(t('main.workflow_edit_modal.toast_ai_invoke_started'), 'success')
    close()
  } catch (e: any) {
    const data = e?.response?.data
    showToast(data?.message || data?.error?.message || t('main.workflow_edit_modal.error_ai_invoke'), 'error')
  } finally {
    invokingAi.value = false
  }
}

// 0399 L0011 §4.4 — the provider row is drawn only when this project actually has a usable
// provider. loadedProjectId (not just a non-empty list) is what separates "none registered"
// from "not asked yet"; without it the row would flicker in and out on every open.
const providerRowVisible = computed(() =>
  providerStore.loadedProjectId !== null && providerStore.providers.length > 0,
)

// Reset / load state when dialog opens
watch(
  () => props.visible,
  (v) => {
    if (v) {
      if (props.mode === 'edit') {
        // A poured payload already contains the whole sequence (P0013 ①), so re-reading it
        // would only race the state we were handed and throw the poured rows away.
        if (props.poured) applyPour(props.poured)
        else {
          pourSession.value = null
          loadSequence()
        }
        const project = (props.docId ?? '').split('.')[0]
        if (project) providerStore.ensureLoaded(project)
      } else {
        sequence.value = []
        idCounter.value = 0
        pourSession.value = null
        pourBaselineIds.value = new Set()
      }
    } else {
      // M0020 "[작업계획 적용] 한다음에 저장하지도 않았는데 주구장창 적용되어 있다":
      // if closed without saving, the poured-in list is discarded right there. The next
      // window opened always re-reads and redraws the sequence actually saved on the server.
      pourSession.value = null
      pourBaselineIds.value = new Set()
      sequence.value = []
      lockedItems.value = []
      idCounter.value = 0
    }
  },
  // A dialog mounted with visible already true never sees a transition, so without this it
  // would come up empty — no sequence, no poured rows, no provider list.
  { immediate: true },
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

/* ── 0399 Apply Work Plan — pour-result notice strip (mockup fgh29xnk v3 · screens 2/3) ── */
.wdm-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  padding: 10px 20px;
  background: #ecfdf5;
  border-bottom: 1px solid #a7f3d0;
  font-size: .74rem;
  line-height: 1.5;
  color: #065f46;
}
.wdm-banner i {
  font-size: 1rem;
  flex-shrink: 0;
}
.wdm-banner .wdm-banner-spacer {
  flex: 1;
}
.wdm-banner--warn {
  background: #fffbeb;
  border-bottom-color: #fde68a;
  color: #92400e;
}
.wdm-undo {
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: var(--r, 8px);
  border: 1px solid #34d399;
  background: #fff;
  color: #047857;
  font-size: .69rem;
  font-weight: 700;
  cursor: pointer;
}
.wdm-undo:hover {
  background: #d1fae5;
}

/* A row that came from the plan: distinguished at a glance by a green strip on the left */
.wdm-seq-item.from-plan {
  border-color: #86efac;
  box-shadow: inset 3px 0 0 #16a34a;
}
.wdm-seq-main {
  flex: 1;
  min-width: 0;
}
.wdm-seq-msg {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 3px;
  font-size: .69rem;
  color: var(--text-s);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wdm-seq-msg i {
  color: #16a34a;
  font-size: .72rem;
  flex-shrink: 0;
}
.wdm-seq-msg--empty {
  color: #b45309;
}
.wdm-seq-msg--empty i {
  color: #d97706;
}
.wdm-plan-badge {
  flex-shrink: 0;
  padding: 1px 7px;
  border-radius: 20px;
  background: #dcfce7;
  color: #166534;
  border: 1px solid #86efac;
  font-size: .62rem;
  font-weight: 700;
}
/* 0399 T0016 — a row whose plan note was cleared because its type was changed */
.wdm-changed-badge {
  flex-shrink: 0;
  padding: 1px 7px;
  border-radius: 20px;
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fde68a;
  font-size: .62rem;
  font-weight: 700;
}
/* 0399 T0016 — per-row note input. Only the underline remains so it reads like static text, but it's actually an input field. */
.wdm-note-input {
  flex: 1;
  min-width: 0;
  border: none;
  border-bottom: 1px dashed transparent;
  background: transparent;
  padding: 0;
  font: inherit;
  font-size: .69rem;
  color: inherit;
}
.wdm-note-input:hover,
.wdm-note-input:focus {
  border-bottom-color: var(--border-d);
  outline: none;
}
.wdm-note-input::placeholder {
  color: #b45309;
}
.wdm-note-input--over {
  border-bottom-color: var(--danger);
  color: var(--danger);
}
.wdm-note-count {
  flex-shrink: 0;
  color: var(--text-m);
  font-size: .58rem;
}
.wdm-note-count--over {
  color: var(--danger);
  font-weight: 700;
}
/* 0399 T0016 — small dropdown for changing a row's type. The doc-tag color badge stays as-is, placed beside it. */
.wdm-type-select {
  flex-shrink: 0;
  font-size: .6rem;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--text-m);
  padding: 1px 2px;
  cursor: pointer;
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

/* ── Footer: provider picker pinned to the far left, buttons right-aligned ── */
.wdm-provider {
  flex-shrink: 0;
  margin-right: 10px;
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
.wdm-provider-chip {
  display:inline-flex; align-items:center; max-width:180px; padding:2px 7px;
  border-radius:999px; background:var(--surface-h); color:var(--text-m);
  font-size:.67rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.wdm-provider-chip.is-unavailable { color:#b45309; background:#fff7ed; }
</style>
