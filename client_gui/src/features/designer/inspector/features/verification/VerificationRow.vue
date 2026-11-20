<template>
  <!-- flowgate.v02test.0053 1-D (mockup v15): the work card's `검증` line under its instruction · report lines, drawn only when a
       verification run is past its time (last launch + run time limit + 1 minute) or this card's request waits behind
       one. Name · state badge · buttons only; times and the card ahead are the badge's tooltip, what a button does is
       its own tooltip. [강제 종료] and [취소] turn into [확인] [취소] in place (no dialog). -->
  <InspectorSlotRow
    v-if="row"
    :name="t(k('name'))"
    :state="{ label: t(k(`state.${row.state}`)), tone: row.tone, sub: tooltip }"
    :danger="row.danger"
    :focus="row.focus"
    class="verification-row"
    :data-state="row.state"
    data-test="verification-row"
  >
    <template v-if="confirming">
      <button type="button" class="verification-btn is-danger" :disabled="busy" data-test="verification-confirm" @click="confirm">{{ t(k('confirm')) }}</button>
      <button type="button" class="verification-btn" :disabled="busy" data-test="verification-dismiss" @click="dismiss">{{ t(k('dismiss')) }}</button>
    </template>
    <template v-else>
      <template v-for="button in row.buttons" :key="button">
        <button
          v-if="button === 'go'"
          type="button"
          class="verification-go"
          :title="t(k('go'), { label: aheadLabel })"
          :aria-label="t(k('go'), { label: aheadLabel })"
          data-test="verification-go"
          @click="press('go')"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13 M13 6l6 6-6 6" /></svg>
        </button>
        <button
          v-else
          type="button"
          class="verification-btn is-danger"
          :title="t(k(`hint.${button}`))"
          :disabled="busy"
          :data-test="`verification-${button}`"
          @click="press(button)"
        >{{ t(k(`button.${button}`)) }}</button>
      </template>
    </template>
  </InspectorSlotRow>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import InspectorSlotRow from '../../parts/InspectorSlotRow.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useVerificationRow } from './useVerification'
import { verificationTimes } from './verificationModel'

const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.verification.${key}`

const { verification, row, confirming, busy, press, confirm, dismiss } = useVerificationRow(() => props.target)

const aheadLabel = computed(() => verification.value?.blocker?.label ?? '')

/** The badge tooltip: the card ahead (a waiting line), then launch time · elapsed or the end time. */
const tooltip = computed(() => {
  const current = verification.value
  if (!current) return null
  const parts: string[] = []
  if (current.state === 'waiting' && current.blocker) parts.push(t(k('ahead'), { label: current.blocker.label }))
  const times = verificationTimes(current)
  if (times) parts.push(times)
  return parts.length ? parts.join(' · ') : null
})
</script>

<style scoped>
.verification-btn {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 9px;
  border: 1px solid #2b4566;
  border-radius: 7px;
  background: #13263d;
  color: #dbe8f6;
  font: inherit;
  font-size: 11.5px;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
.verification-btn.is-danger { border-color: rgba(255, 100, 120, 0.45); background: transparent; color: #ff96a6; }
.verification-btn.is-danger:hover:not(:disabled) { background: rgba(255, 100, 120, 0.12); }
.verification-btn:hover:not(:disabled) { filter: brightness(1.15); }
.verification-btn:disabled { opacity: 0.55; cursor: default; }
.verification-btn:focus-visible,
.verification-go:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: 2px; }
.verification-go {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 24px;
  padding: 0;
  border: 1px solid #2d5f9b;
  border-radius: 6px;
  background: #10294a;
  color: #8cc0ff;
  cursor: pointer;
}
.verification-go:hover { filter: brightness(1.2); }
.verification-go svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
</style>
