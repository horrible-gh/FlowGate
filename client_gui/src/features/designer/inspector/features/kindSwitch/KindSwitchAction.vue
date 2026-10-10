<template>
  <!-- flowgate.v02.0048 T#10 — the R↔B kind switch (D#1 §10.2 종류 전환 row, D#2 §3.4): on an unassigned
       Flow's requirement / bug root, the root turns into the other kind after a confirmation (the doc_id
       changes). A thin container: useKindSwitch reads and calls, kindSwitchModel decides what is shown
       and usable. A refused switch shows why and the state is read again.
       flowgate.v02.0051 stage 2 — drawn as the v7 header 종류 배지 ▾ (0035 insp.js kindBadge): the badge
       itself is the trigger, its menu lists 📄 요구사항 / 🐞 버그 with ✓ on the current kind, and picking
       the other opens the confirmation. A switch that is not usable now shows the locked badge with the
       reason as its hover text (v7 `.kind-badge.locked`). -->
  <div
    v-if="availability.switch.visible && to"
    class="inspector-kind-switch"
    role="group"
    :aria-label="t(k('label'))"
    data-test="inspector-kind-switch"
    :data-busy="busy ? 'true' : 'false'"
  >
    <Dropdown>
      <template #trigger="{ toggle, triggerAttrs }">
        <button
          type="button"
          aria-haspopup="menu"
          :aria-expanded="triggerAttrs['aria-expanded'] === 'true'"
          class="inspector-kind-badge"
          :class="{ 'is-locked': !availability.switch.enabled, 'is-busy': busy }"
          :disabled="!availability.switch.enabled"
          :title="badgeTitle"
          data-test="kind-switch-open"
          :data-to="to"
          :data-kind="from ?? ''"
          @click="toggle"
        >{{ names.from }} <span class="inspector-kind-badge-caret" aria-hidden="true">{{ availability.switch.enabled ? '▾' : '🔒' }}</span></button>
      </template>
      <template #default="{ close }">
        <div class="inspector-kind-switch-menu" data-test="kind-switch-menu">
          <p class="inspector-kind-switch-caption">{{ t(k('menu_title')) }}</p>
          <button
            v-for="kind in KINDS"
            :key="kind"
            type="button"
            role="menuitemradio"
            class="inspector-kind-switch-item"
            :aria-checked="kind === from"
            :data-test="`kind-switch-item-${kind}`"
            @click="pick(kind, close)"
          >
            <span aria-hidden="true">{{ kind === 'B' ? '🐞' : '📄' }}</span>
            <span>{{ t(k(`kinds.${kind}`)) }}</span>
            <span v-if="kind === from" class="inspector-kind-switch-check" aria-hidden="true">✓</span>
          </button>
        </div>
      </template>
    </Dropdown>
    <span v-if="line" class="inspector-kind-switch-line" :title="line.text" data-test="kind-switch-line" :data-kind="line.kind">
      <InspectorReason :text="line.text" :tone="line.tone" />
    </span>

    <ConfirmDialog
      :open="confirmOpen"
      :title="t(k('confirm.title'))"
      :message="confirmMessage"
      :confirm-label="t(k('confirm.submit'))"
      :cancel-label="t(k('confirm.cancel'))"
      @confirm="submit"
      @cancel="closeConfirm"
    />
  </div>
  <span v-else class="inspector-kind-badge is-static" data-test="designer-inspector-kind">{{ names.from }}</span>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ConfirmDialog from '../../../../../components/ui/ConfirmDialog.vue'
import Dropdown from '../../../../../components/ui/Dropdown.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import type { KindSwitchKind } from './kindSwitchModel'
import { useKindSwitch } from './useKindSwitch'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.kindSwitch.${key}`

const KINDS: readonly KindSwitchKind[] = ['R', 'B']

const { availability, to, busy, failureKey, done, run, clearFailure } = useKindSwitch(() => props.target)

/** The current kind: the one the switch leaves; the document type when there is no switch. */
const from = computed<KindSwitchKind | null>(() => {
  if (to.value === 'B') return 'R'
  if (to.value === 'R') return 'B'
  const type = props.target.docType?.trim().toUpperCase()
  return type === 'B' ? 'B' : type === 'R' ? 'R' : null
})

/** The kind names the strings use ({from} / {to}). */
const names = computed(() => ({
  from: from.value ? t(k(`kinds.${from.value}`)) : '',
  to: to.value ? t(k(`kinds.${to.value}`)) : '',
}))

/** `…0001-R` -> `0001-R`: the document number the switch changes. */
const codeOf = (docId: string | null) => (docId ?? '').split('.').pop() ?? ''

const confirmMessage = computed(() => {
  const fromCode = codeOf(props.target.docId)
  const toCode = to.value ? fromCode.replace(/-[RB]$/i, `-${to.value}`) : fromCode
  return t(k('confirm.message'), { ...names.value, from_code: fromCode, to_code: toCode })
})

/** Hover text of the badge: the disabled reason, else the v7 hint ("종류 전환: 그룹의 첫 실행 전까지"). */
const badgeTitle = computed(() => {
  const item = availability.value.switch
  if (!item.enabled && item.reason) return t(k(item.reason))
  return t(k('badge_hint'))
})

/** One line beside the badge: a refusal, else the finished switch (the disabled reason is the badge's hover text). */
const line = computed(() => {
  if (failureKey.value) return { kind: 'failure', tone: 'danger' as const, text: t(k(failureKey.value)) }
  if (done.value) {
    const kind = done.value.document.typeCode === 'B' ? 'B' : 'R'
    return { kind: 'done', tone: 'muted' as const, text: t(k('done'), { to: t(k(`kinds.${kind}`)), doc: codeOf(done.value.document.docId) }) }
  }
  return null
})

const confirmOpen = ref(false)

function pick(kind: KindSwitchKind, close: () => void) {
  close()
  if (kind === from.value || kind !== to.value) return
  openConfirm()
}

function openConfirm() {
  if (!availability.value.switch.enabled) return
  clearFailure()
  confirmOpen.value = true
}

function closeConfirm() {
  confirmOpen.value = false
}

async function submit() {
  confirmOpen.value = false
  await run()
}

// Another Node: its confirmation does not follow.
watch(
  () => props.target.nodeId,
  () => {
    confirmOpen.value = false
  },
)
</script>

<style scoped>
.inspector-kind-switch {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

/* v7 `.kind-badge` (the header's static badge, as a button); the accent comes from the header. */
.inspector-kind-badge {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--inspector-kind-accent, #3b82f6) 55%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--inspector-kind-accent, #3b82f6) 16%, #0b1827);
  color: #e6f0fb;
  font-size: 11.5px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
}

.inspector-kind-badge:hover:not(:disabled) {
  background: color-mix(in srgb, var(--inspector-kind-accent, #3b82f6) 28%, #0b1827);
}

.inspector-kind-badge.is-locked {
  cursor: help;
}

.inspector-kind-badge.is-static {
  cursor: default;
}

.inspector-kind-badge-caret {
  font-size: 9px;
  opacity: 0.85;
}

/* The menu (teleported with the Dropdown; this scope still applies). */
.inspector-kind-switch-menu {
  display: flex;
  flex-direction: column;
  min-width: 180px;
}

.inspector-kind-switch-caption {
  margin: 0 0 4px;
  padding: 2px 10px;
  color: #7f96af;
  font-size: 10.5px;
}

.inspector-kind-switch-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #c3d3e3;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.inspector-kind-switch-item:hover {
  background: #15345a;
}

.inspector-kind-switch-check {
  margin-left: auto;
  color: #72e4b8;
}

.inspector-kind-switch-line {
  display: block;
  max-width: 280px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.inspector-kind-switch-line :deep(.inspector-reason) {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
