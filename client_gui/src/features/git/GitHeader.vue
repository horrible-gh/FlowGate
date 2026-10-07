<template>
  <header class="gx-header">
    <span class="gx-header-icon" aria-hidden="true">⑂</span>
    <div class="gx-header-main">
      <div class="gx-header-branch" data-test="git-work-branch">{{ workBranch || t('gui.git.header.none') }}</div>
      <div class="gx-header-base" data-test="git-base-branch">
        {{ t('gui.git.header.base') }} · {{ baseLabel }}
      </div>
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
      <span aria-hidden="true">⟳</span>
    </IconButton>
  </header>
  <p v-if="line" class="gx-header-line" :class="{ 'is-error': lineError }" data-test="git-header-line">{{ line }}</p>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — Git Header (0033-D §6.1): the active Flow's work branch, the project
// Base (only a configured one; otherwise "미설정") and refresh. Nothing else of the repository.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '../../components/ui/IconButton.vue'

const props = defineProps<{
  workBranch: string | null
  // null = not configured (when the status is known).
  baseBranch: string | null
  statusKnown: boolean
  enabled: boolean
  statusError: 'unreadable' | 'network' | null
  loading: boolean
  refreshing: boolean
  notice: string | null
}>()
const emit = defineEmits<{ refresh: [] }>()
const { t } = useI18n()

const baseLabel = computed(() => {
  if (!props.statusKnown) return t('gui.git.header.none')
  return props.baseBranch || t('gui.git.header.base_unset')
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
