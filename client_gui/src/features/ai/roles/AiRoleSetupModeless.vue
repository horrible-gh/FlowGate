<template>
  <ModelessShell
    :open="!!data"
    :title="t('gui.aiRoles.setup')"
    :initial-x="initialX"
    :initial-y="initialY"
    :initial-width="400"
    :initial-height="340"
    :min-width="320"
    :min-height="260"
    @close="emit('close')"
  >
    <section v-if="data" class="ai-role-modeless" role="dialog" aria-modal="false" :aria-label="t('gui.aiRoles.setup')" data-test="ai-role-setup">
      <AiRoleFields v-model="values" :data="data" :disabled="saving" />
      <footer>
        <InspectorFailureIcon v-if="error" :name="t('gui.inspector.settings.failed')" :reason="error" />
        <Button variant="secondary" @click="emit('close')">{{ t('gui.common.cancel') }}</Button>
        <Button :disabled="saving || !complete" @click="submit">{{ t('gui.common.save') }}</Button>
      </footer>
    </section>
  </ModelessShell>
</template>
<script setup lang="ts">
// flowgate.v02.0117 T#3 — the AI role setup window is a ModelessShell like the run result window: the shell owns the frame
// (drag, minimize, close ×, Esc, overlay z-index, focus return). Body, footer, props and emits are unchanged.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import InspectorFailureIcon from '@/features/designer/inspector/parts/InspectorFailureIcon.vue'
import AiRoleFields from './AiRoleFields.vue'
import { AI_ROLES, type MyAiRoles, type AiRole, type AiRoleValue, type AiRoleValues } from './types'
import { installAiRoleMessages } from './messages'
const props = defineProps<{ data: MyAiRoles | null; saving: boolean; error: string | null }>()
const emit = defineEmits<{ close: []; save: [values: AiRoleValues] }>()
const composer = useI18n({ useScope: 'global' }); installAiRoleMessages(composer)
const { t } = composer
const viewportWidth = typeof window === 'undefined' ? 1280 : window.innerWidth
const initialX = Math.max(8, viewportWidth - 400 - 32)
const initialY = 100
const values = ref<Record<AiRole, AiRoleValue | null>>({ instruction: null, execution: null, review: null })
watch(() => props.data, data => { if (data) values.value = Object.fromEntries(AI_ROLES.map(role => [role, data.roles[role].value])) as typeof values.value }, { immediate: true })
const complete = computed(() => AI_ROLES.every(role => values.value[role] !== null))
function submit() { if (complete.value && !props.saving) emit('save', { ...values.value } as AiRoleValues) }
</script>
<style scoped>
footer { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-top: 14px; }
</style>
