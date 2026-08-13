<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box" style="width:620px;max-width:94vw;">
        <div class="modal-hd">
          <span class="modal-title gti-title">
            <AppIcon name="key" />
            {{ t('main.group_tree_node.issue_token_title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
          <!-- Lead: mirrors the approved prototype — group scope only, no group/doc creation. -->
          <div class="gti-lead">
            <AppIcon name="info" />
            <span>{{ t('main.group_tree_node.issue_token_lead') }}</span>
          </div>

          <div class="gti-field">
            <label>{{ t('main.group_tree_node.issue_token_target') }}</label>
            <div class="gti-group-card">
              <AppIcon name="folder-open" />
              <div>
                <div class="gti-group-id">{{ groupId }}</div>
                <div class="gti-group-name">{{ groupName || '—' }}</div>
              </div>
            </div>
          </div>

          <div class="gti-issue-row">
            <button class="btn btn-primary" type="button" :disabled="issuing" @click="onIssue">
              <AppIcon name="key" />
              {{ t('main.group_tree_node.issue_token_btn') }}
            </button>
            <span class="gti-note">{{ t('main.group_tree_node.issue_token_note') }}</span>
          </div>

          <!-- Result: only the full execution mention is shown (no standalone Bearer card). -->
          <div v-if="mention" class="gti-out">
            <div class="gti-out-hd"><AppIcon name="check-circle" /> {{ t('main.group_tree_node.issue_token_done') }}</div>
            <div class="gti-mention-head">
              <span><AppIcon name="chat-circle-dots" /> {{ t('main.group_tree_node.issue_token_mention_head') }}</span>
              <button class="btn btn-outline btn-sm" type="button" @click="onCopy">
                <AppIcon name="copy" />
                {{ t('main.group_tree_node.issue_token_copy') }}
              </button>
            </div>
            <pre class="gti-mention">{{ mention }}</pre>
            <div class="gti-deliver">
              <AppIcon name="sign-out" />
              <span>{{ t('main.group_tree_node.issue_token_deliver') }}</span>
            </div>
          </div>
        </div>
        <div class="modal-ft">
          <button type="button" class="btn btn-secondary" @click="close">{{ t('common.close') }}</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFlowGateToken, splitGroupId, type IssuedToken } from '../composables/useFlowGateToken'
import { copyToClipboard } from '../utils/clipboard'
import { openClipboardFallback } from '../composables/useClipboardFallback'
import { useToast } from './common/useToast'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  visible: boolean
  // Canonical group_id (project.module.code) of the right-clicked group.
  groupId: string
  groupName: string
  projectId: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const { t } = useI18n()
const { showToast } = useToast()
const { issuing, issueToken } = useFlowGateToken()

const mention = ref<string | null>(null)

// Build the group-token execution mention. Faithful to the approved prototype
// (set3/document_page_group_bootstrap.html) — one self-contained mention that carries the
// Bearer credential together with the target group and usage steps, so the credential is
// never handed over on its own. The real token adds scratch dir + expiry over the mock.
function buildGroupMention(token: IssuedToken, ids: { project: string; module: string; groupId: string }): string {
  return [
    t('main.group_token_mention.title'),
    t('main.group_token_mention.lead', { group: ids.groupId }),
    '',
    `Authorization: Bearer ${token.raw_token}`,
    '',
    `${t('main.group_token_mention.project')} ${ids.project}`,
    `${t('main.group_token_mention.module')} ${ids.module}`,
    `${t('main.group_token_mention.target_group')} ${ids.groupId}`,
    t('main.group_token_mention.scope'),
    `${t('main.group_token_mention.scratch_dir')} ${token.scratch_dir}`,
    `${t('main.group_token_mention.expires_at')} ${token.expires_at}`,
    t('main.group_token_mention.api'),
    t('main.group_token_mention.completion'),
  ].join('\n')
}

async function onIssue() {
  if (issuing.value) return
  const parts = splitGroupId(props.groupId)
  const project = props.projectId || props.groupId.split('.')[0]
  // Group-scoped token: no doc_ref (this is a bare group work token, not a workflow step).
  // issueToken → /token/issue; the backend auto-resolves action_scope to 'new'.
  const token = await issueToken({
    project,
    ...(parts?.module ? { module: parts.module } : {}),
    group: parts?.groupCode ?? props.groupId,
  })
  if (!token) return // issueToken already surfaced the error toast (401/403/other)
  mention.value = buildGroupMention(token, {
    project,
    module: parts?.module ?? 'none',
    groupId: props.groupId,
  })
  showToast(t('main.group_tree_node.issue_token_issued', { group: props.groupId }), 'success')
}

async function onCopy() {
  if (!mention.value) return
  const ok = await copyToClipboard(mention.value)
  if (ok) {
    showToast(t('main.group_tree_node.issue_token_copied'), 'success')
  } else {
    // B0001 / group 0221: the write failed with the text in hand — open the manual-copy
    // fallback modal. (The old toast referenced main.ai_worker_trigger_buttons.*, a key that
    // never existed in any locale, so it displayed the raw key path.)
    openClipboardFallback(mention.value)
  }
}

function close() {
  emit('update:visible', false)
}

// Reset the issued mention each time the dialog opens so a stale token is never shown.
watch(
  () => props.visible,
  (val) => {
    if (val) mention.value = null
  },
)
</script>

<style scoped>
.gti-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--primary);
}
.gti-lead {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 16px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface-h);
  color: var(--text-s);
  font-size: .8rem;
  line-height: 1.5;
}
.gti-lead > .app-icon { color: var(--primary); margin-top: 2px; }
.gti-field { margin-bottom: 16px; }
.gti-field > label {
  display: block;
  margin-bottom: 6px;
  color: var(--text-m);
  font-size: .64rem;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
}
.gti-group-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg);
}
.gti-group-card > .app-icon { color: #f59e0b; font-size: 1.1rem; }
.gti-group-id {
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: .82rem;
}
.gti-group-name { color: var(--text-m); font-size: .74rem; margin-top: 2px; }
.gti-issue-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.gti-note { color: var(--text-m); font-size: .74rem; }
.gti-out {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
.gti-out-hd {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--success, #34d399);
  font-size: .8rem;
  font-weight: 700;
  margin-bottom: 12px;
}
.gti-mention-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.gti-mention-head > span {
  color: var(--text-m);
  font-size: .64rem;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.gti-mention {
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg);
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: .76rem;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
}
.gti-deliver {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  color: var(--text-m);
  font-size: .74rem;
  line-height: 1.5;
}
.gti-deliver > .app-icon { margin-top: 2px; }
</style>