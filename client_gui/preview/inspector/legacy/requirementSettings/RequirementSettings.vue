<!-- Historical previews may still import this component; delegation has no controls or reads. -->
<template><span hidden /></template>
<script setup lang="ts">
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import type { RequirementSettingsView } from './requirementSettingsModel'
defineProps<{ target: InspectorTarget; view?: RequirementSettingsView | null }>()
</script>
