<template>
  <!-- flowgate.v02.0036 — bottom Inspector Frame (0035 v7): the common frame every Node shares.
       No Node selected: an empty area — no text (flowgate.v02.0042), header or tabs. A Node
       selected: header (40px) + tabs (32px) fixed above a scrolling Content area — 72px before
       the content.
       flowgate.v02.0039 T#3: `unselected` (the Designer has no work target yet) leaves only the
       bottom frame — an empty header bar, no line, no tabs and no Content.
       flowgate.v02.0048 T#1 (D#1 §5): the one injection point of the Inspector features. The
       Frame turns the selection into an InspectorTarget and lays out registry contributions
       for the header and tab Content in `order`. The Frame knows no feature and calls no API;
       a feature's Host intent leaves as the `host` event.
       flowgate.v02.0048 T#3 (D#1 §7.3): the header follows the target. When the target document's
       review status, an AI run of its Flow or its documents change on the server, the Frame asks
       for the Flow to be read again quietly (`refresh-flow`); the new selection then gives the
       header and the Canvas card the same values. The Frame keeps no copy of the server state.
       flowgate.v02.0051 stage 2: two more header places from the registry — the kind badge part
       (`headerKind`, the first that applies) and the ⋯ menu items (`headerMenu`, in `order`).
       flowgate.v02.0051 stage 3: every selected Node keeps the four v7 tabs. Missing data
       receives an empty state and unconnected settings a disabled state. Card slots lead the
       document tab; the card slot opens the existing document screen for full detail.
       flowgate.v02.0053 T#6: the body element is provided to the tab parts (INSPECTOR_BODY) so a part can
       take input over the whole tab (the attachments file drop); a `fill` tab's section takes the body's
       free height.
       flowgate.v02.0053 T#9: one more header place — the ✎ 메모 part (`headerMemo`, the first that applies);
       without one the header keeps its dim, disabled ✎ 메모.
       flowgate.v02.0070 T#1 (B1): the host also carries the card title save (`save-title`, the Center column's) — the
       header edits the card's title in its title area and saves it through the host. -->
  <section
    class="designer-inspector"
    data-test="designer-inspector"
    :data-selected="target && !unselected ? 'true' : 'false'"
    :data-unselected="unselected ? 'true' : 'false'"
    :aria-label="t('gui.designer.inspector.label')"
  >
    <div v-if="unselected" class="designer-inspector-frame" data-test="designer-inspector-frame" />
    <div v-else-if="!target" class="designer-inspector-empty" data-test="designer-inspector-empty" />
    <template v-else>
      <InspectorHeader :target="target">
        <template v-if="headerKind" #kind>
          <component :is="headerKind.headerKind" :key="headerKind.key" :target="target" />
        </template>
        <template v-if="headerMemo" #memo>
          <component :is="headerMemo.headerMemo" :key="headerMemo.key" :target="target" />
        </template>
        <template v-if="headerMetas.length" #meta>
          <component :is="entry.headerMeta" v-for="entry in headerMetas" :key="entry.key" :target="target" />
        </template>
        <template v-if="headerActions.length" #actions>
          <component :is="entry.headerAction" v-for="entry in headerActions" :key="entry.key" :target="target" />
        </template>
        <template v-if="headerMenus.length" #menu="{ close }">
          <component :is="entry.headerMenu" v-for="entry in headerMenus" :key="entry.key" :target="target" :close="close" />
        </template>
      </InspectorHeader>
      <template v-if="tabs.length">
        <Tabs
          class="designer-inspector-tabs"
          data-test="designer-inspector-tabs"
          :style="tabStripStyle"
          :tabs="tabItems"
          :model-value="activeTab"
          :aria-label="t('gui.designer.inspector.tabs_label')"
          @update:model-value="selectTab"
        />
        <div ref="bodyElement" class="designer-inspector-body" role="tabpanel" data-test="designer-inspector-body" :data-tab="activeTab">
          <div
            v-for="entry in tabContent"
            :key="entry.key"
            class="designer-inspector-section"
            :class="{ 'is-fill': entry.tab!.fill }"
            data-test="designer-inspector-section"
            :data-key="entry.key"
            :data-fill="entry.tab!.fill ? 'true' : undefined"
          >
            <component :is="entry.tab!.component" :target="target" />
          </div>
          <div
            v-if="!tabContent.length"
            class="designer-inspector-tab-state"
            data-test="designer-inspector-tab-state"
            :data-state="activeTab === 'settings' ? 'disabled' : 'empty'"
          >
            <EmptyState v-if="emptyTabMessage" compact :title="emptyTabMessage" />
          </div>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref, toRaw, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import EmptyState from '../../../components/ui/EmptyState.vue'
import Tabs, { type TabItem } from '../../../components/ui/Tabs.vue'
import type { FlowGraphSelection } from '../center/graph/flowGraph'
import InspectorHeader from './InspectorHeader.vue'
import { INSPECTOR_BODY, INSPECTOR_HOST, type InspectorHostIntent } from './inspectorHost'
import {
  contributionsFor,
  inspectorRegistry,
  installInspectorMessages,
  registrationApplies,
  type InspectorRegistry,
} from './inspectorRegistry'
import {
  DEFAULT_INSPECTOR_TAB,
  INSPECTOR_TABS,
  INSPECTOR_TAB_GAP,
  INSPECTOR_TAB_MIN_WIDTH,
  INSPECTOR_TAB_STRIP_PADDING_X,
   resolveInspectorTab,
  type InspectorTabKey,
} from './inspectorTabs'
import { toInspectorTarget, withWrittenCardLines, type NodeTitleSaveOutcome } from './inspectorTarget'
import { cardLinesWritten, settleCardLinesWritten } from './openDocument'
import { useInspectorEvents } from './useInspectorResource'

const props = withDefaults(
  defineProps<{
    /** The selected Node with its Flow and project (Center session), or null when nothing is selected. */
    selection: FlowGraphSelection | null
    /** The Designer has no work target yet (0039 T#3): frame only. */
    unselected?: boolean
    /** The feature registry; tests swap in a fixture registry. */
    registry?: InspectorRegistry
    /** flowgate.v02.0070 T#1: saves a card's own title (the session's); null = no title edit opens. */
    saveTitle?: ((flowId: string, nodeKey: string, raw: string) => Promise<NodeTitleSaveOutcome>) | null
  }>(),
  { unselected: false, registry: () => inspectorRegistry, saveTitle: null },
)

const emit = defineEmits<{ host: [intent: InspectorHostIntent] }>()

// The global composer: feature strings are merged into it before any feature part renders.
const i18n = useI18n({ useScope: 'global' })
const { t } = i18n
// The registry is fixed for the Frame's life (the static collection, or a test fixture); its
// parts are rendered as given, never through a reactive proxy.
const registry = toRaw(props.registry)
installInspectorMessages(i18n, registry)

provide(INSPECTOR_HOST, {
  request: (intent) => emit('host', intent),
  get saveTitle() {
    return props.saveTitle ?? undefined
  },
})

// flowgate.v02.0053 T#6: the tab Content element, for parts that take input over the whole tab.
const bodyElement = ref<HTMLElement | null>(null)
provide(INSPECTOR_BODY, { element: bodyElement })

// flowgate.v02.0056 F2-2: a card line written from here ([작성]) stands for the card's line until the Canvas read
// shows its document; every read shown settles them (and opens a document a refused [작성] waits for).
const readTarget = computed(() => toInspectorTarget(props.selection))
const target = computed(() => withWrittenCardLines(readTarget.value, cardLinesWritten(readTarget.value)))
watch(readTarget, (current) => settleCardLinesWritten(current), { immediate: true })

/** Server events after which the header (the selected Flow's Canvas read) may be out of date. */
const HEADER_REFRESH_EVENTS = ['doc_review_status_changed', 'ai_invoke_finished', 'document_explorer_refresh'] as const

// A reconnect is left to the Canvas, which reads every open Flow again on its own.
useInspectorEvents(() => (props.unselected ? null : target.value), {
  events: HEADER_REFRESH_EVENTS,
  resync: false,
  changed: () => emit('host', { kind: 'refresh-flow' }),
})

// A local document save sends no server event. Read the selected card's stored lines again,
// including an instruction whose document differs from the Node's bound result document.
function onDocumentContentChanged(event: Event): void {
  const docId = (event as CustomEvent<{ doc_id?: string | null }>).detail?.doc_id
  const current = props.unselected ? null : target.value
  if (!docId || !current) return
  if (current.docId !== docId && !current.cardDocuments.some((line) => line.docId === docId)) return
  emit('host', { kind: 'refresh-flow', flowId: current.flowId })
}

onMounted(() => window.addEventListener('fg:document_content_changed', onDocumentContentChanged))
onBeforeUnmount(() => window.removeEventListener('fg:document_content_changed', onDocumentContentChanged))

const contributions = computed(() => contributionsFor(registry, target.value))
const headerMetas = computed(() => contributions.value.filter((entry) => entry.headerMeta))
const headerActions = computed(() => contributions.value.filter((entry) => entry.headerAction))
const headerKind = computed(() => contributions.value.find((entry) => entry.headerKind) ?? null)
const headerMenus = computed(() => contributions.value.filter((entry) => entry.headerMenu))
const headerMemo = computed(() => contributions.value.find((entry) => entry.headerMemo) ?? null)

const tabs = computed(() => target.value && !props.unselected ? INSPECTOR_TABS : [])

// One tab number supplier per registration, set up once; each sees the target only while its
// registration applies to it (D#1 §5.2: at most one supplier per tab).
const tabCounts = registry.entries.flatMap((entry) => {
  if (!entry.tab?.count) return []
  const scoped = computed(() => (target.value && registrationApplies(entry, target.value) ? target.value : null))
  return [{ entry, tab: entry.tab.key, count: entry.tab.count(scoped) }]
})

function tabCount(key: InspectorTabKey): number | null {
  const supplier = tabCounts.find((item) => item.tab === key && contributions.value.includes(item.entry))
  return supplier?.count.value ?? null
}

const tabItems = computed<TabItem[]>(() =>
  tabs.value.map((key) => {
    const label = t(`gui.designer.inspector.tabs.${key}`)
    const count = tabCount(key)
    return { key, label: count === null ? label : `${label} (${count})` }
  }),
)

// Kept across Node changes and while nothing is selected (v7).
const selectedTab = ref<InspectorTabKey>(DEFAULT_INSPECTOR_TAB)

const activeTab = computed(() => resolveInspectorTab(selectedTab.value, tabs.value))

const tabContent = computed(() => {
  const entries = contributions.value.filter((entry) => entry.tab?.key === activeTab.value)
  // Card slots already open the bound document. Do not repeat its v0.1 metadata beneath them.
  return activeTab.value === 'documents' && entries.some((entry) => entry.key !== 'documents')
    ? entries.filter((entry) => entry.key !== 'documents')
    : entries
})
const emptyTabMessage = computed(() => {
  // flowgate.v02.0053 T#6 (0053 CH): Q&A and attachments of a Node without a document are an empty
  // area — no "no linked document" sentence.
  if (!target.value?.docId && (activeTab.value === 'qna' || activeTab.value === 'attachments')) return ''
  // flowgate.v02.0053 T#7 (0053 CH §4): every card with settings has its form now (a Graph card, just placed
  // or done); a Node with none (a work plan card, a v0.1 document outside the sequence) keeps an empty
  // settings area — no "why not" sentence (the 0052 T#2 reasons, "v0.1 단계에 속하지 않은 Node" among them).
  if (activeTab.value === 'settings') return ''
  return t('gui.designer.inspector.pane.empty')
})

// Strip geometry comes from the tab rules so the Shell's minimum Inspector width and the CSS agree.
const tabStripStyle = {
  gap: `${INSPECTOR_TAB_GAP}px`,
  paddingLeft: `${INSPECTOR_TAB_STRIP_PADDING_X}px`,
  paddingRight: `${INSPECTOR_TAB_STRIP_PADDING_X}px`,
  '--inspector-tab-min-width': `${INSPECTOR_TAB_MIN_WIDTH}px`,
}

function selectTab(key: string) {
  selectedTab.value = resolveInspectorTab(key as InspectorTabKey, tabs.value)
}
</script>

<style scoped>
.designer-inspector {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: #091624;
}

.designer-inspector-frame {
  flex: 0 0 48px;
  height: 48px;
  border-bottom: 1px solid var(--fg2-line);
  background: #0c1b2d;
}

.designer-inspector-empty {
  flex: 1 1 auto;
}

/* The shared Tabs primitive, sized to the v7 32px tab row. Gap and side padding come from
   tabStripStyle. The strip never wraps or scrolls: on a narrow Inspector every tab shrinks to
   --inspector-tab-min-width and ellipsises its label, so all tabs stay visible and clickable. */
.designer-inspector-tabs {
  flex: 0 0 32px;
  flex-wrap: nowrap;
  align-items: flex-end;
  box-sizing: border-box;
  height: 32px;
  min-width: 0;
  overflow: hidden;
  background: #0a1828;
}

.designer-inspector-tabs :deep(.ui-tab) {
  flex: 0 1 auto;
  box-sizing: border-box;
  min-width: var(--inspector-tab-min-width);
  height: 28px;
  overflow: hidden;
  padding: 0 12px;
  border-radius: 5px 5px 0 0;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.designer-inspector-tabs :deep(.ui-tab:hover:not(:disabled)) {
  background: #10233b;
}

.designer-inspector-tabs :deep(.ui-tab.is-active) {
  background: #10243e;
}

.designer-inspector-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 12px 14px;
}

.designer-inspector-section + .designer-inspector-section {
  margin-top: 12px;
}

/* A `fill` tab (the attachments drop area): its part grows to the bottom of the body's content box. */
.designer-inspector-section.is-fill {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  min-height: 100%;
}

.designer-inspector-tab-state {
  min-width: 0;
}


.designer-inspector-body :deep(.inspector-work-plan-details),
.designer-inspector-body :deep(.inspector-test-spec-details),
.designer-inspector-body :deep(.inspector-check-details) {
  min-width: 0;
}

.designer-inspector-body :deep(.inspector-work-plan-details summary),
.designer-inspector-body :deep(.inspector-test-spec-details summary),
.designer-inspector-body :deep(.inspector-check-details summary) {
  color: var(--fg2-muted);
  font-size: 11.5px;
  cursor: pointer;
}

.designer-inspector-body :deep(.inspector-work-plan-details[open] summary),
.designer-inspector-body :deep(.inspector-test-spec-details[open] summary),
.designer-inspector-body :deep(.inspector-check-details[open] summary) {
  margin-bottom: 10px;
}
</style>
