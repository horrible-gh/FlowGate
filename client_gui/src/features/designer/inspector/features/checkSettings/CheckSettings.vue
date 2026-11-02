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
          <div class="inspector-check-settings-target-group">
          <div class="inspector-check-settings-target-wrap">
            <div class="inspector-check-settings-target-control">
            <Select
              class="inspector-check-settings-target"
            :class="{ 'has-missing-target': missingTarget }"
            :model-value="shown.target ?? ''"
            :options="targetOptions"
            :disabled="off || !shown.targetEnabled"
            :invalid="!!targetFailure"
            :aria-label="t(k('git.target'))"
            :aria-invalid="!!targetFailure"
            data-test="check-settings-target"
            @update:model-value="(value: string) => onTarget(value)"
          />
          <span v-if="missingTarget && shown.target" class="inspector-check-settings-missing" data-test="check-settings-target-display">{{ shown.target }}</span>
            </div>
          <span v-if="targetFailure" class="inspector-check-settings-target-help" role="alert" data-test="check-settings-target-error">{{ targetFailure }}</span>
          <span v-else-if="shown.lookupError" class="inspector-check-settings-target-help" role="status" data-test="check-settings-target-state">{{ t(k(shown.lookupError)) }}</span>
          </div>
          <button v-if="shown.canRefreshTargets" type="button" class="inspector-check-settings-refresh"
            :disabled="off || refreshing" :aria-label="t(k('target.refresh'))" :title="t(k('target.refresh'))"
            :aria-busy="refreshing ? 'true' : 'false'" data-test="check-settings-refresh" @click="reload">
            <svg viewBox="0 0 24 24" :class="{ 'is-spinning': refreshing }" aria-hidden="true">
              <path d="M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-1l2 6 M4 12l2 6a7 7 0 0 0 12-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
          </div>
          <label class="inspector-check-settings-toggle">
            <span>{{ t(k('git.push')) }}</span>
            <Toggle :model-value="shown.push ?? false" :disabled="off" :aria-label="t(k('git.push'))" data-test="check-settings-push" @update:model-value="(value: boolean) => onChange('push', value)" />
          </label>
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

const refreshing = computed(() => !props.view && card.status.value === 'loading')

const reasonText = computed(() => (off.value && shown.value.reason ? t(k(shown.value.reason)) : null))

const targetOptions = computed(() => shown.value.targetOptions.map(value => ({ value, label: value })))
/** The shown name is no option (unavailable, or nothing to show): it is drawn over the control, never added to it. */
const missingTarget = computed(() => !shown.value.targetOptions.includes(shown.value.target ?? ''))
const targetFailure = computed(() => failureOf('merge_target_branch') ?? (shown.value.targetError ? t(k(shown.value.targetError)) : null))
function onTarget(value: string) {
  if (shown.value.scope !== 'merge' || !shown.value.targetOptions.includes(value)) return
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
  if (refreshing.value) return
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

.inspector-check-settings-target-group { display: flex; align-items: flex-start; gap: 8px; min-width: 0; max-width: 100%; }
.inspector-check-settings-target-wrap { flex: 0 1 150px; width: 150px; min-width: 0; }
.inspector-check-settings-target-control { position: relative; }
.inspector-check-settings-target { width: 100%; }
.inspector-check-settings-target-help { display: block; margin-top: 3px; color: #ef737c; font-size: 11px; line-height: 1.35; overflow-wrap: anywhere; }
.inspector-check-settings-refresh { display: inline-grid; place-items: center; flex: 0 0 20px; margin-top: 4px; width: 20px; height: 20px; padding: 2px; border: 0; border-radius: 4px; background: transparent; color: #94a8bd; cursor: pointer; }
.inspector-check-settings-refresh:disabled { opacity: .5; cursor: default; }
.inspector-check-settings-refresh:focus-visible { outline: 2px solid #7cb7ff; outline-offset: 2px; }
.inspector-check-settings-refresh svg { width: 16px; height: 16px; }
.is-spinning { animation: target-refresh-spin 1s linear infinite; }
@keyframes target-refresh-spin { to { transform: rotate(360deg); } }
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
