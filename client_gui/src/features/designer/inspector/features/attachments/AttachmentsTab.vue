<template>
  <!-- flowgate.v02.0048 T#8 — attachments tab (D#1 §10.2 T#8 row): the attachments of the selected Node's
       document — open (download), ＋ add, delete with a confirm step — with the reason when a change is
       disabled and the refusal the server answered. A thin container: useAttachments reads and changes,
       attachmentsModel decides the target document, the rows and availability. -->
  <div class="inspector-attachments" data-test="inspector-attachments" :data-status="status">
    <div class="inspector-attachments-bar">
      <input
        ref="picker"
        class="inspector-attachments-picker"
        type="file"
        multiple
        hidden
        data-test="attachments-picker"
        @change="onPick"
      />
      <Button
        size="sm"
        variant="secondary"
        :disabled="!availability.add.enabled"
        :loading="busy?.operation === 'add'"
        data-test="attachments-add"
        @click="pickFiles"
      >{{ t(k('add')) }}</Button>
      <span class="inspector-attachments-sub">{{ t(k('add_hint'), { size: limits.maxUploadMb }) }}</span>
    </div>
    <div v-if="availability.add.reason" data-test="attachments-reason">
      <InspectorReason :text="t(k(availability.add.reason), { limit: limits.maxCount })" />
    </div>
    <div v-if="failure && !rowFailure" data-test="attachments-failure">
      <InspectorReason tone="danger" :text="t(k(failure.key), failure.params)" />
    </div>

    <InspectorPaneState v-if="status === 'loading' && !rows" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !rows" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <template v-else-if="rows">
      <InspectorPaneState v-if="!rows.length" state="empty" :message="t(k('empty'))" />
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <ul v-if="rows.length" class="inspector-attachments-list">
        <li v-for="row in rows" :key="row.filename" class="inspector-attachments-item" data-test="attachments-item" :data-name="row.filename">
          <div class="inspector-attachments-line">
            <button
              class="inspector-attachments-name"
              type="button"
              :title="row.filename"
              :disabled="opening !== null"
              data-test="attachments-open"
              @click="open(row.filename)"
            >{{ row.filename }}</button>
            <span class="inspector-attachments-sub" data-test="attachments-size">{{ t(k(row.size.key), { value: row.size.value }) }}</span>
            <Button
              v-if="confirming !== row.filename"
              size="sm"
              variant="ghost"
              :disabled="!availability.delete.enabled"
              :loading="busy?.operation === 'delete' && busy.filename === row.filename"
              data-test="attachments-delete"
              @click="confirming = row.filename"
            >{{ t(k('delete')) }}</Button>
          </div>
          <span v-if="row.originalFilename" class="inspector-attachments-sub" data-test="attachments-original">{{ t(k('original'), { name: row.originalFilename }) }}</span>
          <div v-if="confirming === row.filename" class="inspector-attachments-line" data-test="attachments-confirm">
            <span class="inspector-attachments-sub">{{ t(k('delete_confirm'), { name: row.filename }) }}</span>
            <Button size="sm" variant="danger" :disabled="!availability.delete.enabled" data-test="attachments-delete-yes" @click="confirmDelete(row.filename)">{{ t(k('delete_yes')) }}</Button>
            <Button size="sm" variant="secondary" data-test="attachments-delete-no" @click="confirming = null">{{ t(k('delete_no')) }}</Button>
          </div>
          <div v-if="failure && rowFailure && failure.filename === row.filename" data-test="attachments-item-failure">
            <InspectorReason tone="danger" :text="t(k(failure.key), failure.params)" />
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useAttachments } from './useAttachments'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.attachments.${key}`

const { rows, availability, status, errorKey, busy, opening, failure, limits, add, remove, open, reload } = useAttachments(() => props.target)

const picker = ref<HTMLInputElement | null>(null)
// A refusal about one attachment shows on its row; an add refusal, or one whose row is gone after the
// read again (the file was deleted elsewhere), shows above the list.
const rowFailure = computed(() => {
  const current = failure.value
  return !!current && current.filename !== null && !!rows.value?.some((row) => row.filename === current.filename)
})

/** The row whose delete waits for the confirm step, or null. */
const confirming = ref<string | null>(null)

watch(
  () => props.target.docId,
  () => {
    confirming.value = null
  },
)

function pickFiles() {
  if (availability.value.add.enabled) picker.value?.click()
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (files.length) void add(files)
}

async function confirmDelete(filename: string) {
  confirming.value = null
  await remove(filename)
}
</script>

<style scoped>
.inspector-attachments {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.inspector-attachments-bar,
.inspector-attachments-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-attachments-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.inspector-attachments-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--fg2-line);
}

.inspector-attachments-name {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: none;
  color: var(--fg2-link, inherit);
  font: inherit;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.inspector-attachments-name:hover {
  text-decoration: underline;
}

.inspector-attachments-name:disabled {
  cursor: progress;
}

.inspector-attachments-sub {
  color: var(--fg2-muted);
  font-size: 11.5px;
}
</style>
