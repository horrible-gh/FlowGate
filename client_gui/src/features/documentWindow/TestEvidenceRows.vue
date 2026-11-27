<template>
  <!-- F81 v8: one fixed-height row per kind — name · count · the two newest files · [전체 N ▭] · ＋ 추가.
       20 files or 200, the rows keep their height; the whole list lives in the kind's evidence window. -->
  <div class="evidence" data-test="case-evidence">
    <div v-for="kind in testEvidenceKinds" :key="kind" class="row" :data-test="`case-evidence-${kind}`" @dragover.prevent @drop.prevent="drop($event, kind)">
      <b class="name">{{ words[kind] }}</b>
      <span v-if="count(kind)" class="count" :data-test="`case-evidence-count-${kind}`">{{ count(kind) }}</span><span v-else />
      <span class="recent">
        <span v-for="file in evidence.byKind.value[kind].slice(0, 2)" :key="file.key" class="chip" :title="file.filename" data-test="case-evidence-recent"><i>{{ file.filename }}</i></span>
      </span>
      <button v-if="count(kind)" type="button" class="all" :class="{ 'all--open': open.includes(kind) }" :aria-pressed="open.includes(kind)" :data-test="`case-evidence-all-${kind}`" @click="emit('toggle', kind)">
        {{ fill(words.all, { n: count(kind) }) }}<span class="window-mark" aria-hidden="true" /></button><span v-else />
      <label class="add" :class="{ 'add--off': disabled }"><input type="file" multiple :disabled="disabled" :data-test="`case-evidence-add-${kind}`" @change="choose($event, kind)" />{{ words.add }}</label>
    </div>
    <p v-if="evidence.failure.value" class="failure" role="alert" :title="evidence.failure.value" data-test="case-evidence-failure">{{ words.failed }}</p>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TestEvidenceKind } from '@/features/document'
import { testEvidenceKinds, type TestEvidence } from './testEvidenceModel'
const props = defineProps<{ evidence: TestEvidence; open: readonly TestEvidenceKind[] }>()
const emit = defineEmits<{ toggle: [kind: TestEvidenceKind] }>()
const { locale } = useI18n()
const dictionary = {
  ko: { input: '입력', log: '로그', output: '출력', all: '전체 {n}', add: '＋ 추가', addDrop: '＋ 추가 / 끌어 놓기', window: '증거 · {kind} {n}', search: '파일 이름 검색', delete: '삭제', previous: '{n}차 시도', close: '닫기', failed: '파일 처리 실패' },
  en: { input: 'Input', log: 'Log', output: 'Output', all: 'All {n}', add: '＋ Add', addDrop: '＋ Add / drop files', window: 'Evidence · {kind} {n}', search: 'Search file names', delete: 'Delete', previous: 'Attempt {n}', close: 'Close', failed: 'File operation failed' },
  ja: { input: '入力', log: 'ログ', output: '出力', all: 'すべて {n}', add: '＋ 追加', addDrop: '＋ 追加 / ドロップ', window: '証拠 · {kind} {n}', search: 'ファイル名を検索', delete: '削除', previous: '{n}回目の試行', close: '閉じる', failed: 'ファイル処理に失敗' },
}
const words = computed(() => dictionary[locale.value.split('-')[0] as keyof typeof dictionary] ?? dictionary.en)
const fill = (text: string, values: Record<string, string | number>) => text.replace(/\{(\w+)\}/g, (_, name: string) => String(values[name] ?? ''))
const disabled = computed(() => props.evidence.busy.value || !props.evidence.ready.value)
const count = (kind: TestEvidenceKind) => props.evidence.byKind.value[kind].length
function choose(event: Event, kind: TestEvidenceKind) { const input = event.target as HTMLInputElement; void props.evidence.upload(Array.from(input.files ?? []), kind); input.value = '' }
function drop(event: DragEvent, kind: TestEvidenceKind) { void props.evidence.upload(Array.from(event.dataTransfer?.files ?? []), kind) }
</script>
<style scoped>
.evidence { position: relative; width: 100%; border: 1px solid var(--fg2-line); border-radius: 8px; box-sizing: border-box; overflow: hidden; }
.row { display: grid; grid-template-columns: 44px 34px minmax(0, 1fr) auto auto; gap: 6px; align-items: center; height: 36px; padding: 0 8px; box-sizing: border-box; }
.row + .row { border-top: 1px solid var(--fg2-line); }
.name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.count { padding: 0 7px; border-radius: 9px; background: rgba(59, 130, 246, 0.22); color: #cfe1ff; font-size: 11px; line-height: 18px; text-align: center; }
.recent { display: flex; gap: 5px; min-width: 0; overflow: hidden; }
.chip { display: flex; align-items: center; min-width: 0; max-width: 50%; height: 24px; padding: 1px 6px; border: 1px solid var(--fg2-line); border-radius: 4px; box-sizing: border-box; font-size: 12px; }
.chip i { overflow: hidden; font-style: normal; text-overflow: ellipsis; white-space: nowrap; }
/* The same "a window opens" look as the edit buttons: name, then a small window mark; lit while its window is open. */
.all { display: inline-flex; align-items: center; gap: 5px; padding: 2px 7px; border: 1px solid #35507a; border-radius: 5px; background: #15253c; color: #cfe1ff; font: inherit; font-size: 12px; white-space: nowrap; cursor: pointer; }
.all:hover, .all--open { border-color: var(--fg2-accent, #3b82f6); background: #1b3152; }
.window-mark { display: inline-block; width: 12px; height: 10px; border: 1.5px solid #7fb0ff; border-top-width: 3px; border-radius: 2px; box-sizing: border-box; }
.all--open .window-mark { background: rgba(127, 176, 255, 0.33); }
.add { padding-left: 4px; color: #7fb0ff; font-size: 12px; white-space: nowrap; cursor: pointer; }
.add input { display: none; } .add--off { opacity: 0.5; cursor: default; }
.failure { margin: 0; padding: 2px 8px 4px; overflow: hidden; color: var(--fg2-status-danger-fg); font-size: 11.5px; white-space: nowrap; text-overflow: ellipsis; }
</style>
