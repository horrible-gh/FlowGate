<template>
  <teleport to="body">
    <div
      v-if="visible"
      ref="overlayRef"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
    >
      <div class="modal-box modal-dhd" role="dialog" aria-modal="true" aria-labelledby="dhd-title">

        <!-- Header -->
        <div class="modal-hd">
          <div>
            <div class="modal-title" id="dhd-title">
              <AppIcon name="stack" style="color:var(--primary); margin-right:6px;" />{{ t('main.design_handoff_dialog.title') }}
            </div>
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- Body -->
        <div class="modal-bd dhd-body">

          <!-- 1. Target card -->
          <div>
            <div class="dhd-section-title">
              <AppIcon name="info" style="color:var(--primary);" />{{ t('main.design_handoff_dialog.target_document') }}
            </div>
            <div class="dhd-target-card">
              <span class="doc-tag c-R" style="font-size:.72rem; padding:2px 7px;">R</span>
              <span class="dhd-target-id">{{ docRef }}</span>
              <span class="dhd-target-meta">{{ projectId }} / {{ groupId }}</span>
              <span class="dhd-badge-done">
                <AppIcon name="check-circle" /> {{ t('main.design_handoff_dialog.reviewed') }}
              </span>
            </div>
          </div>

          <div class="dhd-divider"></div>

          <!-- 2. Type checkboxes -->
          <div>
            <div class="dhd-section-title">
              <AppIcon name="list-checks" style="color:var(--primary);" />{{ t('main.design_handoff_dialog.document_type') }}
              <span class="dhd-section-hint">— {{ t('main.design_handoff_dialog.workflow_hint') }}</span>
            </div>
            <div class="dhd-check-group">
              <label
                v-for="t in ALL_TYPES"
                :key="t.code"
                class="dhd-check-item"
                :class="{ checked: checkedSet.has(t.code) }"
                @click.prevent="toggleType(t.code)"
              >
                <span class="dhd-check-box">
                  <AppIcon name="check" class="dhd-check-ico" />
                </span>
                <span class="dhd-check-type-badge" :class="`c-${t.code}`">{{ t.code }}</span>
                <span class="dhd-check-label">{{ t.label }}</span>
              </label>
            </div>
          </div>

          <div class="dhd-divider"></div>

          <!-- 3. Mode radio -->
          <div>
            <div class="dhd-section-title">
              <AppIcon name="sliders-horizontal" style="color:var(--primary);" />{{ t('main.design_handoff_dialog.issuance_mode') }}
            </div>
            <div class="dhd-radio-group">
              <label
                class="dhd-radio-item"
                :class="{ selected: mode === 'batch' }"
                @click="mode = 'batch'"
              >
                <span class="dhd-radio-dot"></span>
                <div class="dhd-radio-text">
                  <span class="dhd-radio-label">{{ t('main.design_handoff_dialog.batch') }}</span>
                  <div class="dhd-radio-desc">{{ t('main.design_handoff_dialog.batch_desc') }}</div>
                </div>
              </label>
              <label
                class="dhd-radio-item"
                :class="{ selected: mode === 'single' }"
                @click="mode = 'single'"
              >
                <span class="dhd-radio-dot"></span>
                <div class="dhd-radio-text">
                  <span class="dhd-radio-label">{{ t('main.design_handoff_dialog.single') }}</span>
                  <div class="dhd-radio-desc">{{ t('main.design_handoff_dialog.single_desc') }}</div>
                </div>
              </label>
            </div>
          </div>

          <div class="dhd-divider"></div>

          <!-- 4. Mention preview -->
          <div>
            <div class="dhd-section-title">
              <AppIcon name="eye" style="color:var(--primary);" />{{ t('main.design_handoff_dialog.mention_preview') }}
            </div>
            <div class="dhd-preview-box">
              <span v-if="orderedChecked.length === 0" class="dhd-preview-empty">
                {{ t('main.design_handoff_dialog.select_type') }}
              </span>
              <template v-else>{{ mentionText }}</template>
            </div>
          </div>

        </div><!-- /modal-bd -->

        <!-- Footer -->
        <div class="modal-ft">
          <button type="button" class="btn btn-ghost" @click="onClose">{{ t('common.cancel') }}</button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="orderedChecked.length === 0"
            @click="onCopyMention"
          >
            <AppIcon name="copy" /> {{ t('main.design_handoff_dialog.copy_mention') }}
          </button>
          <!-- Group 0223: in-app invoke beside every copy-mention (running alongside it, not either/or). -->
          <button
            type="button"
            class="btn btn-primary"
            :disabled="orderedChecked.length === 0"
            @click="onInvokeAi"
          >
            <AppIcon name="robot" /> {{ t('main.design_handoff_dialog.invoke_ai') }}
          </button>
        </div>

      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDocTypeStore } from '../stores/docTypeStore'
import { copyToClipboard } from '../utils/clipboard'
import { openClipboardFallback } from '../composables/useClipboardFallback'
import AppIcon from '@shared/AppIcon.vue'

type DesignType = 'D' | 'P' | 'L' | 'DB'

const props = defineProps<{
  visible: boolean
  docRef: string
  projectId: string
  groupId: string
  defaultTypes: DesignType[]
  nextStepLabel?: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'copy-mention': [payload: { types: DesignType[]; mode: string; copied: boolean }]
  'invoke-ai': [payload: { types: DesignType[]; mode: string; firstLabel: string }]
}>()

const docTypeStore = useDocTypeStore()
const { t } = useI18n()

const TYPE_ORDER: DesignType[] = ['D', 'P', 'L', 'DB']
const ALL_TYPES = computed(() => TYPE_ORDER.map(code => ({ code, label: docTypeStore.getLabel(code) })))

const checkedSet = ref<Set<DesignType>>(new Set(props.defaultTypes))
const mode = ref<'batch' | 'single'>('batch')
const overlayRef = ref<HTMLElement | null>(null)

watch(
  () => props.visible,
  (val) => {
    if (val) {
      checkedSet.value = new Set(props.defaultTypes)
      mode.value = 'batch'
      setTimeout(() => overlayRef.value?.focus(), 50)
    }
  },
)

const orderedChecked = computed<DesignType[]>(() =>
  TYPE_ORDER.filter(t => checkedSet.value.has(t)),
)

const mentionText = computed<string>(() => {
  const types = orderedChecked.value
  if (types.length === 0) return ''
  if (mode.value === 'batch') {
    const typeStr = types.join(' / ')
    return t('main.design_handoff_dialog.mention_batch', {
      types: typeStr,
      docRef: props.docRef,
    })
  } else {
    const first = types[0]
    const firstLabel = docTypeStore.getLabel(first)
    return t('main.design_handoff_dialog.mention_single', {
      label: firstLabel,
      code: first,
      docRef: props.docRef,
    })
  }
})

function toggleType(code: DesignType) {
  const next = new Set(checkedSet.value)
  if (next.has(code)) {
    next.delete(code)
  } else {
    next.add(code)
  }
  checkedSet.value = next
}

function onClose() {
  emit('update:visible', false)
}

async function onCopyMention() {
  const types = orderedChecked.value
  if (types.length === 0) return
  // Shared honest write (B0001 / group 0221) — the previous local copy ignored the result,
  // so a failed write still closed the dialog claiming success. On failure the manual-copy
  // fallback modal carries the mention text past this dialog's close.
  const copied = await copyToClipboard(mentionText.value)
  if (!copied) openClipboardFallback(mentionText.value)
  emit('copy-mention', { types, mode: mode.value, copied })
  emit('update:visible', false)
}

// Group 0223: hand the same type pick to the in-app invoke path. The server rebuilds
// the identical handoff text (invoke_mention_service.build_design_handoff_context);
// single mode needs the localized first-type label, which only the client knows.
function onInvokeAi() {
  const types = orderedChecked.value
  if (types.length === 0) return
  emit('invoke-ai', { types, mode: mode.value, firstLabel: docTypeStore.getLabel(types[0]) })
  emit('update:visible', false)
}
</script>

<style scoped>
.modal-dhd {
  width: 560px;
  max-width: 94vw;
}

.dhd-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dhd-section-title {
  font-size: .68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-m);
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}

.dhd-section-hint {
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
  font-size: .7rem;
  color: var(--text-m);
}

.dhd-target-card {
  background: var(--surface-h, #f8fafc);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 6px);
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: .82rem;
}

.dhd-target-id {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: .85rem;
  color: var(--text);
}

.dhd-target-meta {
  font-size: .75rem;
  color: var(--text-m);
}

.dhd-badge-done {
  margin-left: auto;
  background: var(--success-l, #dcfce7);
  color: var(--success, #16a34a);
  border: 1px solid #86efac;
  border-radius: var(--r-sm, 4px);
  padding: 2px 7px;
  font-size: .68rem;
  font-weight: 600;
}

.dhd-divider {
  height: 1px;
  background: var(--border, #e2e8f0);
}

/* Checkboxes */
.dhd-check-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dhd-check-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 6px);
  cursor: pointer;
  transition: all 0.1s;
  user-select: none;
}

.dhd-check-item:hover {
  background: var(--surface-h, #f8fafc);
  border-color: var(--border-d, #cbd5e1);
}

.dhd-check-item.checked {
  border-color: var(--primary, #2563eb);
  background: var(--primary-l, #eff6ff);
}

.dhd-check-box {
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--border-d, #cbd5e1);
  border-radius: 4px;
  background: var(--surface, #fff);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.1s;
}

.dhd-check-item.checked .dhd-check-box {
  background: var(--primary, #2563eb);
  border-color: var(--primary, #2563eb);
}

.dhd-check-ico {
  font-size: .6rem;
  color: transparent;
}

.dhd-check-item.checked .dhd-check-ico {
  color: white;
}

.dhd-check-type-badge {
  font-size: .72rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: var(--r-sm, 4px);
  color: white;
  min-width: 32px;
  text-align: center;
  flex-shrink: 0;
}

.dhd-check-label {
  font-size: .82rem;
  font-weight: 500;
  color: var(--text-s, #475569);
  flex: 1;
}

.dhd-check-item.checked .dhd-check-label {
  color: var(--text, #1e293b);
}

/* Radios */
.dhd-radio-group {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.dhd-radio-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 9px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 6px);
  cursor: pointer;
  transition: all 0.1s;
  user-select: none;
}

.dhd-radio-item:hover {
  background: var(--surface-h, #f8fafc);
  border-color: var(--border-d, #cbd5e1);
}

.dhd-radio-item.selected {
  border-color: var(--primary, #2563eb);
  background: var(--primary-l, #eff6ff);
}

.dhd-radio-dot {
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--border-d, #cbd5e1);
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 1px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.1s;
  background: var(--surface, #fff);
}

.dhd-radio-item.selected .dhd-radio-dot {
  border-color: var(--primary, #2563eb);
  background: var(--primary, #2563eb);
  box-shadow: inset 0 0 0 3px white;
}

.dhd-radio-text {
  flex: 1;
}

.dhd-radio-label {
  font-size: .82rem;
  font-weight: 600;
  color: var(--text-s, #475569);
  display: block;
}

.dhd-radio-item.selected .dhd-radio-label {
  color: var(--primary, #2563eb);
}

.dhd-radio-desc {
  font-size: .73rem;
  color: var(--text-m, #64748b);
  margin-top: 2px;
}

/* Preview */
.dhd-preview-box {
  background: #1a2744;
  border: 1px solid #2d3f5c;
  border-radius: var(--r, 6px);
  padding: 14px 16px;
  font-family: 'JetBrains Mono', monospace;
  font-size: .78rem;
  color: rgba(255, 255, 255, .85);
  line-height: 1.75;
  white-space: pre-wrap;
  min-height: 90px;
}

.dhd-preview-empty {
  color: rgba(255, 255, 255, .3);
  font-style: italic;
}
</style>
