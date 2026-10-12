<template>
  <div class="ts-spec" data-test="test-spec-panel">
    <div class="ts-summary" data-test="test-spec-summary">
      <span class="ts-count" data-test="test-spec-count">{{ t(k('test.cases'), { n: doc.cases.length }) }}</span>
      <span class="ts-count">{{ t(k('test.required_count'), { n: requiredCount }) }}</span>
      <TestMarkBadge
        v-if="hasResult && !running"
        :mark="testVerdictMark(doc.overall)"
        :tooltip="`${t(k('test.overall'))}: ${t(k(testVerdictMark(doc.overall).key))}`"
        data-test="test-spec-overall"
      />
      <TestMarkBadge
        v-for="flag in flags"
        :key="flag.mark.key"
        :mark="flag.mark"
        :text="flag.text"
        :tooltip="t(k(flag.mark.key), flag.params)"
      />
    </div>

    <p v-if="doc.intro" class="ts-intro" data-test="test-spec-intro">{{ doc.intro }}</p>

    <details v-for="item in doc.cases" :key="item.caseId" class="ts-case" data-test="test-spec-case" :data-case-id="item.caseId">
      <summary class="ts-case-hd">
        <TestMarkBadge v-if="pending.has(item.caseId)" :mark="TEST_PENDING_MARK" data-test="test-spec-case-pending" />
        <TestMarkBadge v-else-if="results.get(item.caseId)" :mark="testVerdictMark(results.get(item.caseId)?.status)" data-test="test-spec-case-verdict" />
        <span class="ts-case-id">{{ item.caseId }}</span>
        <span class="ts-case-title">{{ item.title }}</span>
        <span v-if="testCategoryKey(item.category)" class="ts-chip">{{ t(k(testCategoryKey(item.category)!)) }}</span>
        <TestMarkBadge v-if="testModeMark(item.executionMode)" :mark="testModeMark(item.executionMode)!" />
        <TestMarkBadge :mark="item.required ? TEST_REQUIRED_MARK : TEST_OPTIONAL_MARK" />
      </summary>
      <dl class="ts-fields">
        <template v-for="field in TEST_CASE_FIELDS" :key="field">
          <template v-if="item.fields[field]">
            <dt>{{ t(k(`test.field.${field}`)) }}</dt>
            <dd>{{ item.fields[field] }}</dd>
          </template>
        </template>
      </dl>
    </details>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0053 T#4 — the test scenario's view: the v0.1 TestSpecPanel
// (client/src/main/components/documents/TestSpecPanel.vue) read only. Kept: the Case count and required
// count, the latest verdict, the intro, one block per Case (id, title, category, execution mode, required,
// its result or "pending" while a run is live) and the Case fields in the v0.1 order. Not carried over: the
// spec editor, result entry, per-Case run and the test asset editor (the window's ✎ edits the document text),
// the Basis facts (fingerprints, revisions, hashes) and the explanation bands — a stale or unchecked result,
// a live run and format errors are marks with the words in their tooltips.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TestSpecDocument } from '@/features/document'
import TestMarkBadge from './TestMarkBadge.vue'
import {
  TEST_CASE_FIELDS,
  TEST_OPTIONAL_MARK,
  TEST_PENDING_MARK,
  TEST_REQUIRED_MARK,
  testCategoryKey,
  testDocumentFlags,
  testModeMark,
  testResultByCase,
  testVerdictMark,
} from './specialDocumentModel'

const props = defineProps<{
  doc: TestSpecDocument
}>()

const { t } = useI18n()
const k = (key: string) => `gui.document_window.${key}`

const requiredCount = computed(() => props.doc.cases.filter((item) => item.required).length)
const results = computed(() => testResultByCase(props.doc.rows))
const pending = computed(() => new Set(props.doc.pendingCaseIds))
const hasResult = computed(() => props.doc.rows.length > 0 || props.doc.overall !== null)
// While a run is live its progress leads; the previous verdict is not shown as this run's.
const running = computed(() => props.doc.view.activeRun !== null)
const flags = computed(() => testDocumentFlags(props.doc))
</script>

<style scoped>
.ts-spec {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 18px;
  color: var(--fg2-text);
  font-size: 12.5px;
}

.ts-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.ts-count {
  color: var(--fg2-muted);
}

.ts-intro {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.ts-case {
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
}

.ts-case-hd {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
}

.ts-case-id {
  color: var(--fg2-muted);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11.5px;
}

.ts-case-title {
  flex: 1 1 auto;
  min-width: 0;
  font-weight: 600;
}

.ts-chip {
  padding: 1px 6px;
  border: 1px solid var(--fg2-line);
  border-radius: 9px;
  color: var(--fg2-muted);
  font-size: 11px;
}

.ts-fields {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 14px;
  margin: 0;
  padding: 4px 12px 10px;
}

.ts-fields dt {
  color: var(--fg2-muted);
}

.ts-fields dd {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
