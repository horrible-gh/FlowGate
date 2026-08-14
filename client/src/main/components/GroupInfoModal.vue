<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="close">
      <div class="modal-box" style="width:560px;max-width:94vw;">
        <div class="modal-hd">
          <span class="modal-title gi-title">
            <AppIcon name="info" />
            {{ t('main.group_actions.info_title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
          <div class="gi-id-row">
            <span class="gi-id-badge">{{ groupId }}</span>
          </div>
          <div class="gi-grid">
            <div>
              <label>{{ t('main.group_actions.info_group_name') }}</label>
              <span>{{ groupName || '—' }}</span>
            </div>
            <div>
              <label>{{ t('main.group_actions.info_doc_count') }}</label>
              <span>{{ t('main.group_actions.info_doc_count_value', { count: documents.length }) }}</span>
            </div>
          </div>
          <div class="gi-subtitle">{{ t('main.group_actions.info_included_docs') }}</div>
          <div v-if="documents.length" class="gi-doc-list">
            <div v-for="d in documents" :key="d.id" class="gi-doc-row">
              <span class="doc-tag" :class="`c-${d.typeCode}`">{{ d.typeCode }}</span>
              <span class="gi-doc-id">{{ d.shortId }}</span>
              <span class="gi-doc-name">{{ d.title }}</span>
              <span
                class="gi-doc-ai"
                :class="{ 'is-unknown': isAiUnknown(d) }"
                :title="aiBadgeTitle(d)"
              >{{ aiBadgeLabel(d) }}</span>
            </div>
          </div>
          <p v-else class="gi-empty">{{ t('main.group_actions.info_empty') }}</p>
        </div>
        <div class="modal-ft">
          <button type="button" class="btn btn-outline gi-foot-left" @click="emit('rename')">
            <AppIcon name="pencil-simple" />
            {{ t('main.group_actions.rename_group') }}
          </button>
          <button type="button" class="btn btn-secondary" @click="close">
            {{ t('common.close') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { useI18n } from 'vue-i18n'

export interface GroupInfoDoc {
  id: string
  typeCode: string
  shortId: string
  title: string
  originProviderName?: string | null
  originAiRunId?: string | null
}

defineProps<{
  visible: boolean
  groupId: string
  groupName: string
  documents: GroupInfoDoc[]
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  rename: []
}>()

const { t } = useI18n()

function close() {
  emit('update:visible', false)
}

// origin_provider_name is a nullable snapshot taken at document-creation time (NR0003 /
// WP0005) — it is never re-looked-up, so an empty/whitespace-only value is treated the
// same as null: an incomplete row must not be guessed into a provider name.
function isAiUnknown(d: GroupInfoDoc): boolean {
  return !(d.originProviderName ?? '').trim()
}

function aiBadgeLabel(d: GroupInfoDoc): string {
  const name = (d.originProviderName ?? '').trim()
  return name
    ? t('main.group_actions.info_doc_author_ai', { provider: name })
    : t('main.group_actions.info_doc_author_unknown')
}

// The run id rides along in the accessible title even on an otherwise-unknown row
// (provider name missing but run id present), instead of being dropped.
function aiBadgeTitle(d: GroupInfoDoc): string | undefined {
  const runId = (d.originAiRunId ?? '').trim()
  return runId ? t('main.group_actions.info_doc_author_run_id', { runId }) : undefined
}
</script>

<style scoped>
.gi-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--primary);
}
.gi-id-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.gi-id-badge {
  padding: 3px 9px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface-h);
  color: var(--text-m);
  font-family: 'JetBrains Mono', monospace;
  font-size: .74rem;
}
.gi-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 18px;
  margin-bottom: 16px;
}
.gi-grid > div { display: flex; flex-direction: column; gap: 3px; }
.gi-grid label {
  color: var(--text-m);
  font-size: .64rem;
  font-weight: 700;
  letter-spacing: .04em;
}
.gi-grid span { color: var(--text); font-size: .82rem; }
.gi-subtitle {
  margin: 4px 0 8px;
  color: var(--text-m);
  font-size: .63rem;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.gi-doc-list { display: flex; flex-direction: column; gap: 6px; }
.gi-doc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg);
  color: var(--text-s);
  font-size: .78rem;
}
.gi-doc-id {
  flex-shrink: 0;
  color: var(--text-m);
  font-family: 'JetBrains Mono', monospace;
  font-size: .72rem;
}
.gi-doc-name {
  flex: 1;
  min-width: 0;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gi-doc-ai {
  flex-shrink: 0;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: .68rem;
  font-weight: 700;
  border: 1px solid var(--primary);
  color: var(--primary);
  background: var(--surface-h);
}
.gi-doc-ai.is-unknown {
  border: 1px dashed var(--border-d);
  color: var(--text-m);
  background: transparent;
  font-weight: 500;
}
.gi-empty {
  margin: 0;
  color: var(--text-m);
  font-size: .78rem;
}
.gi-foot-left { margin-right: auto; }
</style>
