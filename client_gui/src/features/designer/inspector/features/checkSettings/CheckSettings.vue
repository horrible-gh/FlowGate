<template>
  <!-- flowgate.v02.0051 stage 2 — the 확인 card's settings tab (0035 v7 confirmSettings): the 용도 line with
       the v7 토론 중 tag, and the Git 반영 card — 반영 방식 (병합 / 커밋만 / 보류), 병합 대상 · 원격, 원격 반영,
       브랜치 보관 and the one-line summary. Built from the common parts. A thin container: checkSettingsModel
       gives the view. `view` may be handed in for a fixture preview.
       flowgate.v02.0053 T#7 — a Graph gate card keeps these values (T#2): every change is saved at once; none
       chosen = asked at the final approval as today. As v7: 보류 is marked ⛔ on a group-ending card and the
       block line shows when both are chosen. A refused change is a red ! beside its control; a v0.1 Flow's
       확인 Node keeps none (drawn off, the reason a tooltip). The summary reads the merge target and push shown.
       A failed first read is the read failure icon with its retry in place of the form (no guessed values).
       The 용도 line wraps: when its choice and tag do not fit beside the label (264px Inspector) they go
       under it, so both choices stay whole inside the tab. -->
  <div class="inspector-check-settings" data-test="inspector-check-settings" :data-connected="shown.connected ? 'true' : 'false'">
    <InspectorReadFailure v-if="readFailure" :reason="readFailure" @retry="reload" />
    <template v-else>
      <div class="inspector-check-settings-usage" data-test="check-settings-usage">
        <span class="inspector-check-settings-usage-label">{{ t(k('usage.label')) }}</span>
        <span class="inspector-check-settings-usage-value">
          <InspectorSegment
            :model-value="shown.usage"
            :options="CHECK_USAGES.map((usage) => ({ value: usage, label: t(k(`usage.${usage}`)), title: reasonText }))"
            :disabled="off"
            :aria-label="t(k('usage.label'))"
            data-test="check-settings-usage-value"
            @update:model-value="(value: string) => onChange('usage', value)"
          />
          <InspectorTbdNote kind="tag" :label="t(k('usage.tbd'))" :title="t(k('usage.tbd_title'))" />
          <InspectorFailureIcon v-if="failureOf('usage')" :name="t(k('failed'))" :reason="failureOf('usage')!" data-field="usage" />
        </span>
      </div>

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
          <Select
            class="inspector-check-settings-target"
            :model-value="shown.target ?? ''"
            :options="targetOptions"
            :disabled="off"
            data-test="check-settings-target"
            @update:model-value="(value: string) => onChange('merge_target', value === '' ? null : value)"
          />
          <label class="inspector-check-settings-toggle">
            <span>{{ t(k('git.push')) }}</span>
            <Toggle :model-value="shown.push ?? false" :disabled="off" :aria-label="t(k('git.push'))" data-test="check-settings-push" @update:model-value="(value: boolean) => onChange('push', value)" />
          </label>
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
        <p class="inspector-check-settings-summary" data-test="check-settings-summary">{{ summaryText }}</p>
        <p v-if="holdBlocked" class="inspector-check-settings-block" data-test="check-settings-hold-block">
          {{ t(k('git.hold_block')) }} <InspectorTbdNote kind="tag" :label="t(k('git.hold_block_tbd'))" />
        </p>
      </InspectorBlock>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Select from '../../../../../components/ui/Select.vue'
import Toggle from '../../../../../components/ui/Toggle.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorFailureIcon from '../../parts/InspectorFailureIcon.vue'
import InspectorReadFailure from '../../parts/InspectorReadFailure.vue'
import InspectorSegment from '../../parts/InspectorSegment.vue'
import InspectorTbdNote from '../../parts/InspectorTbdNote.vue'
import {
  CHECK_SCOPES,
  CHECK_USAGES,
  checkSettingsErrorKey,
  checkSettingsHoldBlocked,
  checkSettingsSummary,
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
const GIT_FIELDS = ['merge_target', 'push', 'keep_branch'] as const

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
const off = computed(() => (props.view ? !props.view.connected : (shown.value.reason ?? null) !== null))

const reasonText = computed(() => (off.value && shown.value.reason ? t(k(shown.value.reason)) : null))

/** No target first (`—`: asked at the final approval), then the branches. */
const targetOptions = computed(() => [{ value: '', label: '—' }, ...shown.value.targetOptions.map((branch) => ({ value: branch, label: branch }))])

const holdBlocked = computed(() => checkSettingsHoldBlocked(shown.value))

/** v7 scopeSummary of the values shown: the merge target and push as chosen (or not set yet). */
const summaryText = computed(() => {
  const summary = checkSettingsSummary(shown.value)
  const line = t(k(summary.key), summary.params)
  return summary.end ? t(k('git.summary_end'), { summary: line }) : line
})

/** v7: 보류 carries ⛔ on a group-ending card. */
function scopeLabel(scope: CheckScope): string {
  const label = t(k(`git.${scope}`))
  return scope === 'hold' && shown.value.usage === 'end' ? `${label} ⛔` : label
}

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

/* v7 `.usage-row`: the dashed "under discussion" line. The value goes under the label when it does not fit
   beside it (its tag wraps after the choice). */
.inspector-check-settings-usage {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: center;
  padding: 6px 11px;
  border: 1px dashed #6b5a2a;
  border-radius: 8px;
  background: #141208;
}

.inspector-check-settings-usage-label {
  flex: 0 0 100px;
  color: #d7c9a0;
  font-weight: 700;
}

.inspector-check-settings-usage-value {
  display: flex;
  flex: 1 1 auto;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #d9e5f0;
}

.inspector-check-settings-target {
  width: 150px;
}

.inspector-check-settings-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #94a8bd;
  font-size: 11.5px;
  white-space: nowrap;
}

.inspector-check-settings-summary {
  margin: 8px 0 0;
  color: #7f96af;
  font-size: 11.5px;
}

/* v7 `.git-block`: 보류 cannot end the group. */
.inspector-check-settings-block {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 6px 0 0;
  color: #ff9eae;
  font-size: 11.5px;
}
</style>
