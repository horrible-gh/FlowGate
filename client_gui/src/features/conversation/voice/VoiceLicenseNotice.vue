<template>
  <div class="voice-license-notice" data-testid="voice-license-notice">
    <p>Engine: {{ service.license }} · Weights: {{ model.license || 'Pending verification' }}</p>
    <p v-for="component in service.components ?? []" :key="component.filename">
      {{ component.componentType }}: {{ component.filename }} — {{ component.license }}
      · <a :href="component.url" target="_blank" rel="noopener noreferrer">Upstream download</a>
    </p>
    <p>Downloads are obtained directly from upstream. Offline/rehosting is on hold until component and native LICENSE/NOTICE files are verified. FFmpeg is excluded from redistributed bundles.</p>
    <p v-for="notice in service.nativeNotices ?? []" :key="notice">{{ notice }}</p>
  </div>
</template>
<script setup lang="ts">
import type { VoiceModel, VoiceService } from './api'
defineProps<{ service: VoiceService; model: VoiceModel }>()
</script>
<style scoped>
.voice-license-notice { font-size: 12px; overflow-wrap: anywhere; }
p { margin: 4px 0; }
</style>
