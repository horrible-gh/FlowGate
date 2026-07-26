<template>
  <div class="file-diff">
    <div v-if="loading" class="file-diff__state">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="file-diff__state file-diff__state--error">
      <span>{{ t('main.file_diff.load_failed') }}</span>
      <button class="btn btn-secondary btn-sm" type="button" @click="load">
        {{ t('main.explorer.retry') }}
      </button>
    </div>
    <template v-else>
      <div class="file-diff__bar">
        <span class="file-diff__status" :class="`file-diff__status--${status}`">{{ statusLabel }}</span>
        <span class="file-diff__path" :title="path">{{ path }}</span>
        <span class="file-diff__stats">
          <span class="file-diff__stat file-diff__stat--add">+{{ stats.added + stats.changed }}</span>
          <span class="file-diff__stat file-diff__stat--del">-{{ stats.removed + stats.changed }}</span>
        </span>
        <span class="file-diff__spacer"></span>
        <div class="file-diff__toggles">
          <button
            class="file-diff__toggle"
            type="button"
            :aria-pressed="viewMode === 'split'"
            :class="{ active: viewMode === 'split' }"
            @click="viewMode = 'split'"
          >{{ t('main.file_diff.view_split') }}</button>
          <button
            class="file-diff__toggle"
            type="button"
            :aria-pressed="viewMode === 'unified'"
            :class="{ active: viewMode === 'unified' }"
            @click="viewMode = 'unified'"
          >{{ t('main.file_diff.view_unified') }}</button>
        </div>
        <button
          class="file-diff__toggle"
          type="button"
          :aria-pressed="!collapseUnchanged"
          :class="{ active: !collapseUnchanged }"
          @click="collapseUnchanged = !collapseUnchanged"
        >{{ t('main.file_diff.show_all') }}</button>
        <button
          class="file-diff__toggle"
          type="button"
          :title="t('main.explorer.retry')"
          @click="load"
        ><AppIcon name="arrow-clockwise" /></button>
      </div>

      <div v-if="notice" class="file-diff__notice">{{ notice }}</div>

      <div v-if="binary" class="file-diff__state">{{ t('main.file_diff.binary') }}</div>
      <div v-else-if="!hasChanges" class="file-diff__state">{{ t('main.file_diff.no_changes') }}</div>
      <div v-else class="file-diff__body">
        <div class="file-diff__labels" :class="`file-diff__labels--${viewMode}`">
          <span>{{ oldLabel }}</span>
          <span>{{ newLabel }}</span>
        </div>

        <!-- Split: one grid row per aligned diff row, so both sides stay lined up. -->
        <div v-if="viewMode === 'split'" class="file-diff__grid file-diff__grid--split">
          <template v-for="(section, sIdx) in sections" :key="`s${sIdx}`">
            <div v-if="section.kind === 'gap'" class="file-diff__gap">
              {{ t('main.file_diff.skipped_lines', { n: section.count }) }}
            </div>
            <template v-else>
              <div
                v-for="(row, rIdx) in section.rows"
                :key="`s${sIdx}r${rIdx}`"
                class="file-diff__row"
              >
                <span class="file-diff__num">{{ row.leftNumber ?? '' }}</span>
                <span class="file-diff__code" :class="sideClass(row.left ? row.status : null, 'left')">
                  <template v-if="row.left">
                    <span
                      v-for="(token, tIdx) in row.left.tokens"
                      :key="tIdx"
                      :class="`diff-token-${token.status}`"
                    >{{ token.text }}</span>
                  </template>
                </span>
                <span class="file-diff__num">{{ row.rightNumber ?? '' }}</span>
                <span class="file-diff__code" :class="sideClass(row.right ? row.status : null, 'right')">
                  <template v-if="row.right">
                    <span
                      v-for="(token, tIdx) in row.right.tokens"
                      :key="tIdx"
                      :class="`diff-token-${token.status}`"
                    >{{ token.text }}</span>
                  </template>
                </span>
              </div>
            </template>
          </template>
        </div>

        <!-- Unified: the same rows flattened into patch order (old line, then new). -->
        <div v-else class="file-diff__grid file-diff__grid--unified">
          <template v-for="(section, sIdx) in unifiedSections" :key="`u${sIdx}`">
            <div v-if="section.kind === 'gap'" class="file-diff__gap">
              {{ t('main.file_diff.skipped_lines', { n: section.count }) }}
            </div>
            <template v-else>
              <div
                v-for="(row, rIdx) in section.rows"
                :key="`u${sIdx}r${rIdx}`"
                class="file-diff__row"
              >
                <span class="file-diff__num">{{ row.leftNumber ?? '' }}</span>
                <span class="file-diff__num">{{ row.rightNumber ?? '' }}</span>
                <span class="file-diff__sign" :class="`diff-${row.status}`">{{ row.sign }}</span>
                <span class="file-diff__code" :class="`diff-${row.status}`">
                  <span
                    v-for="(token, tIdx) in row.line.tokens"
                    :key="tIdx"
                    :class="`diff-token-${token.status}`"
                  >{{ token.text }}</span>
                </span>
              </div>
            </template>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
// flowgate.default.0326 R0001 / N0004 — read-only change view for one file, opened
// from the file tree's "변경 내용 보기". The server returns the two versions
// (NR0005 §4 안 b) and the line/token diff is computed here with the engine the
// merge-conflict resolver already uses (NR0005 §5), so no new diff algorithm and no
// server-side patch parsing were introduced.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import {
  buildDiffRows,
  collapseCommonRows,
  diffStats,
  splitTextLines,
  toUnifiedRows,
  type DiffRow,
  type DiffSection,
  type UnifiedRow,
} from '../composables/useFileDiff'

const props = defineProps<{
  path: string
  projectId: string | null | undefined
  // Set for the read-only group-branch explorer mode; the diff is then measured
  // against the group's merge base instead of the base checkout's HEAD.
  gitGroupId?: string | null
  gitCommit?: string | null
}>()

const { t } = useI18n()

interface DiffSide {
  exists: boolean
  binary: boolean
  truncated: boolean
  size: number
  content: string | null
}

const loading = ref(false)
const error = ref(false)
const status = ref<'M' | 'A' | 'D'>('M')
const oldSide = ref<DiffSide | null>(null)
const newSide = ref<DiffSide | null>(null)
const viewMode = ref<'split' | 'unified'>('split')
const collapseUnchanged = ref(true)

const binary = computed(() => !!oldSide.value?.binary || !!newSide.value?.binary)
const truncated = computed(() => !!oldSide.value?.truncated || !!newSide.value?.truncated)

const diff = computed(() => {
  if (binary.value) return { rows: [] as DiffRow[], approximate: false }
  return buildDiffRows(
    splitTextLines(oldSide.value?.content ?? ''),
    splitTextLines(newSide.value?.content ?? ''),
  )
})
const stats = computed(() => diffStats(diff.value.rows))
const hasChanges = computed(
  () => stats.value.added + stats.value.removed + stats.value.changed > 0,
)

const sections = computed<DiffSection[]>(() =>
  collapseUnchanged.value
    ? collapseCommonRows(diff.value.rows)
    : [{ kind: 'rows', rows: diff.value.rows }],
)
const unifiedSections = computed(() =>
  sections.value.map((section) =>
    section.kind === 'gap'
      ? section
      : { kind: 'rows' as const, rows: toUnifiedRows(section.rows) as UnifiedRow[] },
  ),
)

const statusLabel = computed(() => {
  if (status.value === 'A') return t('main.file_diff.status_added')
  if (status.value === 'D') return t('main.file_diff.status_deleted')
  return t('main.file_diff.status_modified')
})
const oldLabel = computed(() =>
  props.gitGroupId ? t('main.file_diff.side_old_group') : t('main.file_diff.side_old_base'),
)
const newLabel = computed(() =>
  props.gitGroupId ? t('main.file_diff.side_new_group') : t('main.file_diff.side_new_base'),
)
const notice = computed(() => {
  if (diff.value.approximate) return t('main.file_diff.approximate')
  if (truncated.value) return t('main.file_diff.truncated')
  return ''
})

function sideClass(rowStatus: string | null, side: 'left' | 'right'): string {
  if (!rowStatus) return 'file-diff__code--empty'
  if (rowStatus === 'common') return 'diff-common'
  if (rowStatus === 'changed') return 'diff-changed'
  return side === 'left' ? 'diff-removed' : 'diff-added'
}

async function load() {
  if (!props.path || !props.projectId) return
  loading.value = true
  error.value = false
  try {
    const base = `/api/v1/projects/${encodeURIComponent(props.projectId)}/git`
    const url = props.gitGroupId
      ? `${base}/groups/${encodeURIComponent(props.gitGroupId)}/diff`
      : `${base}/diff`
    const params: Record<string, string> = { path: props.path }
    // Pin the group read to the tree snapshot the file was opened from, exactly
    // as the blob viewer does, so tree and diff cannot drift apart.
    if (props.gitGroupId && props.gitCommit) params.ref = props.gitCommit
    const res = await api.get(url, { params })
    const data = res.data?.data ?? {}
    status.value = (data.status as 'M' | 'A' | 'D') ?? 'M'
    oldSide.value = data.old ?? null
    newSide.value = data.new ?? null
  } catch {
    error.value = true
    oldSide.value = null
    newSide.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.path, props.projectId, props.gitGroupId, props.gitCommit],
  load,
  { immediate: true },
)

defineExpose({ load })
</script>

<style scoped>
.file-diff {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.file-diff__state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px;
  font-size: 0.8rem;
  opacity: 0.7;
}
.file-diff__state--error {
  color: var(--danger, #dc2626);
  opacity: 1;
}
.file-diff__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  font-size: 0.75rem;
  flex-wrap: wrap;
}
.file-diff__spacer {
  flex: 1 1 auto;
}
.file-diff__status {
  flex: none;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 700;
  font-size: 0.68rem;
}
.file-diff__status--M {
  color: #92400e;
  background: #fef3c7;
}
.file-diff__status--A {
  color: #047857;
  background: #d1fae5;
}
.file-diff__status--D {
  color: #9f1239;
  background: #ffe4e6;
}
.file-diff__path {
  color: var(--text-m, #64748b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 40ch;
}
.file-diff__stats {
  display: inline-flex;
  gap: 6px;
  font-family: 'JetBrains Mono', monospace;
}
.file-diff__stat--add {
  color: #047857;
}
.file-diff__stat--del {
  color: #9f1239;
}
.file-diff__toggles {
  display: inline-flex;
}
.file-diff__toggle {
  padding: 3px 8px;
  border: 1px solid var(--border, #cbd5e1);
  background: #fff;
  color: var(--text-m, #475569);
  font-size: 0.7rem;
  cursor: pointer;
  border-radius: 4px;
  margin-left: 4px;
}
.file-diff__toggles .file-diff__toggle {
  margin-left: 0;
  border-radius: 0;
}
.file-diff__toggles .file-diff__toggle:first-child {
  border-radius: 4px 0 0 4px;
}
.file-diff__toggles .file-diff__toggle:last-child {
  border-radius: 0 4px 4px 0;
  border-left: none;
}
.file-diff__toggle.active {
  background: var(--primary-weak, #eff6ff);
  border-color: var(--primary, #3b82f6);
  color: var(--primary, #2563eb);
}
.file-diff__notice {
  padding: 6px 12px;
  font-size: 0.72rem;
  color: #92400e;
  background: #fef3c7;
}
.file-diff__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}
.file-diff__labels {
  display: grid;
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 4px 0;
  background: var(--bg-weak, #f8fafc);
  border-bottom: 1px solid var(--border, #e2e8f0);
  font-size: 0.68rem;
  color: var(--text-m, #64748b);
}
.file-diff__labels--split {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}
.file-diff__labels--unified {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}
.file-diff__labels span {
  padding-left: 12px;
}
.file-diff__grid {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.76rem;
  line-height: 1.6;
}
.file-diff__row {
  display: grid;
  min-height: 1.6em;
}
.file-diff__grid--split .file-diff__row {
  grid-template-columns: 3.2rem minmax(0, 1fr) 3.2rem minmax(0, 1fr);
}
.file-diff__grid--unified .file-diff__row {
  grid-template-columns: 3.2rem 3.2rem 1.2rem minmax(0, 1fr);
}
.file-diff__num {
  padding: 0 8px 0 4px;
  text-align: right;
  color: #94a3b8;
  user-select: none;
  border-right: 1px solid var(--border, #e2e8f0);
}
.file-diff__sign {
  text-align: center;
  user-select: none;
  color: #64748b;
}
.file-diff__code {
  min-width: 0;
  padding: 0 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.file-diff__code--empty {
  background: #f8fafc;
}
.file-diff__gap {
  padding: 3px 12px;
  color: #64748b;
  background: #f1f5f9;
  border-top: 1px solid var(--border, #e2e8f0);
  border-bottom: 1px solid var(--border, #e2e8f0);
  font-size: 0.7rem;
}
/* Same palette as the merge-conflict resolver's diff so the two read alike. */
.diff-removed {
  background: #fff1f2;
}
.diff-added {
  background: #ecfdf5;
}
.diff-changed {
  background: #fff7ed;
}
.diff-common {
  background: transparent;
}
.diff-token-removed,
.diff-token-added,
.diff-token-changed {
  border-radius: 3px;
  padding: 0 1px;
}
.diff-token-removed {
  color: #9f1239;
  background: #ffe4e6;
}
.diff-token-added {
  color: #047857;
  background: #bbf7d0;
}
.diff-token-changed {
  color: #9a3412;
  background: #fed7aa;
}
</style>
