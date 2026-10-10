<template>
  <!-- flowgate.v02.0051 stage 2 — the 테스트 card's own settings (0035 v7 testSettings): 지시 (TS) 사용 /
       생략, 검수 뒤 진행, and the 실행 block (per-Case execution mode with the automated / manual /
       external counts, and the v7 "manual · external 흐름 그리지 않음" tag). Built from the common parts.
       A thin container: testRunSettingsModel gives the view; nothing is stored on the Node yet, so the
       controls are disabled with the reason and the counts show "—" (0051-R §2.3). `view` may be handed
       in for a fixture preview. -->
  <div class="inspector-test-run-settings" data-test="inspector-test-run-settings" :data-locked="shown.locked ? 'true' : 'false'" :data-connected="shown.connected ? 'true' : 'false'">
    <InspectorLockNote v-if="shown.locked" :text="t(k('locked'))" />
    <InspectorBlock :locked="shown.locked" data-test="test-run-instruction">
      <InspectorControlRow :label="t(k('instruction.label'))" :hint="t(k('instruction.hint'))">
        <InspectorSegment
          :model-value="shown.instruction"
          :options="[
            { value: 'use', label: t(k('instruction.use')), title: shown.connected ? null : t(k('unconnected')) },
            { value: 'omit', label: t(k('instruction.omit')), title: shown.connected ? null : t(k('unconnected')) },
          ]"
          :disabled="!shown.connected || shown.locked"
          :aria-label="t(k('instruction.label'))"
          data-test="test-run-instruction-switch"
        />
      </InspectorControlRow>
    </InspectorBlock>
    <InspectorBlock :locked="shown.locked" data-test="test-run-policy">
      <InspectorControlRow :label="t(k('policy.label'))" :hint="t(k('policy.hint'))">
        <InspectorSegment
          :model-value="shown.policy"
          :options="[
            { value: 'follow', label: t(k('policy.follow')), title: shown.connected ? null : t(k('unconnected')) },
            { value: 'human', label: t(k('policy.human')), title: shown.connected ? null : t(k('unconnected')) },
          ]"
          :disabled="!shown.connected || shown.locked"
          :aria-label="t(k('policy.label'))"
          data-test="test-run-policy-switch"
        />
        <span v-if="shown.policy" class="inspector-test-run-settings-muted">{{ t(k(shown.policy === 'human' ? 'policy.note_human' : 'policy.note_follow')) }}</span>
      </InspectorControlRow>
    </InspectorBlock>
    <InspectorBlock :title="t(k('run.title'))" :sub="t(k('run.sub'))" data-test="test-run-execution">
      <template #head>
        <InspectorTbdNote kind="tag" :label="t(k('run.tbd'))" :title="t(k('run.tbd_title'))" />
      </template>
      <InspectorControlRow :label="t(k('run.mode'))" :hint="t(k('run.mode_hint'))">
        <span class="inspector-test-run-settings-modes" data-test="test-run-modes">
          <b>{{ t(k('run.per_case')) }}</b> — {{ t(k('run.decided_in_cases')) }} ·
          <span v-for="mode in MODES" :key="mode" class="inspector-test-run-settings-mode" :class="`inspector-test-run-settings-mode--${mode}`" :data-test="`test-run-mode-${mode}`">{{
            t(k(`run.${mode}`), { n: shown.modes[mode] ?? t(k('run.unknown')) })
          }}</span>
        </span>
      </InspectorControlRow>
    </InspectorBlock>
    <InspectorReason v-if="!shown.connected" :text="t(k('unconnected'))" data-test="test-run-settings-unconnected" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorControlRow from '../../parts/InspectorControlRow.vue'
import InspectorLockNote from '../../parts/InspectorLockNote.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorSegment from '../../parts/InspectorSegment.vue'
import InspectorTbdNote from '../../parts/InspectorTbdNote.vue'
import { testRunSettingsView, type TestRunSettingsView } from './testRunSettingsModel'

const props = withDefaults(
  defineProps<{
    target: InspectorTarget
    /** A fixture view (preview only); the real view comes from the target. */
    view?: TestRunSettingsView | null
  }>(),
  { view: null },
)

const { t } = useI18n()

const k = (key: string) => `gui.inspector.testRunSettings.${key}`

const MODES = ['automated', 'manual', 'external'] as const

const shown = computed(() => props.view ?? testRunSettingsView(props.target))
</script>

<style scoped>
.inspector-test-run-settings {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.inspector-test-run-settings-muted {
  color: #7f96af;
  font-size: 11px;
}

/* v7 `.case-modes`. */
.inspector-test-run-settings-modes {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  color: #9fb3c9;
  font-size: 11.5px;
}

.inspector-test-run-settings-modes b {
  color: #e3eef9;
}

.inspector-test-run-settings-mode {
  padding: 0 7px;
  border: 1px solid #2c4560;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 700;
}

.inspector-test-run-settings-mode--automated {
  border-color: #2d5f9b;
  color: #8cc0ff;
}

.inspector-test-run-settings-mode--manual {
  border-color: #7b5a17;
  color: #ffc75d;
}

.inspector-test-run-settings-mode--external {
  border-color: #315271;
  color: #9fc4e8;
}
</style>
