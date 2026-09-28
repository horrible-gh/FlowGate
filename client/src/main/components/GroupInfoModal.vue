<template>
  <DialogShell :open="visible" variant="readonly" surface-class="dialog-group-info-modal"  @request-close="close">
    <template #header>
      <DialogHeader title="" :closeable="true" @close="close">
        <template #title>
            <AppIcon name="info" />
            {{ t('main.group_actions.info_title') }}
          </template>
      </DialogHeader>
    </template>

        
        <div class="dialog-feature-body">
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
          <section class="gi-bundles" data-test="source-bundle-observability" :aria-label="t('main.group_actions.bundles.title')">
            <h3>{{ t('main.group_actions.bundles.title') }}</h3>
            <p v-if="bundleError">{{ t('main.group_actions.bundles.load_failed') }}</p>
            <p v-else-if="bundles.length === 0">{{ t('main.group_actions.bundles.empty') }}</p>
            <article v-for="bundle in bundles" :key="bundle.bundle_id" class="gi-bundle">
              <strong>{{ bundle.bundle_id }}</strong>
              <dl>
                <dt>{{ t('main.group_actions.bundles.status') }}</dt><dd>{{ bundle.status }}</dd>
                <dt>{{ t('main.group_actions.bundles.revision') }}</dt><dd>{{ bundle.source_revision || '—' }}</dd>
                <dt>{{ t('main.group_actions.bundles.dirty') }}</dt><dd>{{ bundle.source_dirty }}</dd>
                <dt>{{ t('main.group_actions.bundles.created') }}</dt><dd>{{ bundle.created_at || '—' }}</dd>
                <dt>{{ t('main.group_actions.bundles.expires') }}</dt><dd>{{ bundle.expires_at || '—' }}</dd>
                <dt>{{ t('main.group_actions.bundles.files') }}</dt><dd>{{ bundle.file_count ?? '—' }}</dd>
                <dt>{{ t('main.group_actions.bundles.bytes') }}</dt><dd>{{ bundle.byte_size ?? '—' }}</dd>
                <dt>Policy</dt><dd>{{ bundle.exclusion_policy_version }}</dd>
                <dt>{{ t('main.group_actions.bundles.content_hash') }}</dt><dd>{{ bundle.content_fingerprint || '—' }}</dd>
                <dt>{{ t('main.group_actions.bundles.bundle_hash') }}</dt><dd>{{ bundle.bundle_sha256 || '—' }}</dd>
                <dt>{{ t('main.group_actions.bundles.freshness') }}</dt><dd>{{ bundle.freshness }}</dd>
                <dt>{{ t('main.group_actions.bundles.origin') }}</dt><dd>{{ bundle.origin }}</dd>
                <dt>{{ t('main.group_actions.bundles.failure') }}</dt><dd>{{ bundle.failure_code || bundle.failure_reason || '—' }}</dd>
                <dt>{{ t('main.group_actions.bundles.cleanup') }}</dt><dd>{{ bundle.cleanup_state }}{{ bundle.deleted_at ? ` · ${bundle.deleted_at}` : '' }}</dd>
              </dl>
            </article>
          </section>
        </div>
        
      

    <template #footer>
      <DialogFooter :actions="[
        { id: 'emit-0', role: 'aux', label: t('main.group_actions.rename_group'), onSelect: () => { emit('rename') } },
        { id: 'close-1', role: 'cancel', label: t('common.close'), onSelect: () => close() }
      ]">
        <template #action-emit-0>
            <AppIcon name="pencil-simple" />
            {{ t('main.group_actions.rename_group') }}
          </template>
        <template #action-close-1>
            {{ t('common.close') }}
          </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import DialogShell from './dialogs/DialogShell.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import AppIcon from '@shared/AppIcon.vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'

export interface GroupInfoDoc {
  id: string
  typeCode: string
  shortId: string
  title: string
  originProviderName?: string | null
  originAiRunId?: string | null
}

const props = defineProps<{
  visible: boolean
  projectId?: string
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

interface BundleRow {
  bundle_id: string
  status: string
  source_revision: string | null
  source_dirty: boolean
  created_at: string | null
  expires_at: string | null
  file_count: number | null
  byte_size: number | null
  exclusion_policy_version: string
  content_fingerprint: string | null
  bundle_sha256: string | null
  freshness: string
  origin: string
  failure_code: string | null
  failure_reason: string | null
  cleanup_state: string
  deleted_at: string | null
}

const bundles = ref<BundleRow[]>([])
const bundleError = ref(false)
let bundleFetchSeq = 0
watch(
  () => [props.visible, props.projectId, props.groupId] as const,
  async ([visible, projectId, groupId]) => {
    const seq = ++bundleFetchSeq
    bundles.value = []
    bundleError.value = false
    if (!visible || !projectId || !groupId) return
    try {
      const response = await getRequest<{ ok: boolean; bundles: BundleRow[] }>(
        '/api/v1/source-bundles', { project_id: projectId, group_id: groupId },
      )
      if (seq === bundleFetchSeq && props.visible) bundles.value = response.data?.bundles ?? []
    } catch {
      if (seq === bundleFetchSeq && props.visible) bundleError.value = true
    }
  },
  { immediate: true },
)

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
.gi-bundles { margin-top: 18px; border-top: 1px solid var(--border); padding-top: 12px; }
.gi-bundles h3 { margin: 0 0 8px; font-size: .82rem; }
.gi-bundles p { color: var(--text-m); font-size: .76rem; }
.gi-bundle { padding: 8px; border: 1px solid var(--border); margin: 8px 0; border-radius: var(--r); font-size: .72rem; }
.gi-bundle strong { overflow-wrap: anywhere; }
.gi-bundle dl { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 4px 8px; margin: 8px 0 0; }
.gi-bundle dt { color: var(--text-m); }
.gi-bundle dd { margin: 0; overflow-wrap: anywhere; }
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


:global(.fg-dialog-surface.dialog-group-info-modal) { width:560px; max-width:94vw; }
</style>
