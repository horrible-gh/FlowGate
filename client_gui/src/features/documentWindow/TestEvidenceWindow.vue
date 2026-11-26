<template>
  <!-- F81 v8: one evidence kind's modeless window — the whole list, a file-name search, one-by-one delete and add.
       The list scrolls here only, so the case window keeps its height whatever the count. -->
  <ModelessShell ref="shell" kind-color="#3b82f6" :open="true" :title="title" :initial-x="x" :initial-y="y" :initial-width="width ?? 420" :initial-height="460"
    :close-on-escape="false" @close="emit('close')">
    <template #header><span class="heading" :data-test="`case-evidence-window-${kind}`"><span class="heading-kind">{{ title }}</span><span class="heading-case">{{ caseId }}</span></span></template>
    <div class="evidence-window" @dragover.prevent @drop.prevent="drop">
      <Input v-model="query" :placeholder="words.search" data-test="case-evidence-search" />
      <!-- This attempt's files first; the earlier attempts' files are folded under 「이전 시도 (n)」 as the old evidence box did (R §3-5). -->
      <div class="list" data-test="case-evidence-list">
        <ul class="files">
          <li v-for="file in current" :key="file.key" class="file" :data-key="file.key" data-test="case-evidence-file">
            <div class="line">
              <button type="button" class="name" :title="file.filename" @click="evidence.download(file)"><i>{{ file.filename }}</i> ↓</button>
              <button type="button" class="delete" :disabled="disabled" :title="words.delete" :aria-label="`${words.delete} ${file.filename}`" data-test="case-evidence-delete" @click="evidence.remove(file)">✕</button>
            </div>
            <img v-if="evidence.previews[file.key]?.kind === 'image' && evidence.previews[file.key].src" :src="evidence.previews[file.key].src" :alt="file.filename" />
            <pre v-else-if="evidence.previews[file.key]?.kind === 'text'">{{ evidence.previews[file.key].text }}</pre>
          </li>
        </ul>
        <details v-if="older.length" class="previous" data-test="case-evidence-previous">
          <summary data-test="case-evidence-previous-summary">{{ words.previousGroup }} ({{ older.length }})</summary>
          <ul class="files">
            <li v-for="file in older" :key="file.key" class="file" :data-key="file.key" data-test="case-evidence-file">
              <div class="line">
                <button type="button" class="name" :title="file.filename" @click="evidence.download(file)"><i>{{ file.filename }}</i> ↓</button>
                <span class="attempt">{{ fill(words.previous, { n: file.test_evidence.attempt }) }}</span>
                <button type="button" class="delete" :disabled="disabled" :title="words.delete" :aria-label="`${words.delete} ${file.filename}`" data-test="case-evidence-delete" @click="evidence.remove(file)">✕</button>
              </div>
              <img v-if="evidence.previews[file.key]?.kind === 'image' && evidence.previews[file.key].src" :src="evidence.previews[file.key].src" :alt="file.filename" />
              <pre v-else-if="evidence.previews[file.key]?.kind === 'text'">{{ evidence.previews[file.key].text }}</pre>
            </li>
          </ul>
        </details>
      </div>
      <p v-if="evidence.failure.value" class="failure" role="alert">{{ words.failed }}: {{ evidence.failure.value }}</p>
      <div class="bar">
        <label class="add" :class="{ 'add--off': disabled }"><input type="file" multiple :disabled="disabled" data-test="case-evidence-window-add" @change="choose" />{{ words.addDrop }}</label>
        <Button size="sm" variant="secondary" data-test="case-evidence-window-close" @click="emit('close')">{{ words.close }}</Button>
      </div>
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import type { TestEvidenceKind } from '@/features/document'
import type { TestEvidence } from './testEvidenceModel'
const props = defineProps<{ evidence: TestEvidence; kind: TestEvidenceKind; caseId: string; x: number; y: number; width?: number }>()
const emit = defineEmits<{ close: [] }>()
const { locale } = useI18n()
const dictionary = {
  ko: { input: '입력', log: '로그', output: '출력', all: '전체 {n}', add: '＋ 추가', addDrop: '＋ 추가 / 끌어 놓기', window: '증거 · {kind} {n}', search: '파일 이름 검색', delete: '삭제', previous: '{n}차 시도', previousGroup: '이전 시도', close: '닫기', failed: '파일 처리 실패' },
  en: { input: 'Input', log: 'Log', output: 'Output', all: 'All {n}', add: '＋ Add', addDrop: '＋ Add / drop files', window: 'Evidence · {kind} {n}', search: 'Search file names', delete: 'Delete', previous: 'Attempt {n}', previousGroup: 'Previous attempts', close: 'Close', failed: 'File operation failed' },
  ja: { input: '入力', log: 'ログ', output: '出力', all: 'すべて {n}', add: '＋ 追加', addDrop: '＋ 追加 / ドロップ', window: '証拠 · {kind} {n}', search: 'ファイル名を検索', delete: '削除', previous: '{n}回目の試行', previousGroup: '以前の試行', close: '閉じる', failed: 'ファイル処理に失敗' },
}
const words = computed(() => dictionary[locale.value.split('-')[0] as keyof typeof dictionary] ?? dictionary.en)
const fill = (text: string, values: Record<string, string | number>) => text.replace(/\{(\w+)\}/g, (_, name: string) => String(values[name] ?? ''))
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const query = ref('')
const all = computed(() => props.evidence.byKind.value[props.kind])
const shown = computed(() => { const q = query.value.trim().toLowerCase(); return q ? all.value.filter(file => file.filename.toLowerCase().includes(q)) : all.value })
const current = computed(() => shown.value.filter(file => !props.evidence.previous(file)))
const older = computed(() => shown.value.filter(file => props.evidence.previous(file)))
const title = computed(() => fill(words.value.window, { kind: words.value[props.kind], n: all.value.length }))
const disabled = computed(() => props.evidence.busy.value || !props.evidence.ready.value)
onMounted(() => { void props.evidence.loadPreviews(props.kind); shell.value?.bringToFront() })
onBeforeUnmount(() => props.evidence.forgetPreviews(props.kind))
function choose(event: Event) { const input = event.target as HTMLInputElement; void props.evidence.upload(Array.from(input.files ?? []), props.kind); input.value = '' }
function drop(event: DragEvent) { void props.evidence.upload(Array.from(event.dataTransfer?.files ?? []), props.kind) }
</script>
<style scoped>
.evidence-window { display: flex; flex-direction: column; gap: 8px; height: 100%; min-height: 0; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
.list { flex: 1; min-height: 0; overflow: auto; border: 1px solid var(--fg2-line); border-radius: 6px; }
.files { margin: 0; padding: 0; list-style: none; }
.previous > summary { padding: 4px 9px; color: var(--fg2-muted); font-size: 12px; cursor: pointer; }
.file { padding: 4px 9px; border-bottom: 1px solid var(--fg2-line); font-size: 12px; }
.line { display: flex; align-items: center; gap: 8px; min-width: 0; }
.name { display: flex; flex: 1; gap: 4px; min-width: 0; padding: 0; border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.name i { overflow: hidden; font-style: normal; text-overflow: ellipsis; white-space: nowrap; }
.attempt { color: var(--fg2-muted); font-size: 11px; white-space: nowrap; }
.delete { padding: 0 4px; border: 0; background: none; color: var(--fg2-muted); font: inherit; cursor: pointer; } .delete:hover:not(:disabled) { color: #ff9eae; } .delete:disabled { cursor: default; opacity: 0.5; }
img { display: block; max-width: 200px; max-height: 120px; margin-top: 4px; object-fit: contain; }
pre { max-height: 120px; margin: 4px 0 0; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--fg2-muted); font-size: 11px; }
.failure { margin: 0; color: var(--fg2-status-danger-fg); font-size: 11.5px; }
.bar { display: flex; flex: none; align-items: center; justify-content: flex-end; gap: 8px; padding-top: 8px; border-top: 1px solid var(--fg2-line); }
.add { margin-right: auto; color: #7fb0ff; font-size: 12px; cursor: pointer; } .add input { display: none; } .add--off { opacity: 0.5; cursor: default; }
.heading { display: inline-flex; align-items: center; gap: 8px; min-width: 0; max-width: 100%; } .heading-kind { flex: 0 0 auto; }
.heading-case { overflow: hidden; color: var(--fg2-muted); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
</style>
