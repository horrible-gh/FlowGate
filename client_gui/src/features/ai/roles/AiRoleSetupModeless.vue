<template>
  <Teleport to="body">
    <section v-if="data" class="ai-role-modeless" role="dialog" aria-modal="false" :aria-label="t('gui.aiRoles.setup')" data-test="ai-role-setup" @keydown.esc.stop="emit('close')">
      <header><strong>{{ t('gui.aiRoles.setup') }}</strong><button type="button" :aria-label="t('gui.common.close')" @click="emit('close')">×</button></header>
      <AiRoleFields v-model="values" :data="data" :disabled="saving" />
      <footer>
        <InspectorFailureIcon v-if="error" :name="t('gui.inspector.settings.failed')" :reason="error" />
        <Button variant="secondary" @click="emit('close')">{{ t('gui.common.cancel') }}</Button>
        <Button :disabled="saving || !complete" @click="submit">{{ t('gui.common.save') }}</Button>
      </footer>
    </section>
  </Teleport>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import InspectorFailureIcon from '@/features/designer/inspector/parts/InspectorFailureIcon.vue'
import AiRoleFields from './AiRoleFields.vue'
import { AI_ROLES, type MyAiRoles, type AiRole, type AiRoleValue, type AiRoleValues } from './types'
import { installAiRoleMessages } from './messages'
const props = defineProps<{ data: MyAiRoles | null; saving: boolean; error: string | null }>()
const emit = defineEmits<{ close: []; save: [values: AiRoleValues] }>()
const composer = useI18n({ useScope: 'global' }); installAiRoleMessages(composer)
const { t } = composer
const values = ref<Record<AiRole, AiRoleValue | null>>({ instruction: null, execution: null, review: null })
watch(() => props.data, data => { if (data) values.value = Object.fromEntries(AI_ROLES.map(role => [role, data.roles[role].value])) as typeof values.value }, { immediate: true })
const complete = computed(() => AI_ROLES.every(role => values.value[role] !== null))
function submit() { if (complete.value && !props.saving) emit('save', { ...values.value } as AiRoleValues) }
</script>
<style scoped>
.ai-role-modeless { position: fixed; right: 32px; top: 100px; width: min(400px, calc(100vw - 48px)); z-index: 1100; padding: 16px; background: var(--fg2-control-surface, #122234); color: var(--fg2-text, #e3eef9); border: 1px solid var(--fg2-line-strong, #315271); border-radius: 8px; box-shadow: 0 8px 32px #0006; }
header, footer { display: flex; align-items: center; gap: 8px; }
header { justify-content: space-between; margin-bottom: 14px; }
footer { justify-content: flex-end; margin-top: 14px; }
header button { background: transparent; color: inherit; border: 0; cursor: pointer; }
</style>
