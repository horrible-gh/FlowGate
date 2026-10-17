<template>
  <!-- flowgate.v02.0064 T#4 rework (P0006 §3.3) — the header meta part of a document made by an R↔B succession:
       "전환 전 문서: 0001-R" opens the document it came from (useKindSwitchOrigin: the plain document read, no switch
       judgement, whatever the Flow's ownership); a document that was not succeeded shows nothing. -->
  <button
    v-if="convertedFrom"
    type="button"
    class="inspector-kind-switch-from"
    data-test="kind-switch-converted-from"
    :data-doc="convertedFrom.docId"
    @click="openDocument(convertedFrom.docId)"
  >{{ t('gui.inspector.kindSwitchOrigin.converted_from', { doc: codeOf(convertedFrom.docId) }) }}</button>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import { openDocument } from '../../openDocument'
import { useKindSwitchOrigin } from './useKindSwitchOrigin'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const convertedFrom = useKindSwitchOrigin(() => props.target)

/** `…0001-R` -> `0001-R`. */
const codeOf = (docId: string) => docId.split('.').pop() ?? ''
</script>

<style scoped>
.inspector-kind-switch-from {
  flex: 0 0 auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: #8cc4ff;
  font-size: 11px;
  text-decoration: underline;
  white-space: nowrap;
  cursor: pointer;
}
</style>
