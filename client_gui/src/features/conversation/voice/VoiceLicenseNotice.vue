<template>
  <div class="voice-license-notice" data-testid="voice-license-notice">
    <p>Engine: {{ service.license }} · Weights: {{ model.license || 'Pending verification' }}</p>
    <p v-for="component in service.components ?? []" :key="component.filename">
      {{ component.componentType }}: {{ component.filename }} — {{ component.license }}
      · <a :href="component.url" target="_blank" rel="noopener noreferrer">Upstream download</a>
    </p>
    <p>Downloads are obtained directly from upstream. Offline/rehosting is on hold until component and native LICENSE/NOTICE files are verified. FFmpeg is excluded from redistributed bundles.</p>
    <p v-for="notice in service.nativeNotices ?? []" :key="notice">{{ notice }}</p>
    <template v-if="model.requiresLicenseAck">
      <p role="note">{{ model.license }} · <a :href="model.sourceUrl" target="_blank" rel="noopener noreferrer">{{ model.source }}</a>
        · <a :href="model.licenseUrl" target="_blank" rel="noopener noreferrer">Model license</a>. This model cannot be the system default.</p>
      <label v-if="!model.installed">
        <input type="checkbox" :checked="acknowledged" @change="$emit('update:acknowledged', ($event.target as HTMLInputElement).checked)" />
        I have read the pending license scope and upstream source before downloading.
      </label>
    </template>
  </div>
</template>
<script setup lang="ts">
import type { VoiceModel, VoiceService } from './api'
defineProps<{ service: VoiceService; model: VoiceModel; acknowledged: boolean }>()
defineEmits<{ 'update:acknowledged': [value: boolean] }>()
</script>
<style scoped>
.voice-license-notice { font-size: 12px; overflow-wrap: anywhere; }
p { margin: 4px 0; }
</style>
