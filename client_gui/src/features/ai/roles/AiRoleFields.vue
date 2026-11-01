<template>
  <div class="ai-role-fields">
    <label v-for="role in AI_ROLES" :key="role">
      <span>{{ t(`gui.aiRoles.${role}`) }}</span>
      <AiRoleSelect :model-value="modelValue[role]" :providers="data.options" :label="t(`gui.aiRoles.${role}`)" :display-name="data.roles[role].displayName" :state="data.roles[role].state" :cause="data.roles[role].cause" :disabled="disabled" @update:model-value="value => emit('update:modelValue', { ...modelValue, [role]: value })" />
    </label>
  </div>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AiRoleSelect from './AiRoleSelect.vue'
import { AI_ROLES, type MyAiRoles, type AiRole, type AiRoleValue } from './types'
import { installAiRoleMessages } from './messages'
defineProps<{ data: MyAiRoles; modelValue: Record<AiRole, AiRoleValue | null>; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Record<AiRole, AiRoleValue | null>] }>()
const composer = useI18n({ useScope: 'global' }); installAiRoleMessages(composer)
const { t } = composer
</script>
<style scoped>
.ai-role-fields { display: grid; gap: 10px; }
label { display: grid; grid-template-columns: 100px minmax(0, 1fr); align-items: center; gap: 8px; }
</style>
