<template>
  <!-- flowgate.v02.0051 preview — a fixture documents tab for the frame sample: the 작업 · 검수 대기
       card's two slot lines (insp.js work.wait), built from the common parts. Not a feature.
       flowgate.v02.0053 T#5 — the line is `name · state dot · [document icon] · ✎`. -->
  <div class="preview-slots">
    <InspectorSlotRow name="지시서" id="0008.0006-T" :state="{ label: '승인', tone: 'success' }" openable>
      <InspectorOpenButton />
      <IconButton label="편집" title="편집" variant="secondary" size="sm">✎</IconButton>
    </InspectorSlotRow>
    <InspectorSlotRow name="레포트" id="0008.0007-TR" :state="{ label: '검수 대기', tone: 'warning' }" openable focus>
      <InspectorReviewBadge :count="2" state="pass" :remain="0" />
      <InspectorOpenButton />
      <IconButton label="편집" title="편집" variant="secondary" size="sm">✎</IconButton>
    </InspectorSlotRow>
  </div>
</template>

<script setup lang="ts">
import IconButton from '@/components/ui/IconButton.vue'
import InspectorOpenButton from '@/features/designer/inspector/parts/InspectorOpenButton.vue'
import InspectorReviewBadge from '@/features/designer/inspector/parts/InspectorReviewBadge.vue'
import InspectorSlotRow from '@/features/designer/inspector/parts/InspectorSlotRow.vue'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'

defineProps<{ target: InspectorTarget }>()
</script>

<style scoped>
.preview-slots {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
