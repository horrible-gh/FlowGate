<template>
  <!-- One line that says why buttons are off (flowgate.v02test.0090). The server's refusals of the listed actions
       are grouped by reason: buttons refused for the same reason share one sentence, different reasons give
       one sentence each. Nothing is shown while every listed action is allowed or no answer is in yet. -->
  <p v-if="lines.length" class="fg-permission-note" role="note" data-test="permission-note">
    <span v-for="line in lines" :key="line" class="fg-permission-note-line" data-test="permission-note-line">{{ line }}</span>
  </p>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCapabilities, type CapabilityBlock } from '@shared/permissions/capabilities'
import { denialLines, type DeniedAction } from '@shared/permissions/denial'

const props = defineProps<{
  projectId: string | null
  /** Permission ids (`project.run`, …) whose buttons this note explains. */
  actions: readonly string[]
  /**
   * An answer that came with one target (a document: context rules included) is used instead of the project's.
   * Without it the shared store's block of `projectId` is read.
   */
  block?: CapabilityBlock | null
}>()

const { t } = useI18n()
const caps = useCapabilities(() => props.projectId)

const lines = computed(() => {
  const refused: DeniedAction[] = []
  for (const action of props.actions) {
    const own = props.block?.actions[action]
    const decision = props.block ? (own && !own.allowed ? own : null) : caps.refusal(action)
    if (decision) refused.push({ action, decision })
  }
  return denialLines(refused, props.block ? props.block.role : caps.role.value, (key, params) => t(key, params ?? {}))
})
</script>

<style scoped>
.fg-permission-note {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 14px;
  margin: 0;
  padding: 5px 12px;
  border-bottom: 1px solid var(--fg2-status-warning-border, #e0b252);
  background: var(--fg2-status-warning-bg, #fff6e0);
  color: var(--fg2-text, #1d2939);
  font-size: 12px;
  line-height: 1.5;
}
.fg-permission-note-line::before { content: '※ '; }
</style>
