<template>
  <ModalShell
    :open="open"
    size="md"
    close-on-backdrop
    :aria-describedby="submitError ? errorId : undefined"
    @close="requestClose"
  >
    <template #header="{ titleId }">
      <div class="pcd-head">
        <h2 :id="titleId" class="pcd-title">새 프로젝트</h2>
        <IconButton label="닫기" variant="ghost" @click="requestClose">✕</IconButton>
      </div>
    </template>

    <template #default>
      <Alert v-if="submitError" :id="errorId" tone="danger" class="pcd-alert">{{ submitError }}</Alert>
      <form class="pcd-form" @submit.prevent="handleSubmit">
        <div class="pcd-field">
          <label class="pcd-label" for="pcd-name">이름</label>
          <Input
            id="pcd-name"
            :model-value="name"
            placeholder="프로젝트 이름"
            :invalid="showNameError"
            @update:model-value="onNameChange"
          />
          <p v-if="showNameError" class="pcd-field-error">이름을 입력하세요.</p>
        </div>

        <div class="pcd-field">
          <label class="pcd-label" for="pcd-id">식별자 <span class="pcd-optional">(선택, 비우면 이름에서 자동 생성)</span></label>
          <div class="pcd-id-row">
            <Input
              id="pcd-id"
              :model-value="id"
              placeholder="예: my-project"
              :invalid="showIdError"
              @update:model-value="onIdChange"
            />
            <Button type="button" variant="secondary" size="sm" :loading="suggesting" @click="suggestNow">제안</Button>
          </div>
          <p v-if="showIdError" class="pcd-field-error">영문 소문자, 숫자, -, _ 만 사용할 수 있습니다.</p>
        </div>

        <div class="pcd-field">
          <label class="pcd-label" for="pcd-desc">설명 <span class="pcd-optional">(선택)</span></label>
          <Textarea id="pcd-desc" v-model="description" placeholder="프로젝트 설명" :rows="2" />
        </div>

        <div class="pcd-field">
          <span class="pcd-label" id="pcd-color-label">색상</span>
          <div class="pcd-colors" role="radiogroup" aria-labelledby="pcd-color-label">
            <button
              v-for="c in COLOR_OPTIONS"
              :key="c"
              type="button"
              class="pcd-color"
              :class="{ 'is-selected': color === c }"
              :style="{ background: c }"
              role="radio"
              :aria-checked="color === c"
              :aria-label="`색상 ${c}`"
              @click="color = c"
            >
              <span v-if="color === c" aria-hidden="true">✓</span>
            </button>
          </div>
        </div>
      </form>
    </template>

    <template #footer>
      <div class="pcd-actions">
        <Button variant="secondary" @click="requestClose">취소</Button>
        <Button variant="primary" :disabled="submitting" :loading="submitting" @click="handleSubmit">생성</Button>
      </div>
    </template>
  </ModalShell>

  <ConfirmDialog
    :open="confirmDiscardOpen"
    title="변경사항을 버리시겠습니까?"
    message="입력한 내용이 저장되지 않습니다."
    danger
    confirm-label="버리기"
    cancel-label="계속 작성"
    @confirm="confirmDiscard"
    @cancel="cancelDiscard"
  />
</template>

<script setup lang="ts">
// Project creation child Modal (WP0005 T#2 §2.1/§2.4/§5-§7). Opens nested inside
// ProjectDialog via ModalShell's existing nested-modal contract (D0004 §2.4/§4.2) --
// this component never reimplements backdrop/ESC/focus-trap/nesting-order itself, and
// relying on ModalShell's own `isTopmostModal` guard is what keeps an ESC on this
// (topmost) child from also closing the parent Project Dialog underneath it (T#2 §7).
//
// Fields are exactly the four the backend create endpoint reads -- name/id/description/
// color (T#1 §1/§3, D0004 §2.4/§5.3) -- the v0.1 structure/storage-type/custom-path/
// member fields that never reached that endpoint are not reproduced here.
//
// This component owns the create request itself (via the Project store) and its own
// dirty/duplicate-submit guards; it never decides what the caller does with the created
// Project or with `open` -- it only emits `created`/`close` and lets ProjectDialog own
// both (T#1 §5 "Dialog-local 상태는 store에 없다" extends to "child open/close state is
// the parent's").
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import Alert from '../../components/ui/Alert.vue'
import Button from '../../components/ui/Button.vue'
import ConfirmDialog from '../../components/ui/ConfirmDialog.vue'
import IconButton from '../../components/ui/IconButton.vue'
import Input from '../../components/ui/Input.vue'
import ModalShell from '../../components/ui/ModalShell.vue'
import Textarea from '../../components/ui/Textarea.vue'
import { suggestProjectSlug } from './api'
import { useProjectStore } from './store'
import type { Project } from './types'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; created: [project: Project] }>()

const store = useProjectStore()

const errorId = 'project-create-error'

const name = ref('')
const id = ref('')
const idTouched = ref(false)
const description = ref('')

// Same 8-swatch palette the v0.1 create form offered (client/src/settings/views/
// projects/ProjectsView.vue) -- the backend create input accepts an arbitrary color
// string, but this UI only ever sends one of these, matching the existing UX contract.
const COLOR_OPTIONS = ['#2563eb', '#7c3aed', '#db2777', '#0891b2', '#16a34a', '#d97706', '#ea580c', '#64748b']
const DEFAULT_COLOR = COLOR_OPTIONS[0]
const color = ref(DEFAULT_COLOR)

const suggesting = ref(false)
const submitting = ref(false)
const submitError = ref<string | null>(null)
const attemptedSubmit = ref(false)
const confirmDiscardOpen = ref(false)

const nameValid = computed(() => name.value.trim() !== '')
// project_id is optional at the API boundary (backend slugifies from the name when
// omitted) -- only a non-empty value must match the existing id format contract.
const idValid = computed(() => id.value.trim() === '' || /^[a-z0-9_-]+$/.test(id.value.trim()))
const showNameError = computed(() => attemptedSubmit.value && !nameValid.value)
const showIdError = computed(() => id.value.trim() !== '' && !idValid.value)
const canSubmit = computed(() => nameValid.value && idValid.value && !submitting.value)
const isDirty = computed(
  () => name.value.trim() !== '' || id.value.trim() !== '' || description.value.trim() !== '' || color.value !== DEFAULT_COLOR,
)

function resetForm(): void {
  name.value = ''
  id.value = ''
  idTouched.value = false
  description.value = ''
  color.value = DEFAULT_COLOR
  suggestingOwnerTicket = null
  suggesting.value = false
  submitting.value = false
  submitError.value = null
  attemptedSubmit.value = false
  confirmDiscardOpen.value = false
}

// Independent of the Project store's own request epoch (which only tracks
// fetchProjects/createProject/closeDialog on the *parent* Project Dialog, T#1 §5): this
// child Modal can itself be closed and reopened (a fresh create session) while a
// previous submit from an earlier session is still in flight, since the store's
// createProject() call already started is not cancelled by that. `generation` bumps on
// every open-state change so a stale submit's eventual resolution can recognize it no
// longer belongs to the session now on screen and never emits `created`/shows its error/
// touches `submitting` for it (T#2 §9 "child ... Dialog lifecycle이 바뀐 뒤 늦게 도착한
// 응답이 최신 selection/current/open 상태를 덮어쓰지 않음").
const generation = ref(0)

// Every suggestProjectSlug() call -- whether the 400ms auto-suggest debounce fired it or
// the "제안" button did -- takes a ticket from this single counter. A response only ever
// applies id/idTouched if its ticket is still the latest one taken, so two overlapping
// calls (auto vs manual, or two names typed in quick succession) always resolve in
// request order, never in whichever order their network responses happen to land (T#2 §9
// "이전 요청보다 최신 요청의 결과가 우선"). `cancelPendingSuggest` both clears any debounce
// timer that has not fired yet and burns a fresh ticket so an already in-flight call's
// eventual resolution is recognized as stale too.
let suggestTicketSeq = 0
let latestSuggestTicket = 0
let suggestDebounce: ReturnType<typeof setTimeout> | null = null

// The one ticket (if any) currently holding `suggesting` open -- only the manual "제안"
// button sets `suggesting`, never the silent auto-suggest debounce. Whenever
// cancelPendingSuggest() invalidates the ticket that owns it (a name edit superseding an
// in-flight manual request, or a session change), loading ownership must be released
// immediately here rather than left for that request's own eventual `finally` to notice --
// once the ticket no longer matches `latestSuggestTicket`, that `finally`'s own check never
// fires, and without this the "제안" Button's loading→disabled contract would otherwise
// leave it permanently disabled (T#2 §9 stale response must not reverse the latest UI
// state).
let suggestingOwnerTicket: number | null = null

function cancelPendingSuggest(): void {
  if (suggestDebounce) {
    clearTimeout(suggestDebounce)
    suggestDebounce = null
  }
  suggestTicketSeq += 1
  latestSuggestTicket = suggestTicketSeq
  if (suggestingOwnerTicket !== null) {
    suggestingOwnerTicket = null
    suggesting.value = false
  }
}

function takeSuggestTicket(): number {
  suggestTicketSeq += 1
  latestSuggestTicket = suggestTicketSeq
  return latestSuggestTicket
}

watch(
  () => props.open,
  (isOpen) => {
    generation.value += 1
    // A close (or a reopen into a new session) must not let a debounce timer or an
    // already in-flight suggestion from the session now behind us apply to whatever
    // session comes next.
    cancelPendingSuggest()
    if (isOpen) resetForm()
  },
)

function onNameChange(value: string): void {
  name.value = value
}

// A direct id edit always wins over whatever suggestion request (auto or manual "제안")
// is still outstanding -- without cancelling it here first, that request's own ticket is
// still the latest one when it resolves, and its eventual response would overwrite the
// value just typed by hand (T#2 §9 최신 요청/상태 우선 계약).
function onIdChange(value: string): void {
  cancelPendingSuggest()
  idTouched.value = true
  id.value = value
}

// Auto-suggests an id from the name while the person has not typed into the id field
// themselves (v0.1 parity: client/src/settings/views/projects/ProjectsView.vue's
// `slugManuallyEdited` debounce). A failed suggestion is silently ignored -- typing the
// id manually always remains available.
//
// `cancelPendingSuggest()` runs before the `idTouched` check, not after it: a name edit
// must invalidate whatever request (debounce or manual "제안") is in flight for the
// *previous* name regardless of whether the id field happens to be touched right now --
// otherwise a manual "제안" started while id was already touched leaves its ticket alive
// across the name edit, and its stale response later applies the old name's slug to the
// new name (and leaves the "제안" Button's loading state stuck, T#2 §9).
watch(name, (value) => {
  cancelPendingSuggest()
  if (idTouched.value) return
  const text = value.trim()
  if (!text) {
    id.value = ''
    return
  }
  const myGeneration = generation.value
  suggestDebounce = setTimeout(() => {
    suggestDebounce = null
    if (idTouched.value) return
    const myTicket = takeSuggestTicket()
    suggestProjectSlug(text)
      .then((suggested) => {
        if (myGeneration !== generation.value) return
        if (myTicket !== latestSuggestTicket) return
        if (idTouched.value) return
        if (suggested) id.value = suggested
      })
      .catch(() => {})
  }, 400)
})

onBeforeUnmount(() => {
  if (suggestDebounce) clearTimeout(suggestDebounce)
})

async function suggestNow(): Promise<void> {
  const text = name.value.trim()
  if (!text) return
  cancelPendingSuggest()
  const myGeneration = generation.value
  const myTicket = takeSuggestTicket()
  suggestingOwnerTicket = myTicket
  suggesting.value = true
  try {
    const suggested = await suggestProjectSlug(text)
    if (myGeneration !== generation.value) return
    if (myTicket !== latestSuggestTicket) return
    if (suggested) {
      idTouched.value = false
      id.value = suggested
    }
  } catch {
    // Best-effort suggestion only; the person can still type an id manually.
  } finally {
    // A later cancelPendingSuggest() may already have released this ticket's ownership
    // (and reset `suggesting`) before this call even settles -- only clear it here if this
    // ticket still owns it.
    if (suggestingOwnerTicket === myTicket) {
      suggestingOwnerTicket = null
      suggesting.value = false
    }
  }
}

async function handleSubmit(): Promise<void> {
  attemptedSubmit.value = true
  if (!canSubmit.value) return
  if (submitting.value) return
  const myGeneration = generation.value
  submitting.value = true
  submitError.value = null
  try {
    const created = await store.createProject({
      name: name.value.trim(),
      id: id.value.trim() || undefined,
      description: description.value.trim() || undefined,
      color: color.value,
    })
    // This child Modal itself may have been closed and reopened (a new create session)
    // while this call was in flight -- that session's own state must never be reported
    // as if it belongs to this (now stale) one.
    if (myGeneration !== generation.value) return
    // `created` is null only when this request was superseded at the store level by a
    // close/reopen of the *parent* Dialog or a newer fetch/create (store.ts's
    // stale-response guard, T#1 §5) -- in that case the session that now owns the
    // Dialog has already moved on, so there is nothing left for this (stale) call to
    // report either.
    if (created) emit('created', created)
  } catch (err) {
    if (myGeneration !== generation.value) return
    submitError.value = err instanceof Error ? err.message : 'Failed to create project.'
  } finally {
    if (myGeneration === generation.value) submitting.value = false
  }
}

function requestClose(): void {
  if (isDirty.value) {
    confirmDiscardOpen.value = true
    return
  }
  emit('close')
}

function confirmDiscard(): void {
  confirmDiscardOpen.value = false
  emit('close')
}

function cancelDiscard(): void {
  confirmDiscardOpen.value = false
}
</script>

<style scoped>
.pcd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 20px 24px 0;
}

.pcd-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--fg2-text);
}

.pcd-alert {
  margin: 16px 0 0;
}

.pcd-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 0 4px;
}

.pcd-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pcd-label {
  font-size: 12px;
  font-weight: 650;
  color: var(--fg2-muted);
}

.pcd-optional {
  font-weight: 400;
  color: var(--fg2-muted-2);
}

.pcd-id-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.pcd-id-row > :first-child {
  flex: 1;
  min-width: 0;
}

.pcd-field-error {
  margin: 0;
  font-size: 11px;
  color: var(--fg2-status-danger-fg);
}

.pcd-colors {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pcd-color {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 2px solid transparent;
  display: grid;
  place-items: center;
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}

.pcd-color.is-selected {
  border-color: var(--fg2-text);
}

.pcd-color:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
}

.pcd-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 24px 20px;
}
</style>
