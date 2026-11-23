<template>
  <header class="gx-header">
    <span class="gx-header-icon" aria-hidden="true"><GitIcon name="git" :size="14" /></span>
    <div class="gx-header-main">
      <div class="gx-header-branch" data-test="git-work-branch">{{ workBranch || t('gui.git.header.none') }}</div>
      <div class="gx-header-base" data-test="git-base-branch">{{ baseText }}</div>
    </div>
    <IconButton
      class="gx-header-refresh"
      :label="t('gui.git.header.refresh')"
      :title="t('gui.git.header.refresh')"
      size="sm"
      :disabled="refreshing"
      data-test="git-refresh"
      @click="emit('refresh')"
    >
      <GitIcon name="refresh" :size="14" />
    </IconButton>
  </header>
  <p v-if="line" class="gx-header-line" :class="{ 'is-error': lineError }" data-test="git-header-line">{{ line }}</p>
</template>

<script setup lang="ts">
// the active Flow's work branch and effective Flow base; without a
// Flow, the project base. Unknown values stay "—". Refresh and status feedback stay here.
// 0068 (v16 mockup): a Flow reads `flow/<no> ← <base>`; the Git station moved to the tab footer.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '../../components/ui/IconButton.vue'
import GitIcon from './GitIcon.vue'

const props = defineProps<{
  workBranch: string | null
  // Project base is used only without an active Flow.
  baseBranch: string | null
  hasFlow: boolean
  // The active Flow's group id (`project.module.0068`); its last part names the Flow in the base line.
  flowId?: string | null
  flowBase: string | null
  statusKnown: boolean
  enabled: boolean
  statusError: 'unreadable' | 'network' | null
  loading: boolean
  refreshing: boolean
  notice: string | null
}>()
const emit = defineEmits<{ refresh: []; station: [] }>()
const { t } = useI18n()

const baseLabel = computed(() => {
  if (!props.statusKnown || !props.enabled) return '—'
  return (props.hasFlow ? props.flowBase : props.baseBranch) || '—'
})
const baseText = computed(() => {
  if (!props.hasFlow) return `${t('gui.git.header.base')} · ${baseLabel.value}`
  const no = props.flowId?.split('.').pop()
  return no ? `flow/${no} ← ${baseLabel.value}` : `${t('gui.git.header.flow_base')} · ${baseLabel.value}`
})

const lineError = computed(() => !!props.statusError || !!props.notice)
const line = computed(() => {
  if (props.statusError === 'network') return t('gui.git.header.network')
  if (props.statusError) return t('gui.git.header.unreadable')
  if (props.notice) return props.notice
  if (props.loading) return t('gui.git.header.loading')
  if (props.statusKnown && !props.enabled) return t('gui.git.header.disabled')
  return null
})
</script>
