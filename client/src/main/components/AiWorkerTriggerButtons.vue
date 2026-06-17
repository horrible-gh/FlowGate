<template>
  <template v-if="canShow">
    <button
      class="btn btn-soft-amber btn-sm"
      :disabled="issuing"
      @click="onCopyMent"
    >
      <i class="fa-regular fa-copy"></i>
      {{ copyLabel }}
    </button>
    <button
      class="btn btn-soft-amber btn-sm"
      :disabled="issuing"
      @click="onInvokeCommand"
    >
      <i class="fa-solid fa-terminal"></i>
      {{ t('main.ai_worker_trigger_buttons.invoke_command') }}
    </button>
    <CommandSelectorModal
      v-model:visible="commandModalVisible"
      :env-overrides="pendingEnvOverrides"
    />
  </template>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFlowGateToken, splitGroupId, type TokenIssueParams } from '../composables/useFlowGateToken'
import { useToast } from './common/useToast'
import { hasDocumentReadPermission } from '@shared/auth'
import CommandSelectorModal from './CommandSelectorModal.vue'

const props = withDefaults(
  defineProps<{
    project: string | null
    groupId: string | null
    docRef?: string | null
    actionScope?: 'new' | 'edit'
    mention?: string | null
  }>(),
  {
    docRef: null,
    mention: null,
  },
)

const { issuing, issueToken, copyMentToClipboard } = useFlowGateToken()
const { t } = useI18n()
const { showToast } = useToast()

const copied = ref(false)
const copyLabel = computed(() => t(
  copied.value
    ? 'main.ai_worker_trigger_buttons.copied'
    : 'main.ai_worker_trigger_buttons.copy_mention',
))
const commandModalVisible = ref(false)
const pendingEnvOverrides = ref<Record<string, string> | null>(null)

const canShow = computed(
  () => !!(props.project && props.groupId && hasDocumentReadPermission()),
)

function buildIssueParams(): TokenIssueParams {
  const gParts = splitGroupId(props.groupId)
  return {
    project: props.project!,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? props.groupId!,
    ...(props.actionScope != null ? { action_scope: props.actionScope } : {}),
    doc_ref: props.docRef ?? undefined,
  }
}

async function doClipboardCopy(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch { /* fall through */ }
  }
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.style.cssText = 'position:fixed;left:-9999px;top:-9999px;'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(el)
    return ok
  } catch {
    return false
  }
}

async function onCopyMent() {
  if (issuing.value) return

  if (props.mention) {
    const ok = await doClipboardCopy(props.mention)
    if (ok) {
      copied.value = true
      showToast(t('main.ai_worker_trigger_buttons.mention_copied'), 'success')
      setTimeout(() => { copied.value = false }, 2000)
    } else {
      showToast(t('main.ai_worker_trigger_buttons.clipboard_unsupported'), 'warning')
    }
    return
  }

  const token = await issueToken(buildIssueParams())
  if (!token) return
  const ok = await copyMentToClipboard(token)
  if (ok) {
    copied.value = true
    showToast(t('main.ai_worker_trigger_buttons.mention_copied'), 'success')
    setTimeout(() => { copied.value = false }, 2000)
  } else {
    showToast(t('main.ai_worker_trigger_buttons.clipboard_unsupported'), 'warning')
  }
}

async function onInvokeCommand() {
  if (issuing.value) return
  const token = await issueToken(buildIssueParams())
  if (!token) return
  pendingEnvOverrides.value = {
    FLOWGATE_TOKEN: token.raw_token,
    FLOWGATE_SCRATCH: token.scratch_dir,
  }
  commandModalVisible.value = true
}
</script>
