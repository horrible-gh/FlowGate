<template>
  <!-- D0007 §6: archive and Git reflection, saved immediately. -->
  <div class="inspector-check-settings" data-test="inspector-check-settings" :data-connected="shown.connected ? 'true' : 'false'">
    <InspectorReadFailure v-if="readFailure" :reason="readFailure" @retry="reload" />
    <template v-else>
      <InspectorBlock data-test="check-settings-archive">
        <InspectorControlRow :label="t(k('archive'))" :hint="reasonText">
          <Toggle :model-value="shown.archive ?? false" :disabled="off" :aria-label="t(k('archive'))" data-test="check-settings-archive-value" @update:model-value="(value: boolean) => onChange('archive', value)" />
          <InspectorFailureIcon v-if="failureOf('archive')" :name="t(k('failed'))" :reason="failureOf('archive')!" data-field="archive" />
        </InspectorControlRow>
      </InspectorBlock>

      <InspectorBlock :title="t(k('git.title'))" data-test="check-settings-git">
        <InspectorControlRow :label="t(k('git.scope'))" :hint="reasonText">
          <InspectorSegment
            :model-value="shown.scope"
            :options="CHECK_SCOPES.map((scope) => ({ value: scope, label: scopeLabel(scope), title: reasonText }))"
            :disabled="off"
            :aria-label="t(k('git.scope'))"
            data-test="check-settings-scope"
            @update:model-value="(value: string) => onChange('merge_mode', value)"
          />
          <InspectorFailureIcon v-if="failureOf('merge_mode')" :name="t(k('failed'))" :reason="failureOf('merge_mode')!" data-field="merge_mode" />
        </InspectorControlRow>
        <InspectorControlRow :label="t(k('git.target'))" :hint="reasonText" wrap>
          <div class="inspector-check-settings-target-wrap">
            <Select
              class="inspector-check-settings-target"
            :class="{ 'has-missing-target': !shown.targetOptions.includes(shown.target ?? '') }"
            :model-value="shown.target ?? ''"
            :options="targetOptions"
            :disabled="off || !shown.targetEnabled"
            :invalid="!!targetFailure"
            :aria-label="t(k('git.target'))"
            :aria-invalid="!!targetFailure"
            data-test="check-settings-target"
            @update:model-value="(value: string) => onTarget(value)"
          />
          <span v-if="!shown.targetOptions.includes(shown.target ?? '')" class="inspector-check-settings-missing" data-test="check-settings-target-display">{{ shown.target ?? t(k('target.unavailable')) }}</span>
          </div>
          <Button size="sm" variant="ghost" :disabled="off || shown.scope !== 'merge' || !shown.targetStored" data-test="check-settings-default" @click="onTarget(null)">{{ t(k('target.use_default')) }}</Button>
          <span v-if="targetFailure" role="alert" data-test="check-settings-target-error">{{ targetFailure }}</span>
          <span v-if="shown.lookupError" role="status" data-test="check-settings-target-state">{{ t(k(shown.lookupError)) }}</span>
          <Button size="sm" variant="ghost" v-if="shown.canRefreshTargets" type="button" :disabled="off" data-test="check-settings-refresh" @click="reload">{{ t(k('target.refresh')) }}</Button>
          <label class="inspector-check-settings-toggle">
            <span>{{ t(k('git.push')) }}</span>
            <Toggle :model-value="shown.push ?? false" :disabled="off" :aria-label="t(k('git.push'))" data-test="check-settings-push" @update:model-value="(value: boolean) => onChange('push', value)" />
          </label>
          <span v-if="shown.push" data-test="check-settings-push-create">{{ t(k('target.push_create')) }}</span>
          <span v-if="shown.pushCheckRequired || shown.failures?.push === 'target.push_check'" data-test="check-settings-push-check">{{ t(k('target.push_check')) }}</span>
          <Button size="sm" variant="ghost" v-if="shown.pushCheckRequired || shown.failures?.push === 'target.push_check'" type="button" :disabled="off" data-test="check-settings-push-recheck" @click="onChange('push', true)">{{ t(k('target.check_settings')) }}</Button>
          <label class="inspector-check-settings-toggle">
            <span>{{ t(k('git.keep_branch')) }}</span>
            <Toggle
              :model-value="shown.keepBranch ?? false"
              :disabled="off"
              :aria-label="t(k('git.keep_branch'))"
              data-test="check-settings-keep-branch"
              @update:model-value="(value: boolean) => onChange('keep_branch', value)"
            />
          </label>
          <InspectorFailureIcon v-for="field in GIT_FIELDS.filter((name) => failureOf(name))" :key="field" :name="t(k('failed'))" :reason="failureOf(field)!" :data-field="field" />
        </InspectorControlRow>
        <p v-if="shown.unmergeWarning" data-test="check-settings-unmerge-warning">{{ t(k('target.unmerge_unsupported')) }}</p>
      </InspectorBlock>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import Select from '../../../../../components/ui/Select.vue'
import Toggle from '../../../../../components/ui/Toggle.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorFailureIcon from '../../parts/InspectorFailureIcon.vue'
import InspectorReadFailure from '../../parts/InspectorReadFailure.vue'
import InspectorSegment from '../../parts/InspectorSegment.vue'
import {
  CHECK_SCOPES,
  checkSettingsErrorKey,
  checkSettingsView,
  type CheckScope,
  type CheckSettingsView,
} from './checkSettingsModel'
import { useCheckSettings } from './useCheckSettings'

const props = withDefaults(
  defineProps<{
    target: InspectorTarget
    /** A fixture view (preview only); the real view comes from the card's settings. */
    view?: CheckSettingsView | null
  }>(),
  { view: null },
)

const { t } = useI18n()

const k = (key: string) => `gui.inspector.checkSettings.${key}`

/** The fields of the 병합 대상 · 원격 row (their refusal icons sit at its end). */
const GIT_FIELDS = ['push', 'keep_branch'] as const

const card = useCheckSettings(() => (props.view ? null : props.target))

const shown = computed<CheckSettingsView>(
  () =>
    props.view ??
    checkSettingsView({
      card: props.target.nodeType !== null,
      node: card.node.value,
      pending: card.pending.value,
      branches: card.branches.value,
      readOnly: props.target.readOnly,
      placing: card.placing.value,
      disposed: card.disposed.value,
      failure: card.failure.value,
    }),
)

/** Why the card's settings could not be read (the icon's tooltip), while nothing was read; else null. */
const readFailure = computed(() => {
  const kind = props.view ? null : card.errorKind.value
  return kind && card.node.value === null ? t(k(checkSettingsErrorKey(kind))) : null
})

/** Nothing can change now: the controls are off, the reason their tooltip. */
const off = computed(() => props.view ? !props.view.connected : (shown.value.reason ?? null) !== null || Object.keys(card.pending.value).length > 0 || card.status.value !== 'ready')

const reasonText = computed(() => (off.value && shown.value.reason ? t(k(shown.value.reason)) : null))

const targetOptions = computed(() => shown.value.targetOptions.map(value => ({ value, label: value })))
const targetFailure = computed(() => failureOf('merge_target_branch') ?? (shown.value.targetError ? t(k(shown.value.targetError)) : null))
function onTarget(value: string | null) {
  if (shown.value.scope !== 'merge' || (value !== null && !shown.value.targetOptions.includes(value))) return
  onChange('merge_target_branch', value)
}

function scopeLabel(scope: CheckScope): string { return t(k(`git.${scope}`)) }

function failureOf(field: string): string | null {
  const key = shown.value.failures?.[field]
  return key ? t(k(key)) : null
}

function onChange(field: string, value: unknown) {
  if (off.value) return
  void card.change(field, value)
}

function reload() {
  void card.reload()
}
</script>

<style scoped>
.inspector-check-settings {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.inspector-check-settings-target-wrap { position: relative; width: 150px; }
.inspector-check-settings-target { width: 150px; }
.inspector-check-settings-target.has-missing-target { color: transparent; }
.inspector-check-settings-missing { position: absolute; left: 10px; right: 28px; top: 50%; transform: translateY(-50%); pointer-events: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.inspector-check-settings-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #94a8bd;
  font-size: 11.5px;
  white-space: nowrap;
}

</style>
