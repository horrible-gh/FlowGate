<template>
  <section class="fgs-sec" data-test="ai-role-defaults">
    <div class="fgs-sec-head"><h3>{{ t('gui.aiRoles.title') }}</h3></div>
    <InspectorReadFailure v-if="!data && error" :reason="error" @retry="load" />
    <template v-if="data">
      <AiRoleFields v-model="values" :data="data" :disabled="saving" />
      <Button :disabled="saving || !complete" @click="save">{{ t('gui.common.save') }}</Button>
      <InspectorFailureIcon v-if="error" :name="t('gui.inspector.settings.failed')" :reason="error" />
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import Button from '@/components/ui/Button.vue'
import InspectorReadFailure from '@/features/designer/inspector/parts/InspectorReadFailure.vue'
import InspectorFailureIcon from '@/features/designer/inspector/parts/InspectorFailureIcon.vue'
import { getMyAiRoles, putMyAiRoles } from '../api'
import AiRoleFields from './AiRoleFields.vue'
import { AI_ROLES, type MyAiRoles, type AiRole, type AiRoleValue, type AiRoleValues } from './types'
import { installAiRoleMessages } from './messages'
const composer = useI18n({ useScope: 'global' }); installAiRoleMessages(composer)
const { t } = composer
const { user } = useCurrentUser()
const data = ref<MyAiRoles | null>(null)
const values = ref<Record<AiRole, AiRoleValue | null>>({ instruction: null, execution: null, review: null })
const saving = ref(false), error = ref<string | null>(null)
let epoch = 0
const complete = computed(() => AI_ROLES.every(role => values.value[role] !== null))
async function load() {
  const token = ++epoch; data.value = null; error.value = null; saving.value = false
  try {
    const answer = await getMyAiRoles()
    if (token !== epoch) return
    data.value = answer
    values.value = Object.fromEntries(AI_ROLES.map(role => [role, answer.roles[role].value])) as typeof values.value
  } catch (e) { if (token === epoch) error.value = e instanceof Error ? e.message : String(e) }
}
async function save() {
  if (!complete.value || saving.value) return
  const token = epoch; saving.value = true; error.value = null
  try {
    const answer = await putMyAiRoles(values.value as AiRoleValues)
    if (token === epoch) { data.value = answer; values.value = Object.fromEntries(AI_ROLES.map(role => [role, answer.roles[role].value])) as typeof values.value }
  } catch (e) { if (token === epoch) error.value = e instanceof Error ? e.message : String(e) }
  finally { if (token === epoch) saving.value = false }
}
watch(() => user.value?.username, () => { void load() }, { immediate: true })
onScopeDispose(() => { ++epoch })
</script>
