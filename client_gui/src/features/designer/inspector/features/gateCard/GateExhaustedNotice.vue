<template>
  <!-- v02gui.0029 (0002-CH turn 35-36): on a 확인 card, the cards before it that came through with their review count
       spent — approved without passing a review. A name selects that card. Nothing shows when there is none. -->
  <div v-if="cards.length" class="inspector-gate-exhausted" role="note" data-test="gate-exhausted-notice">
    <p class="inspector-gate-exhausted-title" data-test="gate-exhausted-title"><span aria-hidden="true">⚠</span>{{ t(k('title'), { count: cards.length }) }}</p>
    <ul class="inspector-gate-exhausted-list">
      <li v-for="card in cards" :key="card.nodeId" data-test="gate-exhausted-card" :data-node-id="card.nodeId">
        <button type="button" class="inspector-gate-exhausted-go" data-test="gate-exhausted-open" @click="go(card.nodeId)">{{ nameOf(card) }}</button>
        <span class="inspector-gate-exhausted-detail" data-test="gate-exhausted-detail">{{ detailOf(card) }}</span>
      </li>
    </ul>
    <p class="inspector-gate-exhausted-hint">{{ t(k('hint')) }}</p>
  </div>
</template>

<script setup lang="ts">
import { inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { flowNodeKindView } from '../../../center/canvas/flowNodeKinds'
import { INSPECTOR_HOST } from '../../inspectorHost'
import type { InspectorExhaustedCard } from '../../inspectorTarget'

defineProps<{ cards: readonly InspectorExhaustedCard[] }>()

const { t } = useI18n()
const host = inject(INSPECTOR_HOST, null)
const k = (key: string) => `gui.inspector.gateCard.exhausted.${key}`

function nameOf(card: InspectorExhaustedCard): string {
  const kind = t(flowNodeKindView(card.kind, card.docType).labelKey)
  const title = card.title.trim()
  return title && title !== kind ? `${kind} · ${title}` : kind
}

function detailOf(card: InspectorExhaustedCard): string {
  if (card.reviewCount !== null && card.revisionNo !== null) return t(k('detail'), { count: card.reviewCount, revision: card.revisionNo })
  if (card.reviewCount !== null) return t(k('detail_count'), { count: card.reviewCount })
  return t(k('detail_unknown'))
}

function go(nodeId: string) {
  host?.request({ kind: 'select-node', nodeId })
}
</script>

<style scoped>
.inspector-gate-exhausted { margin-bottom: 10px; padding: 10px 14px; border: 1px solid var(--fg2-status-warning-border); border-radius: 9px; background: var(--fg2-status-warning-bg); color: var(--fg2-status-warning-fg); font-size: 12px; }
.inspector-gate-exhausted-title { display: flex; align-items: center; gap: 7px; margin: 0 0 6px; font-weight: 700; }
.inspector-gate-exhausted-list { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
.inspector-gate-exhausted-list li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; min-width: 0; }
.inspector-gate-exhausted-go { padding: 0; border: 0; background: transparent; color: inherit; font: inherit; font-weight: 700; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }
.inspector-gate-exhausted-detail { color: #d9c08a; }
.inspector-gate-exhausted-hint { margin: 6px 0 0; color: #c9b27c; font-size: 11.5px; }
</style>
