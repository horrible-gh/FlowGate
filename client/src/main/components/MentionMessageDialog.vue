<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="cancel">
      <div class="modal-box mm-box">
        <!-- Header -->
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="copy" style="color:var(--primary); margin-right:6px;" />
            {{ t('main.next_action_modal.mm_dialog_title') }}
          </span>
          <button class="modal-close" type="button" @click="cancel"><AppIcon name="x" /></button>
        </div>

        <!-- Body -->
        <div class="modal-bd mm-body">
          <div class="form-group">
            <label class="form-label">{{ t('main.next_action_modal.mm_dialog_doc_type') }}</label>
            <select class="form-ctrl" :value="selectedType" @change="onTypeChange(($event.target as HTMLSelectElement).value)">
              <option v-for="opt in typeOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </select>
          </div>

          <div class="mm-pick-label">{{ t('main.next_action_modal.mm_dialog_pick') }}</div>
          <div class="mm-candidate-list">
            <div v-if="loading" class="mm-empty"><AppIcon name="spinner" spin /></div>
            <div v-else-if="localCandidates.length === 0" class="mm-empty">
              {{ t('main.next_action_modal.mm_dialog_empty') }}
            </div>
            <label
              v-for="c in localCandidates"
              :key="c.id"
              class="mm-candidate"
              :class="{ selected: selectedIds.includes(c.id) }"
            >
              <input type="checkbox" :value="c.id" v-model="selectedIds" />
              <span class="mm-candidate-text">{{ c.message }}</span>
              <span v-if="c.doc_type === WILDCARD_DOC_TYPE" class="mm-all-tag">
                {{ t('main.next_action_modal.mm_dialog_all_label') }}
              </span>
            </label>
          </div>
        </div>

        <!-- Footer -->
        <div class="modal-ft">
          <button class="btn btn-secondary" type="button" @click="cancel">{{ t('common.cancel') }}</button>
          <button class="btn btn-primary" type="button" :disabled="selectedIds.length === 0" @click="confirm">
            <AppIcon name="plus" /> {{ t('main.next_action_modal.mm_dialog_add') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import { useToast } from './common/useToast'
import { buildCandidateList, WILDCARD_DOC_TYPE, type MessageEntry } from '../utils/mentionMessages'

interface DocTypeOption { code: string; label: string }

const props = defineProps<{
  visible: boolean
  projectId: string
  docType: string
  docTypes: DocTypeOption[]
  candidates: MessageEntry[]
}>()

const emit = defineEmits<{
  select: [messages: string[]]
  cancel: []
}>()

const { t } = useI18n()
const { showToast } = useToast()

const selectedType = ref(props.docType)
const localCandidates = ref<MessageEntry[]>([])
const selectedIds = ref<number[]>([])
const loading = ref(false)

// [All]('*') first, then active project doc types; ensure the current type is present
// even if it is not in the supplied list (defensive).
const typeOptions = computed<{ value: string; label: string }[]>(() => {
  const opts: { value: string; label: string }[] = [
    { value: WILDCARD_DOC_TYPE, label: t('main.next_action_modal.mm_dialog_all_label') },
  ]
  const seen = new Set<string>([WILDCARD_DOC_TYPE])
  for (const dt of props.docTypes) {
    if (dt.code && !seen.has(dt.code)) {
      opts.push({ value: dt.code, label: dt.label || dt.code })
      seen.add(dt.code)
    }
  }
  if (props.docType && props.docType !== WILDCARD_DOC_TYPE && !seen.has(props.docType)) {
    opts.push({ value: props.docType, label: props.docType })
  }
  return opts
})

// L0007 §2.3 — re-fetch + re-merge on type change. On failure: error toast, keep state
// (do not close, do not clear the existing candidates).
async function onTypeChange(newType: string) {
  loading.value = true
  try {
    const res = await getRequest<{ data: MessageEntry[] }>(
      `/api/v1/projects/${encodeURIComponent(props.projectId)}/messages`,
      { doc_type: newType },
    )
    selectedType.value = newType
    localCandidates.value = buildCandidateList(res.data?.data ?? [], newType)
    selectedIds.value = []
  } catch {
    showToast(t('main.next_action_modal.copy_mention_error_toast'), 'danger')
    // keep selectedType, localCandidates as-is (L0007 §2.3 / §5)
  } finally {
    loading.value = false
  }
}

function confirm() {
  // Collect selected candidates in DISPLAY order (buildCandidateList already deduped/sorted),
  // so multi-select preserves on-screen order and never duplicates a body (NR0007 §3).
  const picked = new Set(selectedIds.value)
  const messages = localCandidates.value.filter((c) => picked.has(c.id)).map((c) => c.message)
  if (messages.length === 0) return
  emit('select', messages)
}

function cancel() {
  emit('cancel')
}

// Reset internal state from props each time the dialog opens.
watch(
  () => props.visible,
  (val) => {
    if (!val) return
    selectedType.value = props.docType
    localCandidates.value = [...props.candidates]
    selectedIds.value = []
    loading.value = false
  },
  { immediate: true },
)
</script>

<style scoped>
.mm-box {
  width: 460px;
  max-width: 94vw;
}
.mm-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mm-pick-label {
  font-size: .74rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--text-m);
}
.mm-candidate-list {
  border: 1px solid var(--border);
  border-radius: var(--r);
  max-height: 280px;
  overflow-y: auto;
}
.mm-candidate {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background var(--tr);
}
.mm-candidate:last-child { border-bottom: none; }
.mm-candidate:hover { background: var(--surface-h); }
.mm-candidate.selected { background: var(--primary-l); }
.mm-candidate-text {
  flex: 1;
  font-size: .84rem;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
.mm-all-tag {
  font-size: .62rem;
  font-weight: 700;
  color: var(--text-m);
  font-family: 'JetBrains Mono', monospace;
  flex-shrink: 0;
}
.mm-empty {
  padding: 24px;
  text-align: center;
  font-size: .8rem;
  color: var(--text-m);
  font-style: italic;
}
</style>
