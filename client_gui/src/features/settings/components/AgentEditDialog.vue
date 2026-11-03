<template>
  <ModalShell :open="true" surface-class="agent-settings-dialog" close-on-backdrop :aria-label="title" @close="emit('close')">
    <template #header="{ titleId }">
      <header class="agent-dialog-head"><h2 :id="titleId">{{ title }}</h2><Button variant="ghost" :disabled="busy" :aria-label="t('gui.common.close')" data-testid="agent-editor-close" @click="emit('close')"><SettingsIcon name="x" /></Button></header>
    </template>
    <form class="agent-dialog-form" :data-testid="creating ? 'agent-create-form' : 'agent-detail'" :data-agent="agent?.agent_id" @submit.prevent="emit('submit')">
      <div class="agent-form-group">
        <label for="agent-dialog-name">{{ t('gui.settings.agents.fields.name') }} <span>*</span></label>
        <Input id="agent-dialog-name" :model-value="name" :maxlength="100" :disabled="busy" :invalid="!!name && !!nameProblem" autocomplete="off" data-modal-autofocus :data-testid="creating ? 'agent-name' : 'edit-name'" @update:model-value="emit('update:name', $event)" />
        <small v-if="name && nameProblem" class="fgs-error" data-testid="agent-name-invalid">{{ nameProblem }}</small>
        <small v-else>{{ t('gui.settings.agents.name_hint', { max: 100 }) }}</small>
      </div>
      <dl v-if="agent && !creating" class="agent-info-grid" data-testid="agent-status">
        <div><dt>{{ t('gui.agent_screen.connection') }}</dt><dd data-field="connection">{{ stateLabel }}</dd></div>
        <div><dt>{{ t('gui.agent_screen.os') }}</dt><dd data-field="platform">{{ [agent.os, agent.architecture].filter(Boolean).join(' / ') || '—' }}</dd></div>
      </dl>
      <p v-else class="agent-dialog-note">{{ t('gui.agent_screen.create_hint') }}</p>
      <fieldset class="agent-form-group">
        <legend>{{ t('gui.settings.agents.create_caps') }}</legend>
        <label v-for="cap in VISIBLE_CAPS" :key="cap" class="agent-check-row">
          <input type="checkbox" :checked="caps[cap]" :disabled="busy" :data-cap="creating ? cap : undefined" :data-edit-cap="creating ? undefined : cap" @change="emit('update:cap', cap, ($event.target as HTMLInputElement).checked)" />
          <span><strong>{{ t(`gui.settings.agents.caps.${cap}`) }}</strong><small>{{ t(`gui.settings.agents.caps_desc.${cap}`) }}</small></span>
        </label>
        <small>{{ t('gui.settings.agents.caps_hint') }}</small>
      </fieldset>
      <p v-if="error" class="fgs-error" role="alert" :data-testid="creating ? 'agent-form-error' : 'agent-action-error'">{{ error }}</p>
      <p v-if="saved" role="status" data-testid="edit-saved">{{ t('gui.agent_screen.saved') }}</p>
    </form>
    <template #footer><footer class="agent-dialog-foot">
      <Button variant="secondary" :disabled="busy" :data-testid="creating ? 'agent-create-cancel' : 'edit-cancel'" @click="emit('close')">{{ t('gui.common.cancel') }}</Button>
      <Button variant="primary" :disabled="!canSave" :data-testid="creating ? 'agent-create-submit' : 'edit-save'" @click="emit('submit')">{{ busy ? t('gui.settings.saving') : t(creating ? 'gui.agent_screen.create_submit' : 'gui.agent_screen.save') }}</Button>
    </footer></template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ModalShell from '@/components/ui/ModalShell.vue'
import Input from '@/components/ui/Input.vue'
import Button from '@/components/ui/Button.vue'
import SettingsIcon from '../SettingsIcon.vue'
import type { AgentCapabilities, SystemAgent } from '../types'
const props = defineProps<{
  creating: boolean; agent: SystemAgent | null; name: string; caps: AgentCapabilities
  busy: boolean; canSave: boolean; nameProblem: string; error: string; saved: boolean; stateLabel: string
}>()
const emit = defineEmits<{
  'update:name': [name: string]; 'update:cap': [cap: 'ai_cli' | 'ai_api', value: boolean]
  submit: []; close: []
}>()
const { t } = useI18n()
const VISIBLE_CAPS = ['ai_cli', 'ai_api'] as const
const title = computed(() => t(props.creating ? 'gui.agent_screen.create_title' : 'gui.agent_screen.edit_title'))
</script>

<style>
.ui-modal-surface.agent-settings-dialog { max-width: 515px; border-radius: 14px; }
.agent-settings-dialog .agent-dialog-head { display: flex; justify-content: space-between; align-items: center; padding: 20px 24px 17px; border-bottom: 1px solid var(--fg2-line-strong); }
.agent-settings-dialog .agent-dialog-head h2 { font-size: 17px; margin: 0; }
.agent-settings-dialog .agent-dialog-form { display: grid; gap: 17px; padding: 22px 0; }
.agent-settings-dialog .agent-form-group { display: grid; gap: 8px; border: 0; padding: 0; margin: 0; min-width: 0; }
.agent-settings-dialog .agent-form-group > label, .agent-settings-dialog legend { font-size: 11px; font-weight: 700; }
.agent-settings-dialog legend { margin-bottom: 8px; }
.agent-settings-dialog small { color: var(--fg2-muted); font-size: 10px; line-height: 1.6; }
.agent-settings-dialog .agent-check-row { display: flex; gap: 12px; align-items: center; padding: 12px 13px; border: 1px solid var(--fg2-line-strong); border-radius: 9px; background: var(--fg2-control-surface); }
.agent-settings-dialog .agent-check-row strong, .agent-settings-dialog .agent-check-row small { display: block; }
.agent-settings-dialog .agent-check-row input { width: 16px; height: 16px; }
.agent-settings-dialog .agent-info-grid { display: grid; grid-template-columns: 1fr 1fr; border: 1px solid var(--fg2-line-strong); border-radius: 10px; margin: 0; }
.agent-settings-dialog .agent-info-grid > div { padding: 15px; }
.agent-settings-dialog dt { font-size: 11px; color: var(--fg2-muted); }
.agent-settings-dialog dd { margin: 8px 0 0; font-size: 13px; }
.agent-settings-dialog .agent-dialog-note { margin: 0; padding: 12px 15px; border: 1px solid var(--fg2-line-strong); border-radius: 10px; font-size: 11px; line-height: 1.65; }
.agent-settings-dialog .agent-dialog-foot { display: flex; justify-content: flex-end; gap: 9px; padding: 14px 24px; border-top: 1px solid var(--fg2-line-strong); }
</style>
