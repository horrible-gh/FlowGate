// Shared MainPanel mount helper (flowgate.default.0394 T0004, NR0003 §13-3 / §9.2).
//
// MainPanel gained an async gate: `v-if="aiRunBootstrapPending"` renders a loading
// card, and the whole document branch sits in the trailing `v-else`. The flag comes
// from the aiInvokeRuns store (`bootstrapPending = ref(true)`) and only drops when
// `bootstrap()` — kicked off in MainPanel's own onMounted — resolves.
//
// `await wrapper.vm.$nextTick()` does NOT wait for that: it flushes the render queue,
// not the pending request. Eleven specs across five files did exactly that, so
// DocHeader / ReviewActionBar / the action bar were never mounted and the assertions
// looked at an empty branch. Worse, some SIBLING cases kept passing for the same
// reason — a "no bar is rendered" expectation is trivially true when nothing is
// (NR0003 §5.1).
//
// So the fix belongs in the mount, not in each assertion. Everything that mounts
// MainPanel goes through here and gets the settled tree. The bootstrap request is
// whatever the spec's own `@shared/api` mock returns; the helper only waits for it.
//
// It also owns the child-stub list, which those five files were each carrying as a
// 13-14 entry copy. Pass `stubs` to add to it, or `{ DocHeader: false }` to opt a
// child back into real rendering.

import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore, type Tab } from '@main/stores/tabs'

/** Children MainPanel mounts that no MainPanel-level test needs the real version of. */
export const MAIN_PANEL_STUBS: Record<string, unknown> = {
  TabBar: true,
  DocHeader: true,
  DocWorkflow: true,
  MdViewer: true,
  TextViewer: true,
  DocInfoPanel: true,
  ReviewActionBar: true,
  ReviewRejectDialog: true,
  DesignHandoffDialog: true,
  NextActionModal: true,
  NextEmptyDocModal: true,
  CommandSelectorModal: true,
  QTDetailViewer: true,
  NewQModal: true,
  AiInvokeInline: true,
  GitFinalizePanel: true,
  ConfirmModal: true,
  TimeMachineDialog: true,
  MentionAddModal: true,
  ClipboardFallbackModal: true,
}

export interface MountMainPanelOptions {
  /** Tabs to seed into the tabs store before mounting. */
  tabs?: Tab[]
  /** Active tab id; defaults to the first seeded tab. */
  activeTabId?: string
  /** Added to (and overriding) MAIN_PANEL_STUBS. `false` un-stubs a child. */
  stubs?: Record<string, unknown>
  /** Deep mount instead of shallowMount — only when a real child is under test. */
  deep?: boolean
  attachTo?: Element | string
  plugins?: unknown[]
}

/**
 * Mount MainPanel and wait until the active-run bootstrap has settled.
 *
 * Always await this: on return the document branch is rendered, so `findComponent`
 * and DOM assertions see the same tree a user would.
 */
export async function mountMainPanel(options: MountMainPanelOptions = {}) {
  const { tabs, activeTabId, stubs, deep, attachTo, plugins } = options

  if (tabs) {
    const store = useTabsStore()
    store.tabs = tabs
    store.activeTabId = activeTabId ?? tabs[0]?.id ?? null
  } else if (activeTabId !== undefined) {
    useTabsStore().activeTabId = activeTabId
  }

  const wrapper = (deep ? mount : shallowMount)(MainPanel, {
    ...(attachTo ? { attachTo } : {}),
    global: {
      plugins: [i18n, ...(plugins ?? [])],
      stubs: { ...MAIN_PANEL_STUBS, ...(stubs ?? {}) },
    },
  } as any)

  // Settles MainPanel's onMounted bootstrap() — see the header note.
  await flushPromises()
  return wrapper
}

/**
 * Assert the bootstrap gate is actually open.
 *
 * The gate fails closed, so a spec that never waits sees an empty document branch and
 * can pass an "X is absent" expectation for the wrong reason. Call this before any
 * such negative assertion to prove the branch was mounted at all.
 */
export function expectDocumentBranchMounted(wrapper: { html: () => string }): void {
  if (wrapper.html().includes('ai-run-bootstrap-pending')) {
    throw new Error(
      'MainPanel is still showing the bootstrap placeholder — the document branch was '
      + 'never mounted, so any assertion about its contents is meaningless. Mount via '
      + 'mountMainPanel() (which awaits the bootstrap) instead of a bare $nextTick.',
    )
  }
}
