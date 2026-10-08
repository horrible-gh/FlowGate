<template>
  <!-- flowgate.v02.0043 — File › 새 Group: the v0.1 module create (features/project createGroup)
       with the two fields that contract reads — display name and key — and the existing key
       suggestion (POST /slug/romanize), as the v0.1 create modal and the Project create child
       offer them. Emits the created Group; the Shell re-reads the tree and opens it. -->
  <ModalShell :open="open" size="md" close-on-backdrop :aria-describedby="submitError ? errorId : undefined" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="dfm-head">
        <h2 :id="titleId" class="dfm-title">{{ t('gui.designer.file_menu.group_new_title') }}</h2>
        <IconButton :label="t('gui.designer.file_menu.close')" variant="ghost" @click="emit('close')">✕</IconButton>
      </div>
    </template>

    <template #default>
      <Alert v-if="submitError" :id="errorId" tone="danger" class="dfm-alert" data-test="designer-group-create-error">{{ submitError }}</Alert>
      <form class="dfm-form" data-test="designer-group-create" @submit.prevent="submit">
        <div class="dfm-field">
          <label class="dfm-label" for="dgc-title">{{ t('gui.designer.file_menu.name') }}</label>
          <Input
            id="dgc-title"
            :model-value="title"
            :invalid="showTitleError"
            data-modal-autofocus
            data-test="designer-group-create-title"
            @update:model-value="onTitleChange"
          />
          <p v-if="showTitleError" class="dfm-field-error">{{ t('gui.designer.file_menu.name_required') }}</p>
        </div>

        <div class="dfm-field">
          <label class="dfm-label" for="dgc-name">{{ t('gui.designer.file_menu.id') }}</label>
          <div class="dfm-id-row">
            <Input
              id="dgc-name"
              :model-value="name"
              :invalid="showNameError"
              data-test="designer-group-create-name"
              @update:model-value="onNameChange"
            />
            <Button type="button" variant="secondary" size="sm" :loading="suggesting" data-test="designer-group-create-suggest" @click="suggestNow">
              {{ t('gui.designer.file_menu.suggest') }}
            </Button>
          </div>
          <p v-if="showNameEmptyError" class="dfm-field-error">{{ t('gui.designer.file_menu.id_required') }}</p>
          <p v-else-if="showNameFormatError" class="dfm-field-error">{{ t('gui.designer.file_menu.id_format') }}</p>
        </div>
      </form>
    </template>

    <template #footer>
      <div class="dfm-actions">
        <Button variant="secondary" data-test="designer-group-create-cancel" @click="emit('close')">{{ t('gui.designer.file_menu.cancel') }}</Button>
        <Button variant="primary" :disabled="submitting" :loading="submitting" data-test="designer-group-create-submit" @click="submit">
          {{ t('gui.designer.file_menu.create') }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Alert from '../../../components/ui/Alert.vue'
import Button from '../../../components/ui/Button.vue'
import IconButton from '../../../components/ui/IconButton.vue'
import Input from '../../../components/ui/Input.vue'
import ModalShell from '../../../components/ui/ModalShell.vue'
import { useProjectStore, type CreatedGroup } from '@/features/project'

const props = defineProps<{
  open: boolean
  projectId: string | null
}>()

const emit = defineEmits<{
  close: []
  created: [group: CreatedGroup]
}>()

const { t } = useI18n()
const projectStore = useProjectStore()

const errorId = 'designer-group-create-error'

const title = ref('')
const name = ref('')
const nameTouched = ref(false)
const suggesting = ref(false)
const submitting = ref(false)
const submitError = ref<string | null>(null)
const attemptedSubmit = ref(false)

// Same key rule as the server (module_routes.validate_module_name, M031).
const NAME_RE = /^[a-z0-9_-]+$/

const titleValid = computed(() => title.value.trim() !== '')
const nameEmpty = computed(() => name.value.trim() === '')
const nameFormatValid = computed(() => NAME_RE.test(name.value.trim()))
const showTitleError = computed(() => attemptedSubmit.value && !titleValid.value)
const showNameEmptyError = computed(() => attemptedSubmit.value && nameEmpty.value)
const showNameFormatError = computed(() => !nameEmpty.value && !nameFormatValid.value)
const showNameError = computed(() => showNameEmptyError.value || showNameFormatError.value)
const canSubmit = computed(() => titleValid.value && !nameEmpty.value && nameFormatValid.value && !submitting.value)

// Every open is a new session: a response of an earlier session never reaches this one.
let generation = 0
let suggestTimer: ReturnType<typeof setTimeout> | null = null
let suggestSeq = 0

function cancelSuggest() {
  if (suggestTimer) {
    clearTimeout(suggestTimer)
    suggestTimer = null
  }
  suggestSeq += 1
  suggesting.value = false
}

function reset() {
  title.value = ''
  name.value = ''
  nameTouched.value = false
  submitting.value = false
  submitError.value = null
  attemptedSubmit.value = false
  cancelSuggest()
}

watch(
  () => props.open,
  (isOpen) => {
    generation += 1
    if (isOpen) reset()
    else cancelSuggest()
  },
)

onBeforeUnmount(cancelSuggest)

async function applySuggestion(text: string, manual: boolean) {
  const myGeneration = generation
  const mySeq = ++suggestSeq
  if (manual) suggesting.value = true
  try {
    const suggested = await projectStore.suggestSlug(text)
    if (myGeneration !== generation || mySeq !== suggestSeq) return
    if (!manual && nameTouched.value) return
    if (suggested) {
      name.value = suggested
      if (manual) nameTouched.value = false
    }
  } catch {
    // Best-effort suggestion only; the key can still be typed.
  } finally {
    if (mySeq === suggestSeq) suggesting.value = false
  }
}

// While the key has not been typed by hand it follows the name (v0.1 create modal parity).
function onTitleChange(value: string) {
  title.value = value
  cancelSuggest()
  if (nameTouched.value) return
  const text = value.trim()
  if (!text) {
    name.value = ''
    return
  }
  suggestTimer = setTimeout(() => {
    suggestTimer = null
    if (!nameTouched.value) void applySuggestion(text, false)
  }, 400)
}

function onNameChange(value: string) {
  cancelSuggest()
  nameTouched.value = true
  name.value = value
}

function suggestNow() {
  const text = title.value.trim()
  if (!text) return
  cancelSuggest()
  void applySuggestion(text, true)
}

async function submit() {
  attemptedSubmit.value = true
  const pid = props.projectId
  if (!pid || !canSubmit.value) return
  const myGeneration = generation
  submitting.value = true
  submitError.value = null
  try {
    const created = await projectStore.createGroup(pid, { name: name.value.trim(), title: title.value.trim() })
    if (myGeneration !== generation) return
    emit('created', created)
  } catch (err) {
    if (myGeneration !== generation) return
    submitError.value = err instanceof Error && err.message ? err.message : t('gui.designer.file_menu.create_failed')
  } finally {
    if (myGeneration === generation) submitting.value = false
  }
}
</script>

<style scoped>
.dfm-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 20px 24px 0;
}

.dfm-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--fg2-text);
}

.dfm-alert {
  margin: 16px 0 0;
}

.dfm-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 0 4px;
}

.dfm-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dfm-label {
  font-size: 12px;
  font-weight: 650;
  color: var(--fg2-muted);
}

.dfm-id-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.dfm-id-row > :first-child {
  flex: 1;
  min-width: 0;
}

.dfm-field-error {
  margin: 0;
  font-size: 11px;
  color: var(--fg2-status-danger-fg);
}

.dfm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 24px 20px;
}
</style>
