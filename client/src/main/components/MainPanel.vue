<template>
  <main class="app-main">
    <TabBar @new-tab="showQuickOpen = true" />

    <div
      class="content-wrap"
      :class="{
        'has-sticky-footer': !aiRunDocumentLocked && activeTabId != null && getActionBarMode(activeTabId) != null,
        'content-wrap--text-preview': activeTab?.type === 'text',
        'content-wrap--conversation': activeTab?.typeCode === 'CH',
      }"
    >
      <!-- Document panels (one per open tab) -->
      <template v-if="activeTab">
        <div
          v-for="tab in tabs"
          :key="tab.id"
          role="tabpanel"
          :id="`panel-${tab.id}`"
          :aria-labelledby="`tab-${tab.id}`"
          class="content-panel"
          :class="{ active: tab.id === activeTabId }"
        >
          <div
            v-if="tab.id === activeTabId"
            :class="['doc-with-panel', { 'panel-collapsed': canShowDocInfoPanel(tab.id) && docInfoCollapsed }]"
          >
          <div class="doc-main">
          <!-- Fail closed only until active-run bootstrap completes. Afterwards the status
               card and the same document component instances coexist; write surfaces derive
               from aiRunDocumentLocked. A CH document's own run remains the one exception so
               ConversationView keeps its existing STOP-button path. -->
          <div v-if="aiRunBootstrapPending" class="ai-run-bootstrap-pending">
            {{ t('common.loading') }}
          </div>
          <template v-else>
          <AiInvokeInline
            v-if="activeGroupRunInlineVisible"
            :group-id="activeAiInvokeGroupId"
          />
          <!-- 0155: test-failure strip (confirmed design B) — self-hides unless the
               viewed doc's latest test run failed. Sits above DocHeader as the first
               child of .doc-main, per the confirmed layout. A failed run assembles no
               TSR, so this is the only in-context signal of the failure (R0001). -->
          <TestFailStrip
            v-if="!aiRunDocumentLocked"
            :test-run="exposedValue(docHeaderRefs[tab.id]?.testRun) ?? null"
            :doc-id="tab.id"
            @run-started="docHeaderRefs[tab.id]?.fetchDoc?.(tab.id)"
          />
          <!-- 0174 (NR0003): first-run entry point. TestRunStrip carries the run/delegate
               logic + i18n + unit test built for 0166, but it was never mounted in any real
               view — an orphan component, so an approved-yet-never-run TS still showed no run
               button (the "dead entrance" regression). Mount it here, right after TestFailStrip:
               the two are mutually exclusive (latest run failed -> Fail strip owns re-run; any
               other admissible state -> Run strip). Same testRun source as the fail strip;
               run-started re-fetches the doc so the embedded run/SSE state refreshes. -->
          <TestRunStrip
            v-if="!aiRunDocumentLocked"
            :type-code="getTabTypeCode(tab.id)"
            :review-status="exposedValue(docHeaderRefs[tab.id]?.docReviewStatus) ?? null"
            :test-run="exposedValue(docHeaderRefs[tab.id]?.testRun) ?? null"
            :group-disposed="exposedValue(docHeaderRefs[tab.id]?.groupDisposed) ?? false"
            :doc-loaded="exposedValue(docHeaderRefs[tab.id]?.docLoaded) ?? false"
            :doc-id="tab.id"
            @run-started="docHeaderRefs[tab.id]?.fetchDoc?.(tab.id)"
          />
          <!-- CH (conversation) is a normal workflow node: it shows the standard document
               header AND the workflow strip, exactly like every other doc. The chat surface
               (bubbles + composer + mention-copy) renders below in its own card. TR0044.0010 rev6
               — restores the header/workflow that rev5 wrongly hid: the thing the reviewer
               wanted gone was the redundant chat intro, never the document header itself
               ("where did you sell off the document header and workflow"). -->
          <!-- 0460 TR0005 rev6 — `headerEditModeVisible` used to sit in this OR: it only
               toggles which of the two textareas the raw-content edit modal shows (body-only
               vs full-content-with-frontmatter), but being ORed in here meant every click of
               that in-modal [헤더 수정]/[헤더 숨김] button also unmounted/remounted THIS
               background DocHeader — a same-tab remount fires a fresh non-silent fetchDoc(),
               which blanks the header (v-if="doc" renders nothing until the GET resolves)
               before it pops back in ("disappears then reappears"). The doc-open/doc-close
               unmount below is intentional and load-bearing (group 0064 NR0003's DocHeader
               ref-identity regression exercises exactly that same-tab unmount→remount) —
               only the extra churn from the in-modal toggle is removed. -->
          <DocHeader
            v-if="tab.typeCode && editTab?.id !== tab.id"
            :ref="(el) => bindActiveRef(docHeaderRefs, tab.id, el)"
            :tab="tab"
            :read-only="aiRunDocumentLocked"
            @related-doc-created="emit('related-doc-created', $event)"
            @doc-updated="onDocHeaderUpdated"
            @workflow-decided="onWorkflowDecided"
          />
          <DocWorkflow
            v-if="tab.typeCode && tab.typeCode !== 'DC'"
            :tab="tab"
            :read-only="aiRunDocumentLocked"
            :workflow-decided="getWorkflowViewState(tab.id).mode !== 'workflow'"
            :parent-r-doc-id="exposedValue(docHeaderRefs[tab.id]?.parentRDocId) ?? null"
            :step-states="getWorkflowViewState(tab.id).stepStates"
            :can-next-action="getWorkflowViewState(tab.id).canNextAction"
            :return-targets="getReturnTargets(tab.id)"
            :slot-commits="getSlotCommits(tab.id)"
            @sequence-updated="() => { docHeaderRefs[tab.id]?.fetchDoc?.(tab.id); workPlanEditorRefs[tab.id]?.fetchPlan?.() }"
            @decide-workflow="openWorkflowDecisionForActive"
            @next-action="onProceedNextStep(tab.id)"
            @time-machine="onWorkflowStepTimeMachine(tab.id, $event)"
            @return-to="onWorkflowStepReturn(tab.id, $event)"
            @create-work-plan="onCreateWorkPlan(tab.id, $event.docId)"
          />
          <!-- 0115: git finalize panel — self-hiding unless the group has a git
               worktree (status !== 'none'); shown on workflow roots only. -->
          <GitFinalizePanel
            v-if="!aiRunDocumentLocked && (tab.typeCode === 'R' || tab.typeCode === 'B')"
            :group-id="exposedValue(docHeaderRefs[tab.id]?.groupId) ?? ''"
            @open-archive="openGitArchive"
            @archived="onGitArchived"
          />
          <!-- 0060 R0001 / D0010 6-1 — attachment file card. The body column's card order is
               [document header → workflow → attachment files → document preview], and this spot
               sits directly **above** the following preview card series (AC / DC / CH / WP / md /
               text / diff v-if chain). The rejection ("맨 밑에 쳐박혀 있으니까 안보이잖아 문서 미리보기 위로 올려")
               pointed at this problem, and this position resolves it — in the approved mockup
               wdkcvrmk, the attachment card fits on the first screen without scrolling
               (TR0006 measured: attachment top 427px, document preview 674px).
               It cannot sit inside the chain itself — inserting a sibling between v-if / v-else-if
               breaks the chain. While an AI run is in progress it drops to read-only (no
               upload/delete, list/download only). -->
          <AttachmentCard
            v-if="tab.typeCode"
            :doc-id="tab.id"
            :read-only="aiRunDocumentLocked"
          />
          <!-- AC (final approval): file-less workflow step — no body file, so it
               must render by typeCode regardless of tab.type. When reopened from
               the tree the tab type resolves to 'unsupported' (no md), which would
               otherwise fall through to the unsupported-view. -->
          <template v-if="tab.typeCode === 'AC'">
            <!-- 0265 R0001: once the document is finally approved, the [Git Integration]
                 finalize panel rises ABOVE the [Final Approval] card — after approval
                 the merge/push is the remaining action, so it leads. Before
                 approval the approval card stays on top (approving is the primary
                 action) and the panel mounts below, as before. isCompletedDoc
                 (approved | wf_done) is the same signal the card body uses for its
                 done state, so the reorder tracks it exactly. The two mounts are
                 mutually exclusive (v-if / !v-if): only one panel exists at a time. -->
            <GitFinalizePanel
              v-if="!aiRunDocumentLocked && isCompletedDoc(tab.id)"
              :group-id="exposedValue(docHeaderRefs[tab.id]?.groupId) ?? ''"
              @open-archive="openGitArchive"
              @archived="onGitArchived"
            />
            <div class="card md-preview-card">
              <div class="card-hd">
                <span class="card-title">
                  <AppIcon name="clipboard-text" style="color:var(--text-m);" />
                  {{ t('main.review_action_bar.final_approval') }}
                </span>
              </div>
              <div class="card-bd ac-final-approval-body">
                <template v-if="isCompletedDoc(tab.id)">
                  <AppIcon name="check-circle" class="ac-fa-icon ac-fa-icon-done" />
                  <p class="ac-fa-title">{{ t('main.final_approval.panel_title_done') }}</p>
                  <p class="ac-fa-desc">{{ t('main.final_approval.panel_desc_done') }}</p>
                </template>
                <template v-else>
                  <AppIcon name="seal" class="ac-fa-icon" />
                  <p class="ac-fa-title">{{ t('main.final_approval.panel_title') }}</p>
                  <p class="ac-fa-desc">{{ t('main.final_approval.panel_desc') }}</p>
                </template>
              </div>
            </div>
            <!-- 0182 NR0003 §3: the git finalize UI used to live on the R/B root
                 only, forcing a detour back to the R document after final
                 approval. Mount the same self-hiding panel under the AC card so
                 merge/push happens right where the approval ended. group-id
                 resolves exactly like the R/B mount above.
                 0206 B0001: dropped the inline-conflicts prop — conflict
                 resolution no longer force-renders cramped inside the document
                 column. The panel now shows the status summary + a [conflict
                 resolve] button that opens the shared 1180×820 overlay resolver,
                 identical to the R/B root path.
                 0265 R0001: this below-the-card mount is now the PRE-approval
                 position only; the post-approval mount above leads instead. -->
            <GitFinalizePanel
              v-if="!aiRunDocumentLocked && !isCompletedDoc(tab.id)"
              :group-id="exposedValue(docHeaderRefs[tab.id]?.groupId) ?? ''"
              @open-archive="openGitArchive"
              @archived="onGitArchived"
            />
          </template>
          <!-- DC (group discard): file-less terminal record. Like AC it has no .md
               body, so it must render by typeCode (otherwise the tab type resolves to
               'unsupported' and shows the bogus "preview not supported" error —
               TR0029.0008 review r2 #2). It is terminal, not a review step: no action
               bar, no workflow strip, no info panel (review r2 #3, #4). -->
          <div v-else-if="tab.typeCode === 'DC'" class="card md-preview-card">
            <div class="card-hd">
              <span class="card-title">
                <AppIcon name="prohibit" style="color:var(--danger, #dc2626);" />
                {{ t('main.group_discard.panel_title') }}
              </span>
            </div>
            <div class="card-bd ac-final-approval-body">
              <AppIcon name="x-circle" class="ac-fa-icon" style="color:var(--danger, #dc2626);" />
              <p class="ac-fa-title">{{ t('main.group_discard.panel_title_done') }}</p>
              <p class="ac-fa-desc">{{ t('main.group_discard.panel_desc_done') }}</p>
            </div>
          </div>
          <!-- CH (conversation/chat): the body IS a chat log, so it renders as a
               conversation (bubbles + composer) instead of the plain MdViewer +
               workflow action bar. This is the TR0044.0010 rev2 fix — selecting CH
               now yields an actual conversation, not a generic document with
               [Proceed to next step]/[Create empty doc] buttons. -->
          <div v-else-if="tab.typeCode === 'CH'" class="card md-preview-card conv-card">
            <div class="card-hd">
              <span class="card-title">
                <span class="doc-tag c-CH" style="font-size:.68rem; padding:2px 5px; margin-right:4px;">CH</span>
                {{ t('main.conversation_view.title') }}
              </span>
              <div class="card-actions">
                <button class="btn btn-secondary btn-sm" type="button" @click="openFullView(tab)">
                  <AppIcon name="corners-out" /> {{ t('main.document_preview.full_view') }}
                </button>
              </div>
            </div>
            <div class="card-bd conv-card-bd">
              <!-- 0263 R0001: [full view] hands this very instance to the dialog through a
                   Teleport rather than mounting a second ConversationView there. A chat holds
                   live state the markup cannot re-create — an in-flight AI call's poll loop and
                   spinner, plus the unsent draft — so unmounting it to re-mount elsewhere is
                   exactly what dropped the progress in 0251 NR0003 §4. Moving the node keeps one
                   instance, and with it one source of truth, on both sides of the transition.
                   0251 B0001 still holds either way: nothing covers the chat while its own AI
                   call runs (AiInvokeInline suppresses this doc's run) — progress is the send
                   button. -->
              <Teleport :to="convFullViewHost" :disabled="!convFullViewOn">
                <ConversationView
                  :ref="(el) => bindActiveRef(convViewRefs, tab.id, el)"
                  :doc-id="tab.id"
                  :project-id="tab.projectId ?? null"
                  :manual-copy-text="convManualCopy[tab.id] ?? null"
                  :read-only="aiRunDocumentLocked && !activeChatOwnRun"
                  @copy-mention="(opts) => onConversationCopyMention(tab.id, opts)"
                  @manual-copy-dismiss="setConvManualCopy(tab.id, null)"
                />
              </Teleport>
            </div>
          </div>
          <!-- WP (work plan): the body is JSON, not Markdown — a dedicated table editor
               replaces MdViewer for this typeCode, the same way CH replaces it with a
               conversation view. flowgate.default.0395 D0007 §6.4. -->
          <WorkPlanEditor
            v-else-if="tab.typeCode === 'WP'"
            :ref="(el) => bindActiveRef(workPlanEditorRefs, tab.id, el)"
            :doc-id="tab.id"
            :project-id="tab.projectId ?? null"
            :read-only="aiRunDocumentLocked"
          />
          <div v-else-if="tab.type === 'qtui'" class="card md-preview-card">
            <div class="card-hd">
              <span class="card-title">
                <span class="doc-tag c-Q" style="font-size:.68rem; padding:2px 5px; margin-right:4px;">Q</span>
                {{ tab.title }}
              </span>
            </div>
            <div class="card-bd" style="padding:16px;">
              <QTDetailViewer :q-id="tab.id" :read-only="aiRunDocumentLocked" @status-changed="onQStatusChanged" />
            </div>
          </div>
          <div v-else-if="tab.type === 'md'" class="card md-preview-card">
            <!-- Q document: question item accordion -->
            <template v-if="tab.typeCode === 'Q'">
              <div class="card-hd">
                <span class="card-title">
                  <span class="doc-tag c-Q" style="font-size:.68rem; padding:2px 5px; margin-right:4px;">Q</span>
                  {{ tab.title }}
                </span>
              </div>
              <div class="card-bd" style="padding:16px;">
                <QTDetailViewer :q-id="tab.id" :read-only="aiRunDocumentLocked" @status-changed="onQStatusChanged" />
              </div>
            </template>

            <!-- Regular document: MDViewer -->
            <template v-else>
            <div class="card-hd">
              <span class="card-title">
                <AppIcon name="markdown-logo" style="color:var(--text-m);" />
                {{ t('main.document_preview.title') }}
              </span>
              <div class="card-actions">
                <div v-if="!aiRunDocumentLocked && canEditTab(tab)" class="edit-dropdown-wrap">
                  <button
                    class="btn btn-outline btn-sm"
                    type="button"
                    @click.stop="toggleEditDropdown(tab.id)"
                  >
                    <AppIcon name="pencil-simple" /> {{ t('main.document_preview.edit') }}
                    <AppIcon name="caret-down" class="edit-caret" />
                  </button>
                  <transition name="edit-dropdown">
                    <div v-if="editDropdownTabId === tab.id" class="edit-dropdown-menu" @click.stop>
                      <button class="edit-dropdown-item" type="button" @click="onEditDirect(tab)">
                        <AppIcon name="note-pencil" /> {{ t('main.main_panel.edit_direct') }}
                      </button>
                      <button v-if="tab.typeCode" class="edit-dropdown-item" type="button" @click="onEditMentCopy(tab)">
                        <AppIcon name="copy" /> {{ t('main.main_panel.copy_mention') }}
                      </button>
                      <button v-if="tab.typeCode" class="edit-dropdown-item" type="button" @click="onEditInvokeCommand(tab)">
                        <AppIcon name="terminal" /> {{ t('main.main_panel.invoke_command') }}
                      </button>
                      <button v-if="tab.typeCode" class="edit-dropdown-item" type="button" @click="onEditInvokeAi(tab)">
                        <AppIcon name="robot" /> {{ t('main.main_panel.invoke_ai') }}
                      </button>
                    </div>
                  </transition>
                </div>
                <button v-if="!aiRunDocumentLocked" class="btn btn-secondary btn-sm" type="button" @click="openFullView(tab)">
                  <AppIcon name="corners-out" /> {{ t('main.document_preview.full_view') }}
                </button>
                <span v-else class="ro-badge ro-badge-sm">
                  <AppIcon name="lock-simple" /> {{ t('main.document_preview.edit_locked') }}
                </span>
              </div>
            </div>
            <div class="card-bd">
              <!-- 0310 TR: load by doc-id whenever this is NOT a file tab, instead of keying on
                   typeCode. A DB-document tab opened via any entry point that omitted typeCode
                   (e.g. the head-move button pre-0310.0005-TR) still loaded null → "연결된 MD
                   파일이 없습니다". isFileTab (projectId && no typeCode) is the single, entry-point-
                   agnostic signal for path-backed tabs, so new navigation entries can never
                   reintroduce the typeCode-loss regression. See flowgate.default.0310.0003-NR. -->
              <MdViewer
                :ref="(el) => bindActiveRef(mdViewerRefs, tab.id, el)"
                :path="tab.mdPath ?? tab.path"
                :doc-id="isFileTab(tab) ? null : tab.id"
                :project-id="tab.projectId ?? null"
                :git-group-id="tab.gitGroupId ?? null"
                :git-commit="tab.gitCommit ?? null"
                :read-only="aiRunDocumentLocked"
              />
            </div>
            </template>
          </div>
          <div v-else-if="tab.type === 'text'" class="card text-preview-card">
            <div class="card-hd">
              <span class="card-title">
                <AppIcon name="file-text" style="color:var(--text-m);" />
                {{ t('main.document_preview.text_title') }}
              </span>
              <div class="card-actions">
                <label class="text-wrap-toggle">
                  <input v-model="textWrapEnabled" type="checkbox" />
                  <span>{{ t('main.document_preview.wrap_lines') }}</span>
                </label>
                <button
                  v-if="!aiRunDocumentLocked && canEditTab(tab)"
                  class="btn btn-outline btn-sm"
                  type="button"
                  @click="onEditDirect(tab)"
                >
                  <AppIcon name="pencil-simple" /> {{ t('main.document_preview.edit') }}
                </button>
                <button class="btn btn-secondary btn-sm" type="button" @click="openFullView(tab)">
                  <AppIcon name="corners-out" /> {{ t('main.document_preview.full_view') }}
                </button>
              </div>
            </div>
            <div class="card-bd text-preview-body">
              <TextViewer
                :ref="(el) => bindActiveRef(textViewerRefs, tab.id, el)"
                :path="tab.path"
                :project-id="tab.projectId ?? null"
                :wrap-lines="textWrapEnabled"
                :git-group-id="tab.gitGroupId ?? null"
                :git-commit="tab.gitCommit ?? null"
              />
            </div>
          </div>
          <!-- 0326 R0001 — change view for one file, opened from the tree's
               "변경 내용 보기". A read-only sibling of the editor tab for the same
               path (ids differ by the `diff:` prefix), so both can stay open. -->
          <div v-else-if="tab.type === 'diff'" class="card text-preview-card">
            <div class="card-hd">
              <span class="card-title">
                <AppIcon name="git-diff" style="color:var(--text-m);" />
                {{ t('main.file_diff.title') }}
              </span>
            </div>
            <div class="card-bd text-preview-body">
              <FileDiffViewer
                :path="tab.path"
                :project-id="tab.projectId ?? null"
                :git-group-id="tab.gitGroupId ?? null"
                :git-commit="tab.gitCommit ?? null"
              />
            </div>
          </div>
          <div v-else-if="tab.type === 'too_large'" class="unsupported-view">
            <span>⚠️ {{ t('main.error.file_too_large') }}</span>
            <button @click="tabsStore.closeTab(tab.id)">{{ t('common.close') }}</button>
          </div>
          <div v-else class="unsupported-view">
            <span>⚠️ {{ t('main.main_panel.text_22') }}</span>
            <button @click="tabsStore.closeTab(tab.id)">{{ t('common.close') }}</button>
          </div>
          </template>
          </div><!-- doc-main -->
          <DocInfoPanel
            v-if="!aiRunDocumentLocked && canShowDocInfoPanel(tab.id)"
            :doc-id="tab.id"
            :type-code="getTabTypeCode(tab.id)"
            :group-id="exposedValue(docHeaderRefs[tab.id]?.groupId) ?? null"
            :review-status="exposedValue(docHeaderRefs[tab.id]?.docReviewStatus) ?? null"
            :reject-reason="exposedValue(docHeaderRefs[tab.id]?.rejectionReason) ?? null"
            :rejection-history="exposedValue(docHeaderRefs[tab.id]?.rejectionHistory) ?? []"
            :ai-review="exposedValue(docHeaderRefs[tab.id]?.aiReview) ?? null"
            :ai-review-history="exposedValue(docHeaderRefs[tab.id]?.aiReviewHistory) ?? []"
            :tr-scope="exposedValue(docHeaderRefs[tab.id]?.trScope) ?? null"
            :q-status="qStatuses[tab.id] ?? null"
            :workflow-steps="exposedValue(docHeaderRefs[tab.id]?.workflowSteps) ?? null"
            :orphan="exposedValue(docHeaderRefs[tab.id]?.workflowOrphan) === true"
            :candidate-slots="exposedValue(docHeaderRefs[tab.id]?.workflowCandidateSlots) ?? []"
            :self-index="exposedValue(docHeaderRefs[tab.id]?.workflowSelfIndex) ?? null"
            :step-states="getWorkflowViewState(tab.id).stepStates"
            :next-step-index="getWorkflowViewState(tab.id).nextStepIndex"
            :collapsed="docInfoCollapsed"
            @toggle="docInfoCollapsed = !docInfoCollapsed"
            @next-action="onProceedNextStep(tab.id)"
            @orphan-recovered="onOrphanRecovered(tab.id)"
            @edit-rejection="onEditRejectionRequested(tab.id)"
          />
          </div><!-- doc-with-panel -->
        </div>
      </template>

      <!-- Overview panel (no tab selected) -->
      <div v-else class="overview-panel">
        <div class="overview-head">
          <div class="overview-head-titles">
            <p class="page-title" data-i18n="dash.title">{{ t('dash.title') }}</p>
            <p class="page-sub" data-i18n="dash.sub">{{ t('dash.sub') }}</p>
          </div>
          <button
            class="btn btn-outline btn-sm overview-refresh"
            type="button"
            :disabled="!projectStore.currentProjectId || dashboardEntry?.refreshing || dashboardEntry?.initialLoading"
            :title="t('main.overview.refresh')"
            @click="emit('refresh-overview')"
          >
            <AppIcon
              name="arrows-clockwise"
              :spin="dashboardEntry?.refreshing || dashboardEntry?.initialLoading"
            />
            <span>{{ t('main.overview.refresh') }}</span>
          </button>
        </div>

        <!-- Guide card -->
        <div v-if="!guideDismissed" class="guide-card">
          <div class="guide-card-hd">
            <span class="guide-card-title">
              <AppIcon name="rocket-launch" /> {{ t('main.overview.guide_title') }}
            </span>
            <button class="guide-dismiss" :aria-label="t('common.close')" @click="dismissGuide">
              <AppIcon name="x" />
            </button>
          </div>
          <p class="guide-desc">{{ t('main.overview.guide_desc') }}</p>
          <div class="guide-flow">
            <div class="guide-step gs-start">
              <span class="gs-tag" style="background:#2563eb;">R</span>
              {{ t('main.overview.step_req') }}
              <span class="gs-hint">{{ t('main.main_panel.text_48') }}</span>
            </div>
            <span class="guide-arr"><AppIcon name="caret-right" /></span>
            <div class="guide-step"><span class="gs-tag" style="background:#7c3aed;">DS</span> {{ t('main.overview.step_ds') }}</div>
            <span class="guide-arr"><AppIcon name="caret-right" /></span>
            <div class="guide-step"><span class="gs-tag" style="background:#ea580c;">D</span> {{ t('main.overview.step_d') }}</div>
            <span class="guide-arr"><AppIcon name="caret-right" /></span>
            <div class="guide-step"><span class="gs-tag" style="background:#0891b2;">T</span> {{ t('main.overview.step_t') }}</div>
            <span class="guide-arr"><AppIcon name="caret-right" /></span>
            <div class="guide-step"><span class="gs-tag" style="background:#0284c7;">TR</span> {{ t('main.overview.step_tr') }}</div>
            <span class="guide-arr"><AppIcon name="caret-right" /></span>
            <div class="guide-step"><span class="gs-tag" style="background:#16a34a;">AC</span> {{ t('main.overview.step_ac') }}</div>
          </div>
          <div class="guide-actions">
            <button class="btn btn-primary btn-sm" @click="$emit('create-requirement')">
              <AppIcon name="plus" /> {{ t('main.overview.guide_cta') }}
            </button>
            <span class="guide-kbdhint"><kbd class="kbd">Alt</kbd>+<kbd class="kbd">N</kbd> {{ t('main.overview.guide_kbd') }}</span>
            <button class="btn btn-ghost btn-sm" style="color:var(--text-m); font-size:.75rem; margin-left:auto;" @click="dismissGuide">
              <AppIcon name="eye-slash" /> {{ t('main.overview.guide_dismiss') }}
            </button>
          </div>
        </div>

        <!-- Reopened guide indicator (when dismissed) -->
        <div v-else-if="projectStore.currentProjectId" class="guide-reopen">
          <button class="btn btn-outline btn-sm" @click="reopenGuide">
            <AppIcon name="eye" /> {{ t('main.overview.guide_reopen') }}
          </button>
        </div>

        <!-- Stats grid -->
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-icon" style="background:#dcfce7;"><AppIcon name="tree-structure" class="ic-green" /></div>
            <div>
              <div class="stat-num" style="color:var(--success);">{{ activeProjects }}</div>
              <div class="stat-lbl">{{ t('main.overview.active_projects') }}</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#dbeafe;"><AppIcon name="file-text" class="ic-blue" /></div>
            <div>
              <div class="stat-num" style="color:var(--primary);">{{ totalDocs }}</div>
              <div class="stat-lbl">{{ t('main.overview.total_docs') }}</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#ede9fe;"><AppIcon name="list-checks" style="color:#7c3aed;" /></div>
            <div>
              <div class="stat-num" style="color:#7c3aed;">{{ inProgressWorkflows }}</div>
              <div class="stat-lbl">{{ t('main.overview.wf_in_progress') }}</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#fef3c7;"><AppIcon name="gear-six" class="ic-yellow" /></div>
            <div>
              <div class="stat-num" style="color:var(--warning);">{{ workingGroups }}</div>
              <div class="stat-lbl">{{ t('main.overview.working') }}</div>
            </div>
          </div>
        </div>

        <!-- Content grid -->
        <div class="content-grid">
          <!-- Recent Activity -->
          <div class="card">
            <div class="card-hd">
              <span class="card-title">{{ t('main.overview.recent') }}</span>
              <span v-if="dashboardEntry?.refreshing" class="dashboard-refreshing">
                {{ t('main.overview.refreshing') }}
              </span>
            </div>
            <div class="card-body dashboard-list-body">
              <div v-if="dashboardEntry?.initialLoading" class="empty">
                <AppIcon name="spinner" spin />
                <p>{{ t('main.overview.loading') }}</p>
              </div>
              <div v-else-if="dashboardEntry?.error && !dashboardEntry.data" class="empty">
                <AppIcon name="warning" />
                <p>{{ t('main.overview.load_failed') }}</p>
                <button class="btn btn-outline btn-sm" type="button" @click="dashboardStore.retryCurrent">
                  {{ t('main.overview.retry') }}
                </button>
              </div>
              <div v-else-if="recentActivities.length === 0" class="empty">
                <AppIcon name="clock" />
                <p>{{ t('main.overview.no_activity') }}</p>
              </div>
              <div
                v-else
                ref="activityListEl"
                class="act-list"
                :class="{ 'dashboard-scroll-list': activitiesExpanded }"
                :style="activityListLockStyle"
              >
                <button
                  v-for="item in recentActivities"
                  :key="item.event_id"
                  class="act-item dashboard-row"
                  :class="{ 'dashboard-row--disabled': item.navigation.kind === 'none' }"
                  type="button"
                  :disabled="item.navigation.kind === 'none'"
                  @click="openDashboardTarget(item.navigation)"
                >
                  <div class="act-dot" :style="{ background: activityColor(item.activity_type) }"></div>
                  <div class="act-content">
                    <div v-if="item.document" class="activity-target">
                      <span class="doc-tag" :class="`c-${item.document.type_code}`">
                        {{ item.document.type_code }}
                      </span>
                      <strong class="activity-doc-id" :title="item.document.doc_id">
                        {{ item.document.doc_id }}
                      </strong>
                      <span class="activity-target-title">{{ item.document.title }}</span>
                    </div>
                    <div v-else-if="item.group" class="activity-target">
                      <AppIcon name="folder" class="activity-group-icon" />
                      <strong class="activity-doc-id">{{ item.group.group_id }}</strong>
                      <span class="activity-target-title">{{ item.group.title }}</span>
                    </div>
                    <div class="act-msg">{{ activityActionLabel(item) }}</div>
                    <div class="act-time">
                      {{ formatDashboardTime(item.occurred_at) }}
                      <template v-if="item.actor"> · {{ item.actor.username }}</template>
                    </div>
                  </div>
                </button>
              </div>
              <button
                v-if="allRecentActivities.length > ACTIVITY_PREVIEW_COUNT"
                class="dashboard-view-all"
                type="button"
                @click="toggleActivitiesExpanded"
              >
                <template v-if="activitiesExpanded">
                  <AppIcon name="caret-up" /> {{ t('main.overview.collapse') }}
                </template>
                <template v-else>
                  <AppIcon name="caret-down" />
                  {{ t('main.overview.view_all', { total: allRecentActivities.length }) }}
                </template>
              </button>
              <div v-if="dashboardEntry?.error && dashboardEntry.data" class="dashboard-inline-error">
                {{ t('main.overview.load_failed') }}
                <button type="button" @click="dashboardStore.retryCurrent">{{ t('main.overview.retry') }}</button>
              </div>
            </div>
          </div>

          <!-- Right column: AI runs + Workflow + Type Distribution -->
          <div class="right-col">
            <!-- AI run monitor (0269): always on the dashboard, running or not. -->
            <AiRunMonitorCard />

            <!-- Workflow Status -->
            <div class="card">
              <div class="card-hd">
                <span class="card-title">{{ t('main.overview.workflow') }}</span>
                <span v-if="dashboardEntry?.refreshing" class="dashboard-refreshing">
                  {{ t('main.overview.refreshing') }}
                </span>
              </div>
              <div class="card-body dashboard-list-body">
                <div v-if="dashboardEntry?.initialLoading" class="empty">
                  <AppIcon name="spinner" spin />
                  <p>{{ t('main.overview.loading') }}</p>
                </div>
                <div v-else-if="dashboardEntry?.error && !dashboardEntry.data" class="empty">
                  <AppIcon name="warning" />
                  <p>{{ t('main.overview.load_failed') }}</p>
                  <button class="btn btn-outline btn-sm" type="button" @click="dashboardStore.retryCurrent">
                    {{ t('main.overview.retry') }}
                  </button>
                </div>
                <div v-else-if="activeWorkflows.length === 0" class="empty">
                  <AppIcon name="tree-structure" />
                  <p>{{ t('main.overview.no_workflow') }}</p>
                </div>
                <div
                  v-else
                  ref="workflowListEl"
                  class="workflow-list"
                  :class="{ 'dashboard-scroll-list': workflowsExpanded }"
                  :style="workflowListLockStyle"
                >
                  <button
                    v-for="workflow in activeWorkflows"
                    :key="workflow.group_id"
                    class="workflow-item dashboard-row"
                    type="button"
                    @click="openDashboardTarget(workflow.navigation)"
                  >
                    <span class="workflow-content">
                      <span class="workflow-heading">
                        <strong :title="workflow.requirement.doc_id">{{ workflow.requirement.doc_id }}</strong>
                        <span
                          class="workflow-status-badge"
                          :class="`workflow-status-badge--${workflow.stage.state}`"
                        >
                          {{ workflowStageLabel(workflow) }}
                        </span>
                      </span>
                      <span class="workflow-requirement">
                        {{ workflow.requirement.title }}
                      </span>
                      <span class="workflow-progress-track" aria-hidden="true">
                        <span
                          class="workflow-progress-fill"
                          :style="{
                            width: `${workflow.progress.percent}%`,
                            background: typeBarColor(workflow.stage.type_code),
                          }"
                        ></span>
                      </span>
                      <span class="workflow-footer">
                        <span class="workflow-stage-flow">
                          <span class="doc-tag c-R">R</span>
                          <AppIcon name="caret-right" />
                          <span class="doc-tag" :class="`c-${workflow.stage.type_code}`">
                            {{ workflow.stage.type_code }}
                          </span>
                        </span>
                        <small>
                          {{ t('main.overview.workflow_steps', {
                            completed: workflow.progress.completed_steps,
                            total: workflow.progress.total_steps,
                          }) }}
                          · {{ formatDashboardTime(workflow.updated_at) }}
                        </small>
                      </span>
                    </span>
                  </button>
                </div>
                <button
                  v-if="allActiveWorkflows.length > WORKFLOW_PREVIEW_COUNT"
                  class="dashboard-view-all"
                  type="button"
                  @click="toggleWorkflowsExpanded"
                >
                  <template v-if="workflowsExpanded">
                    <AppIcon name="caret-up" /> {{ t('main.overview.collapse') }}
                  </template>
                  <template v-else>
                    <AppIcon name="caret-down" />
                    {{ t('main.overview.view_all', { total: allActiveWorkflows.length }) }}
                  </template>
                </button>
                <div v-if="dashboardEntry?.error && dashboardEntry.data" class="dashboard-inline-error">
                  {{ t('main.overview.load_failed') }}
                  <button type="button" @click="dashboardStore.retryCurrent">{{ t('main.overview.retry') }}</button>
                </div>
              </div>
            </div>

            <!-- Type Distribution -->
            <div class="card">
              <div class="card-hd">
                <span class="card-title">{{ t('main.overview.type_dist') }}</span>
              </div>
              <div class="card-body">
                <div v-if="typeDistribution.length === 0" class="empty">
                  <AppIcon name="chart-pie" />
                  <p>{{ t('main.overview.no_data') }}</p>
                </div>
                <div v-else style="padding:14px 18px; display:flex; flex-direction:column; gap:8px;">
                  <div v-for="item in distPageItems" :key="item.type" style="display:flex; align-items:center; gap:10px;">
                    <span class="doc-tag" :class="`c-${item.type}`" style="width:32px; text-align:center; flex-shrink:0;">{{ item.type }}</span>
                    <div style="flex:1; height:8px; background:var(--border); border-radius:4px; overflow:hidden;">
                      <div :style="{ width: typeBarWidth(item.count) + '%', height: '100%', background: typeBarColor(item.type), borderRadius: '4px' }"></div>
                    </div>
                    <span class="text-xs text-m" style="width:20px; text-align:right;">{{ item.count }}</span>
                  </div>
                  <div
                    v-if="distPageCount > 1"
                    style="display:flex; align-items:center; justify-content:center; gap:12px; margin-top:6px;"
                  >
                    <button
                      type="button"
                      class="dist-pager-btn"
                      :disabled="distPage === 0"
                      :aria-label="t('main.overview.dist_prev')"
                      @click="distPage--"
                    >
                      <AppIcon name="caret-left" />
                    </button>
                    <span class="text-xs text-m">{{ t('main.overview.dist_page', { current: distPage + 1, total: distPageCount }) }}</span>
                    <button
                      type="button"
                      class="dist-pager-btn"
                      :disabled="distPage >= distPageCount - 1"
                      :aria-label="t('main.overview.dist_next')"
                      @click="distPage++"
                    >
                      <AppIcon name="caret-right" />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <!-- Open queries — group 0022 D0005 §3.7: aggregates document-scoped queries.
                 Standalone [Register new Q] is retired. Queries are always created from a
                 document's [Q&A] panel. -->
            <div class="card">
              <div class="card-hd">
                <span class="card-title">
                  <span class="doc-tag c-Q" style="font-size:.65rem; padding:1px 5px; margin-right:4px;">Q</span>
                  {{ t('main.main_panel.open_q_title') }}
                </span>
              </div>
              <div class="card-body">
                <div v-if="qListLoading" style="padding:20px; text-align:center; font-size:.8rem; opacity:.6;">{{ t('common.loading') }}</div>
                <div v-else-if="qListError" style="padding:12px 16px; font-size:.8rem; color:var(--danger);">{{ qListError }}</div>
                <div v-else-if="qList.length === 0" class="empty">
                  <AppIcon name="question" />
                  <p>{{ t('main.main_panel.open_q_empty') }}</p>
                </div>
                <template v-else>
                  <div
                    ref="qListEl"
                    :class="{ 'dashboard-scroll-list': qListExpanded }"
                    :style="qListLockStyle"
                  >
                    <div
                      v-for="qItem in openQueries"
                      :key="`${qItem.doc_id}-${qItem.seq}`"
                      class="q-list-item"
                      @click="openDocFromQuery(qItem)"
                    >
                      <span class="q-state-badge pending" style="font-size:.66rem; padding:1px 7px; flex-shrink:0;">
                        {{ t('main.main_panel.q_status_pending') }}
                      </span>
                      <span class="q-list-item-title">{{ qItem.doc_id }} · Q{{ qItem.seq }} {{ qItem.title }}</span>
                    </div>
                  </div>
                  <button
                    v-if="allOpenQueries.length > OPEN_Q_PREVIEW_COUNT"
                    class="dashboard-view-all"
                    type="button"
                    @click="toggleQListExpanded"
                  >
                    <template v-if="qListExpanded">
                      <AppIcon name="caret-up" /> {{ t('main.overview.collapse') }}
                    </template>
                    <template v-else>
                      <AppIcon name="caret-down" />
                      {{ t('main.overview.view_all', { total: allOpenQueries.length }) }}
                    </template>
                  </button>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>


    <!-- B0001 (0310): the "head doc로 이동" button shows/opens by the RAW DocHeader headDocId
         (the :head-doc-id binding below), so head-doc-label — which becomes the opened tab's
         typeCode — must come from the SAME DocHeader source (workflowHeadType), not the computed
         workflowViewState. In every noAction mode (review/info/…) the view state returns
         headDocLabel=null while the raw headDocId is still set, so the old binding opened a
         type:'md' tab with no typeCode → MdViewer :doc-id=null → "연결된 MD 파일이 없습니다".
         Falls back to the view state value for the decision-override bridge.
         See flowgate.default.0310.0003-NR. -->
    <ReviewActionBar
      v-if="!aiRunDocumentLocked && activeTabId != null && activeTab && getActionBarMode(activeTabId) != null"
      :key="`${activeTabId}:${reviewActionBarGitEpoch}`"
      :mode="getActionBarMode(activeTabId)!"
      :doc-id="activeTabId"
      :project-id="exposedValue(docHeaderRefs[activeTabId]?.docProjectId) ?? ''"
      :group-id="exposedValue(docHeaderRefs[activeTabId]?.groupId) ?? ''"
      :doc-ref="activeTabId"
      :doc-title="activeTab.title"
      :review-status="exposedValue(docHeaderRefs[activeTabId]?.docReviewStatus) ?? null"
      :ai-review-arrived="!!exposedValue(docHeaderRefs[activeTabId]?.aiReview)"
      :has-ai-provider="activeProjectHasAiProvider"
      :doc-type="getTabTypeCode(activeTabId) ?? undefined"
      :next-step-label="getNextStepLabel(activeTabId)"
      :next-step-code="getWorkflowViewState(activeTabId).nextStepCode ?? undefined"
      :review-request-label="getReviewRequestLabel(activeTabId)"
      :can-next-action="getWorkflowViewState(activeTabId).canNextAction"
      :test-run-status="exposedValue(docHeaderRefs[activeTabId]?.testRun)?.status ?? null"
      :group-test-run-active="exposedValue(docHeaderRefs[activeTabId]?.groupTestRunActive) ?? true"
      :head-doc-id="exposedValue(docHeaderRefs[activeTabId]?.headDocId) ?? null"
      :head-doc-label="exposedValue(docHeaderRefs[activeTabId]?.workflowHeadType) ?? getWorkflowViewState(activeTabId).headDocLabel"
      :head-doc-title="exposedValue(docHeaderRefs[activeTabId]?.headDocTitle) ?? null"
      :viewed-doc-id="activeTabId"
      @approve="onReviewApproved(activeTabId, $event)"
      @reject="onReviewRejected(activeTabId)"
      @revision-complete="onReviewApproved(activeTabId, $event)"
      @open-mention-dialog="onReviewOpenMentionDialog"
      @copy-rework-mention="onReviewReworkCopyMention"
      @invoke-command="onReviewInvokeCommand"
      @invoke-rework-ai="onReviewReworkInvokeAi"
      @invoke-review-ai="onReviewInvokeAiEntry"
      @decide-workflow="openWorkflowDecisionForActive"
      @copy-workflow-mention="onWorkflowDecisionCopyMention"
      @invoke-workflow-command="onWorkflowDecisionInvokeCommand"
      @next-action="onProceedNextStep(activeTabId)"
      @copy-next-mention="onActionBarCopyNextMention(activeTabId)"
      @create-empty="onActionBarCreateEmpty(activeTabId)"
      @create-approved="onActionBarCreateApproved(activeTabId)"
      @create-conversation="onActionBarCreateConversation(activeTabId)"
      @run-test="onActionBarRunTest(activeTabId)"
      @continuous-work="onActionBarContinuousWork(activeTabId)"
      @open-head-doc="onOpenHeadDocClick"
      @open-test-scenario="onOpenTestScenarioDoc"
    />

    <!-- flowgate.default.0405 P0004: the work-plan-only proposal dialog. The next-action
         list never opens for a WP head — this window replaces it and carries one scope into
         all four buttons. -->
    <WorkPlanProposalDialog
      v-model:visible="workPlanProposalVisible"
      :parent-doc-id="workPlanCreateParentDocId"
      :project-id="workPlanCreateProjectId"
      :group-id="workPlanCreateGroupId"
      :busy-action="workPlanProposalBusy"
      :external-notice="workPlanProposalNotice"
      :ai-active="workPlanProposalAiActive"
      @created="onWorkPlanCreated"
      @copy-mention="onWorkPlanProposalCopyMention"
      @invoke-ai="onWorkPlanProposalInvokeAi"
    />

    <!-- flowgate.default.0395 D0007 §6.3: create dialog for a new work-plan document. -->
    <WorkPlanCreateDialog
      v-model:visible="workPlanCreateVisible"
      :parent-doc-id="workPlanCreateParentDocId"
      :project-id="workPlanCreateProjectId"
      :group-id="workPlanCreateGroupId"
      @created="onWorkPlanCreated"
    />

    <!-- Document Full View Modal -->
    <teleport to="body">
      <!-- `modal-bg--below-header`: the full view is a reading surface, not an alert, and
           the run monitor now lives in the header. Starting the dim below the header keeps
           the monitor reachable while a document is being read (0269 D0002 "화면 어디에서든
           항상"), which the full-screen dim would otherwise cover. -->
      <div
        v-if="fullViewVisible && fullViewTab"
        class="modal-bg modal-bg--below-header"
        @keydown.escape="closeFullView"
      >
        <div class="modal-box document-modal">
          <div class="modal-hd">
            <span class="modal-title">
              <AppIcon :name="fullViewIcon" style="color:var(--text-m);" />
              {{ fullViewTab.title }}
            </span>
            <div class="modal-hd-actions">
              <!-- CH has no [edit]: a chat is written through its composer, not by hand-editing
                   the transcript, and its card offers no [edit] either.
                   0344 TR0008 후속: 위 문장은 쓰여 있었지만 조건에 CH 검사가 없어 실제로는
                   여기 [편집]이 노출돼 있었다. 이제 canEditTab() 이 한 곳에서 막는다
                   (0432.0003-NR §7-2). -->
              <button
                v-if="canEditTab(fullViewTab)"
                class="btn btn-outline btn-sm"
                type="button"
                @click="editFromFullView(fullViewTab)"
              >
                <AppIcon name="pencil-simple" /> {{ t('main.document_preview.edit') }}
              </button>
              <button class="modal-close" type="button" @click="closeFullView">
                <AppIcon name="x" />
              </button>
            </div>
          </div>
          <!-- CH mounts no viewer of its own here. This body is the teleport target: the chat
               card moves its live ConversationView in, so the dialog shows the same instance the
               card held — running spinner and unsent draft included. -->
          <div
            class="modal-bd document-modal__body"
            :class="{ 'document-modal__body--conversation': fullViewTab.typeCode === 'CH' }"
          >
            <template v-if="fullViewTab.typeCode !== 'CH'">
              <TextViewer
                v-if="fullViewTab.type === 'text'"
                :path="fullViewTab.path"
                :project-id="fullViewTab.projectId ?? null"
                :wrap-lines="textWrapEnabled"
                :git-group-id="fullViewTab.gitGroupId ?? null"
                :git-commit="fullViewTab.gitCommit ?? null"
              />
              <!-- 0310 TR: not-a-file-tab → load by doc-id (mirrors the preview-card MdViewer);
                   see the comment there and flowgate.default.0310.0003-NR. -->
              <MdViewer
                v-else
                :path="fullViewTab.mdPath ?? fullViewTab.path"
                :doc-id="isFileTab(fullViewTab) ? null : fullViewTab.id"
                :project-id="fullViewTab.projectId ?? null"
                :git-group-id="fullViewTab.gitGroupId ?? null"
                :git-commit="fullViewTab.gitCommit ?? null"
              />
            </template>
          </div>
        </div>
      </div>
    </teleport>

    <!-- Document Edit Modal -->
    <teleport to="body">
      <div v-if="editVisible && editTab" class="modal-bg" @keydown.escape="closeEditModal">
        <div class="modal-box document-modal document-modal--edit">
          <div class="modal-hd">
            <span class="modal-title">
              <AppIcon name="pencil-simple" style="color:var(--primary);" />
              {{ t('main.document_preview.edit_title', { title: editTab.title }) }}
            </span>
            <div class="modal-hd-actions">
              <button
                class="btn btn-outline btn-sm"
                type="button"
                :disabled="editSaving"
                @click="toggleHeaderEditMode"
                :title="headerEditModeVisible ? t('main.main_panel.header_hide') : t('main.main_panel.header_show')"
              >
                <AppIcon :name="headerEditModeVisible ? 'eye' : 'eye-slash'" />
                {{ headerEditModeVisible ? t('main.main_panel.header_hide') : t('main.main_panel.header_edit') }}
              </button>
              <button class="modal-close" type="button" :disabled="editSaving" @click="closeEditModal">
                <AppIcon name="x" />
              </button>
            </div>
          </div>
          <div class="modal-bd document-editor">
            <div v-if="editLoading" class="document-editor__state">
              {{ t('common.loading') }}
            </div>
            <div v-else-if="editError" class="document-editor__state document-editor__state--error">
              {{ editError }}
            </div>
            <textarea
              v-else-if="headerEditModeVisible"
              v-model="editFullContent"
              class="document-editor__textarea"
              spellcheck="false"
            />
            <textarea
              v-else
              v-model="editBody"
              class="document-editor__textarea"
              spellcheck="false"
            />
          </div>
          <div class="modal-ft">
            <button type="button" class="btn btn-secondary" :disabled="editSaving" @click="closeEditModal">
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="editLoading || editSaving || !!editError"
              @click="saveEditContent"
            >
              <AppIcon name="floppy-disk" />
              {{ editSaving ? t('main.document_preview.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </teleport>

    <!-- Next Action Modal -->
    <NextActionModal
      v-model:visible="nextActionModalVisible"
      :next-step-label="nextActionModalStep"
      :next-type-code="nextActionModalTypeCode"
      :project-id="nextActionModalProjectId"
      :group-id="nextActionModalGroupId"
      :doc-ref="nextActionModalDocRef"
      :doc-module="nextActionModalModuleName"
      :initial-selected-docs="nextActionModalInitialDocs"
      :current-doc-id="nextActionModalTabId"
      :current-doc-type="nextActionModalCurrentType"
      @invoke-command="onNextActionInvokeCommand"
      @invoke-ai="onNextActionInvokeAi"
      @copy-mention="onNextActionCopyMention"
      @copy-mention-with-message="onNextActionCopyMentionWithMessage"
      @create-empty="onNextActionCreateEmpty"
    />

    <!-- Continuous work: choose a range, then select in-app execution or an external-AI mention. -->
    <ContinuousWorkDialog
      v-model:visible="continuousDialogVisible"
      :doc-ref="continuousDocRef"
      :providers="aiProviderStore.providers"
      :selected-provider="aiProviderStore.selectedProviderId"
      :provider-pinned="aiProviderStore.pinned"
      :provider-loading="aiProviderStore.loading"
      :provider-errored="!!aiProviderStore.error"
      @confirm="onContinuousDialogConfirm"
      @update:provider="aiProviderStore.selectProvider"
      @clear-provider-pin="aiProviderStore.clearPin"
    />
    <ContinuousWarningDialog
      v-model:visible="continuousWarnVisible"
      :project="continuousProjectId"
      :step-count="continuousStepCount"
      :target-label="continuousTargetLabel"
      :review-mode="continuousReviewMode"
      :instruction-mode="continuousInstructionMode"
      :auto-approve-count="continuousAutoApproveItemSeqs.length"
      :from-decision="continuousFromDecision"
      @confirm="onContinuousWarnConfirm"
      @copy-mention="onContinuousWarnCopyMention"
      @copy-with-message="onContinuousWarnCopyMentionWithMessage"
    />

    <MentionMessageDialog
      :visible="mmDialogVisible"
      :project-id="mmDialogProjectId"
      :doc-type="mmDialogDocType"
      :doc-types="mmDialogDocTypes"
      :candidates="mmDialogCandidates"
      @select="onMmDialogSelect"
      @select-invoke="onMmDialogSelectInvoke"
      @cancel="onMmDialogCancel"
    />

    <!-- Design Handoff Dialog -->
    <DesignHandoffDialog
      v-model:visible="designHandoffVisible"
      :doc-ref="designHandoffDocRef"
      :project-id="designHandoffProjectId"
      :group-id="designHandoffGroupId"
      :default-types="designHandoffDefaultTypes"
      :next-step-label="designHandoffNextStepLabel"
      @copy-mention="onDesignHandoffCopyMention"
      @invoke-ai="onDesignHandoffInvokeAi"
    />

    <!-- Review Reject Dialog -->
    <ReviewRejectDialog
      ref="rejectDialogRef"
      :visible="rejectDialogVisible"
      :doc-id="rejectDialogDocId"
      :doc-name="rejectDialogDocName"
      :doc-type="tabs.find(t => t.id === rejectDialogTabId)?.typeCode ?? null"
      :existing-reason="rejectDialogExistingReason"
      :edit-mode="rejectDialogEditMode"
      @save-reason="onRejectDialogSaveReason"
      @copy-mention="onRejectDialogCopyMention"
      @invoke-ai="onRejectDialogInvokeAi"
      @update:visible="(v: boolean) => { rejectDialogVisible = v; if (!v) onRejectDialogClosed() }"
    />

    <!-- Time Machine Dialog (AC reject → reopen at an earlier step) -->
    <TimeMachineDialog
      :visible="timeMachineVisible"
      :steps="timeMachineSteps"
      :loading="timeMachineLoading"
      :preselect-doc-id="timeMachinePreselectDocId"
      :commit-preview="timeMachineCommitPreview"
      :cancel-result="timeMachineCancelResult"
      :retrying="timeMachineRetrying"
      @confirm="onTimeMachineConfirm"
      @retry-cancel="onTimeMachineRetryCancel"
      @open-git-panel="onTimeMachineOpenGitPanel"
      @update:visible="onTimeMachineVisibleChange"
    />

    <!-- 0142 rework — forward-restore confirm (symmetric with the backward dialog above). -->
    <ConfirmModal
      v-model:visible="returnConfirmVisible"
      :title="t('main.time_machine.return_confirm_title')"
      :message="returnConfirmMessage"
      :confirm-label="t('main.time_machine.return_confirm_ok')"
      @confirm="doWorkflowStepReturn"
      @cancel="pendingReturn = null"
    />

    <NextEmptyDocModal
      v-model:visible="nextEmptyDocModalVisible"
      :project-id="nextEmptyDocProjectId"
      :group-id="nextEmptyDocGroupId"
      :prev-doc-id="nextEmptyDocPrevDocId"
      :doc-type="nextEmptyDocType"
      :module-name="nextEmptyDocModule"
      :module-title="nextEmptyDocModuleTitle"
      @created="onNextEmptyDocCreated"
    />

    <!-- R0001 #2 (0048): auto-approved document creation — confirm-once, no input modal (D0004 §4-3) -->
    <ConfirmModal
      v-model:visible="createApprovedConfirmVisible"
      :title="t('main.review_action_bar.create_approved_confirm_title')"
      :message="t('main.review_action_bar.create_approved_confirm_message')"
      :confirm-label="t('main.review_action_bar.btn_create_approved')"
      @confirm="doCreateApprovedDocument"
    />

    <!-- Command Selector Modal -->
    <CommandSelectorModal
      v-model:visible="commandSelectorVisible"
      :env-overrides="pendingEnvOverrides"
    />

    <!-- AI invoke setup: admitted runs continue in the group-scoped inline strip. -->
    <AiInvokeDialog
      v-model:visible="aiInvokeVisible"
      :project="aiInvokeProject"
      :module="aiInvokeModule"
      :group="aiInvokeGroup"
      :doc-ref="aiInvokeDocRef"
      :sequence-doc-ref="aiInvokeSequenceDocRef"
      :action-scope="aiInvokeActionScope"
      :initial-mode="aiInvokeInitialMode"
      :initial-target-seq="aiInvokeInitialTargetSeq"
      :continuation-review-mode="aiInvokeContinuationReviewMode"
      :continuation-instruction-mode="aiInvokeContinuationInstructionMode"
      :continuation-auto-approve-item-seqs="aiInvokeContinuationAutoApproveItemSeqs"
      :provider-overrides="aiInvokeProviderOverrides"
      :default-message="aiInvokeDefaultMessage"
      :message-overrides="aiInvokeMessageOverrides"
      :review-count-overrides="aiInvokeReviewCountOverrides"
      :reviewer-overrides="aiInvokeReviewerOverrides"
      :continuation-step-timeout-sec="aiInvokeStepTimeoutSec"
      :continuation-restart-max-attempts="aiInvokeRestartMaxAttempts"
      :auto-start="aiInvokeAutoStart"
      :selected-docs="aiInvokeSelectedDocs"
      :messages="aiInvokeMessages"
      :reject-reason="aiInvokeRejectReason"
      :design-types="aiInvokeDesignTypes"
      :design-mode="aiInvokeDesignMode"
      :design-first-label="aiInvokeDesignFirstLabel"
    />

    <!-- 0339 R0001: reversible Git archive catalogue. Permanent deletion is
         intentionally reachable only from this second-stage screen. -->
    <Teleport to="body">
      <div
        v-if="gitArchiveVisible"
        class="modal-bg git-archive-overlay"
      >
        <div class="modal-box modal-lg git-archive-modal" role="dialog" aria-modal="true">
          <div class="modal-hd">
            <span class="modal-title">
              <AppIcon name="archive" /> {{ t('main.git_archive_panel.title') }}
              <span class="badge badge-yellow">{{ gitArchives.length }}</span>
            </span>
            <button class="modal-close" type="button" :disabled="gitArchiveBusy" @click="closeGitArchive">
              <AppIcon name="x" />
            </button>
          </div>
          <div class="modal-bd git-archive-body">
            <div class="git-archive-intro">
              <AppIcon name="arrow-counter-clockwise" />
              <span>{{ t('main.git_archive_panel.intro') }}</span>
            </div>
            <div v-if="gitArchiveLoading" class="git-archive-state">
              <AppIcon name="spinner" spin /> {{ t('main.git_archive_panel.loading') }}
            </div>
            <div v-else-if="gitArchiveError" class="git-archive-state git-archive-state--error">
              {{ gitArchiveError }}
              <button class="btn btn-secondary btn-sm" type="button" @click="fetchGitArchives">
                {{ t('main.git_archive_panel.retry') }}
              </button>
            </div>
            <div v-else-if="gitArchives.length === 0" class="git-archive-state">
              {{ t('main.git_archive_panel.empty') }}
            </div>
            <div v-else class="git-archive-list">
              <article v-for="item in gitArchives" :key="item.group_id" class="git-archive-row">
                <input
                  v-model="gitArchivePicked"
                  type="checkbox"
                  :value="item.group_id"
                  :disabled="gitArchiveBusy || item.status !== 'archived'"
                  :title="t('main.git_archive_panel.pick')"
                />
                <div class="git-archive-row__body">
                  <strong>
                    {{ item.group_id }}: {{ item.title }}
                    <span class="badge badge-gray">{{ t('main.git_archive_panel.archived') }}</span>
                  </strong>
                  <span class="git-archive-branch">{{ item.branch }}</span>
                  <span>
                    {{ formatGitArchiveTime(item.archived_at) }} ·
                    {{ t('main.git_archive_panel.commits') }} {{ item.commit_count ?? 0 }} ·
                    {{ t('main.git_archive_panel.files') }} {{ item.changed_file_count ?? 0 }} ·
                    {{ t('main.git_archive_panel.base') }} <code class="git-archive-sha">{{ shortGitSha(item.base_sha) }}</code>
                  </span>
                  <span v-if="item.reason" class="git-archive-reason">{{ t('main.git_archive_panel.reason') }}: {{ item.reason }}</span>
                  <code class="git-archive-refs">
                    {{ item.head_ref }} → {{ shortGitSha(item.head_sha) }}
                    <template v-if="item.stash_ref"><br />{{ item.stash_ref }} → {{ shortGitSha(item.stash_sha) }}</template>
                  </code>
                </div>
                <button
                  class="btn btn-primary btn-sm"
                  type="button"
                  :disabled="gitArchiveBusy || item.status !== 'archived'"
                  @click="restoreGitArchive(item)"
                >
                  <AppIcon name="arrow-counter-clockwise" /> {{ t('main.git_archive_panel.restore') }}
                </button>
              </article>
            </div>
            <div v-if="gitArchives.length" class="git-archive-purge">
              <strong><AppIcon name="warning" /> {{ t('main.git_archive_panel.purge_title') }}</strong>
              <p>{{ t('main.git_archive_panel.purge_desc') }}</p>
              <label>
                <input v-model="gitArchivePurgeConfirmed" type="checkbox" :disabled="gitArchiveBusy" />
                {{ t('main.git_archive_panel.purge_confirm') }}
              </label>
              <button
                class="btn btn-danger btn-sm"
                type="button"
                :disabled="gitArchiveBusy || !gitArchivePurgeConfirmed || gitArchivePicked.length === 0"
                @click="purgeGitArchives"
              >
                <AppIcon name="trash" /> {{ t('main.git_archive_panel.purge') }} ({{ gitArchivePicked.length }})
              </button>
            </div>
          </div>
          <div class="modal-ft">
            <button class="btn btn-ghost btn-sm" type="button" :disabled="gitArchiveBusy" @click="closeGitArchive">
              {{ t('main.git_archive_panel.close') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
    <!-- Quick Open Dialog -->
    <div v-if="showQuickOpen" class="modal-overlay">
      <div class="modal" style="max-width:480px;">
        <div class="modal-hd">
          <span>{{ t('main.quick_open.placeholder') }}</span>
          <button class="modal-close" @click="showQuickOpen = false"><AppIcon name="x" /></button>
        </div>
        <div class="modal-body">
          <input
            ref="quickInputRef"
            v-model="quickQuery"
            class="form-ctrl"
            :placeholder="t('main.quick_open.placeholder')"
            autofocus
            disabled
            @keydown.escape="showQuickOpen = false"
          />
          <div class="empty" style="margin-top:16px;">
            <p>{{ t('main.main_panel.description_187') }}</p>
          </div>
        </div>
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import api, { getRequest, patchRequest, postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import { isFileTab, useTabsStore, type Tab } from '../stores/tabs'
import { useProjectStore } from '../stores/project'
import { useExplorerStore } from '../stores/explorer'
import { useAiProviderStore } from '../stores/aiProvider'
import { groupIdFromDocId, useAiInvokeRunsStore } from '../stores/aiInvokeRuns'
import {
  useDashboardStore,
  type DashboardWorkflow,
} from '../stores/dashboard'
import { useShortcuts } from '../composables/useShortcuts'
import { useDashboardNavigation } from '../composables/useDashboardNavigation'
import { useActivityFormat } from '../composables/useActivityFormat'
import { useToast, type ToastType } from './common/useToast'
import { useDocTypeStore } from '../stores/docTypeStore'
import { resolveWorkflowViewState, type WorkflowViewInput, type WorkflowViewState } from '../workflow/workflowViewState'
import {
  resolveClickedSlot, isRollbackTarget, returnTargetIndices, slotCommitMarks,
  type SequenceSlot, type SlotCommitMark,
} from '../workflow/timeMachineSlot'
import { useFlowGateToken, splitGroupId, type RejectionHistoryItem, type RejectionContext } from '../composables/useFlowGateToken'
import { useMentionCopy, type MentionKind } from '../composables/useMentionCopy'
import TabBar from './TabBar.vue'
import AiRunMonitorCard from './AiRunMonitorCard.vue'
import TextViewer from './TextViewer.vue'
import FileDiffViewer from './FileDiffViewer.vue'
import MdViewer from './MdViewer.vue'
import DocHeader from './DocHeader.vue'
import TestFailStrip from './TestFailStrip.vue'
import TestRunStrip from './TestRunStrip.vue'
import DocWorkflow from './DocWorkflow.vue'
import GitFinalizePanel from './GitFinalizePanel.vue'
import AttachmentCard from './AttachmentCard.vue'
import ReviewActionBar from './ReviewActionBar.vue'
import ReviewRejectDialog from './ReviewRejectDialog.vue'
import TimeMachineDialog from './TimeMachineDialog.vue'
import DesignHandoffDialog from './DesignHandoffDialog.vue'
import WorkPlanCreateDialog from './WorkPlanCreateDialog.vue'
import WorkPlanProposalDialog, { type WorkPlanScope } from './WorkPlanProposalDialog.vue'
import WorkPlanEditor from './WorkPlanEditor.vue'
import {
  DEFAULT_INSTRUCTION_MODE,
  type ContinuationInstructionMode,
} from '../types/workPlanFillPreset'
import NextActionModal from './NextActionModal.vue'
import ContinuousWorkDialog from './ContinuousWorkDialog.vue'
import ContinuousWarningDialog from './ContinuousWarningDialog.vue'
import MentionMessageDialog from './MentionMessageDialog.vue'
import { buildCandidateList, type MessageEntry } from '../utils/mentionMessages'
import { copyToClipboard, copyToClipboardDeferred, ClipboardAbort, consumeLastFailedCopyText } from '../utils/clipboard'
import { openClipboardFallback } from '../composables/useClipboardFallback'
import type { AdvanceFailure, IssuedToken } from '../composables/useFlowGateToken'
import NextEmptyDocModal from './NextEmptyDocModal.vue'
import ConfirmModal from './ConfirmModal.vue'
import CommandSelectorModal from './CommandSelectorModal.vue'
import AiInvokeDialog from './AiInvokeDialog.vue'
import AiInvokeInline from './AiInvokeInline.vue'
import QTDetailViewer from './QTDetailViewer.vue'
import DocInfoPanel from './DocInfoPanel.vue'
import ConversationView from './ConversationView.vue'

const { t, locale } = useI18n()

interface GitArchiveItem {
  status: 'archiving' | 'archived'
  project_id: string
  group_id: string
  title: string
  branch: string
  archived_at: string | null
  reason: string | null
  base_sha: string | null
  head_sha: string
  head_ref: string
  stash_sha: string | null
  stash_ref: string | null
  commit_count: number
  changed_file_count: number
}

const gitArchiveVisible = ref(false)
const gitArchiveLoading = ref(false)
const gitArchiveBusy = ref(false)
const gitArchiveError = ref('')
const gitArchiveProjectId = ref('')
const gitArchives = ref<GitArchiveItem[]>([])
const gitArchivePicked = ref<string[]>([])
const gitArchivePurgeConfirmed = ref(false)
// A standalone archive can happen while the AC tab remains mounted. Remounting
// its action bar forces the approval-preview fetch to discard the pre-archive
// git_action before the next click.
const reviewActionBarGitEpoch = ref(0)

function gitArchiveProject(groupId: string): string {
  return (groupId || '').split('.', 1)[0] || projectStore.currentProjectId || ''
}

function gitArchiveFailure(error: any): string {
  return error?.response?.data?.error?.message
    || error?.response?.data?.detail
    || error?.message
    || t('main.git_archive_panel.failed')
}

async function openGitArchive(groupId: string) {
  const projectId = gitArchiveProject(groupId)
  if (!projectId) return
  gitArchiveProjectId.value = projectId
  gitArchiveVisible.value = true
  gitArchivePicked.value = []
  gitArchivePurgeConfirmed.value = false
  await fetchGitArchives()
}

async function onGitArchived(groupId: string) {
  const projectId = gitArchiveProject(groupId)
  if (!projectId) return
  gitArchiveProjectId.value = projectId
  reviewActionBarGitEpoch.value += 1
  await refreshAfterGitArchive()
}

function closeGitArchive() {
  if (gitArchiveBusy.value) return
  gitArchiveVisible.value = false
  gitArchiveError.value = ''
  gitArchivePicked.value = []
  gitArchivePurgeConfirmed.value = false
}

async function fetchGitArchives() {
  const projectId = gitArchiveProjectId.value
  if (!projectId) return
  gitArchiveLoading.value = true
  gitArchiveError.value = ''
  try {
    const { data } = await getRequest<{ ok: boolean; items: GitArchiveItem[] }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/git/archives`,
    )
    gitArchives.value = Array.isArray(data.items) ? data.items : []
    const available = new Set(gitArchives.value.map(item => item.group_id))
    gitArchivePicked.value = gitArchivePicked.value.filter(groupId => available.has(groupId))
  } catch (error: any) {
    gitArchiveError.value = gitArchiveFailure(error)
  } finally {
    gitArchiveLoading.value = false
  }
}

async function refreshAfterGitArchive() {
  const projectId = gitArchiveProjectId.value
  if (!projectId) return
  // invalidateProject drops BOTH display variants for the project; the prewarm below then
  // rebuilds the full one (0454 T0006 §4.2), which is what this file's cached-tree readers
  // (the overview cards and the empty-doc module lookup) go on to read.
  explorerStore.invalidateProject(projectId)
  await Promise.allSettled([
    explorerStore.fetchGroupTree(projectId, true, true),
    explorerStore.fetchGitStatus(projectId),
    dashboardStore.fetchSummary(projectId),
  ])
  emit('refresh-overview')
}

async function restoreGitArchive(item: GitArchiveItem) {
  if (gitArchiveBusy.value) return
  gitArchiveBusy.value = true
  gitArchiveError.value = ''
  try {
    await postRequest(`/api/v1/groups/${encodeURIComponent(item.group_id)}/git/archive/restore`, {})
    showToast(t('main.git_archive_panel.restored'), 'success')
    await Promise.all([fetchGitArchives(), refreshAfterGitArchive()])
  } catch (error: any) {
    gitArchiveError.value = gitArchiveFailure(error)
    showToast(gitArchiveError.value, 'danger')
  } finally {
    gitArchiveBusy.value = false
  }
}

async function purgeGitArchives() {
  if (
    gitArchiveBusy.value
    || !gitArchivePurgeConfirmed.value
    || gitArchivePicked.value.length === 0
  ) return
  gitArchiveBusy.value = true
  gitArchiveError.value = ''
  try {
    for (const groupId of gitArchivePicked.value) {
      await api.delete(`/api/v1/groups/${encodeURIComponent(groupId)}/git/archive`)
    }
    gitArchivePicked.value = []
    gitArchivePurgeConfirmed.value = false
    showToast(t('main.git_archive_panel.purged'), 'success')
    await Promise.all([fetchGitArchives(), refreshAfterGitArchive()])
  } catch (error: any) {
    gitArchiveError.value = gitArchiveFailure(error)
    showToast(gitArchiveError.value, 'danger')
  } finally {
    gitArchiveBusy.value = false
  }
}

function shortGitSha(value: string | null): string {
  return value ? value.slice(0, 7) : '-'
}

function formatGitArchiveTime(value: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale.value)
}
const props = withDefaults(defineProps<{
  overviewRefreshToken?: number
}>(), {
  overviewRefreshToken: 0,
})
const docTypeStore = useDocTypeStore()
const { showToast } = useToast()
const {
  issueToken,
  advanceWithWorkPlanScope,
  requestReview,
  requestWorkflowDecision,
  composeMention,
} = useFlowGateToken()
// R0001 group 0015 / NR0003 rev4 — record a successful mention copy as server user-state so the
// document header badge persists. Best-effort (never blocks the copy the user already made).
const { recordMentionCopy } = useMentionCopy()
const emit = defineEmits<{
  'create-requirement': []
  'related-doc-created': [payload: { docId: string; openAfter: boolean; projectId: string }]
  'refresh-overview': []
}>()
const tabsStore = useTabsStore()
const projectStore = useProjectStore()
const explorerStore = useExplorerStore()
// 0234 B0001: runtime provider selection, shared with AppHeader. Surfaced in the
// continuous-work dialog so its auto-started run's provider is confirmable (RC3).
const aiProviderStore = useAiProviderStore()
const dashboardStore = useDashboardStore()
const dashboardEntry = computed(() => dashboardStore.currentEntry)

const tabs = computed(() => tabsStore.tabs)
const activeTabId = computed(() => tabsStore.activeTabId)
const activeTab = computed(() => tabsStore.activeTab)

// Template refs for DocHeader instances (keyed by tab id).
const docHeaderRefs = reactive<Record<string, any>>({})
const mdViewerRefs = reactive<Record<string, any>>({})
const textViewerRefs = reactive<Record<string, any>>({})
const convViewRefs = reactive<Record<string, any>>({})
const workPlanEditorRefs = reactive<Record<string, any>>({})
const qStatuses = reactive<Record<string, string>>({})
const headerRevision = ref(0)

// 0260: load providers for the active document project instead of relying on AppHeader order.
// The review-request button label stays unchanged; only its primary click action switches.
const activeDocProjectId = computed(() => {
  const tabId = activeTabId.value
  if (!tabId) return ''
  return exposedValue<string>(docHeaderRefs[tabId]?.docProjectId)
    ?? tabId.split('.')[0]
    ?? ''
})
// Per R0001 ("존재하지 않을 경우 기존과 같다") and NR0003 (recommendation 2 / caution notes), mention-copy is
// the default in every state EXCEPT a confirmed provider. AI invoke becomes the default only
// once the active document project's provider request has completed successfully with a
// non-empty result — loading, a query error, a project mismatch, or an empty result all keep
// the existing mention-copy behavior so an unresolved/errored lookup never routes to AI.
const activeProjectHasAiProvider = computed(() => {
  const projectId = activeDocProjectId.value
  if (!projectId) return false
  return aiProviderStore.loadedProjectId === projectId
    && !aiProviderStore.loading
    && !aiProviderStore.error
    && aiProviderStore.providers.length > 0
})
watch(activeDocProjectId, (projectId) => {
  if (projectId) void aiProviderStore.ensureLoaded(projectId)
}, { immediate: true })

function resolveTemplateRef(el: any) {
  return Array.isArray(el) ? el.find(Boolean) ?? null : el
}

// Function ref: Vue invokes this with the live instance on mount (and on each
// re-render) and with null on unmount. Binding the registry here — instead of a
// watch over a `ref=` template ref collected inside v-for — guarantees the
// registry always points at the *live* DocHeader/viewer instance, even for a
// same-tab unmount→remount (e.g. inline edit). The previous watch-on-ref never
// fired on in-place array mutation, so it stuck on the dead instance and the
// action bar derived state from it (the workflow-decision-button bug, gp 0064).
function bindActiveRef(registry: Record<string, any>, tabId: string, el: any) {
  const instance = resolveTemplateRef(el)
  if (instance) registry[tabId] = instance
  else if (tabId in registry) delete registry[tabId]
}

// Single source of truth: the write path (workflow-decision click) reads the
// SAME registry the action bar reads, so a decision applied to the live
// instance is always reflected by the action bar (NR0003 §6.2).
function getActiveDocHeader() {
  const tabId = activeTabId.value
  return tabId != null ? docHeaderRefs[tabId] ?? null : null
}

function openWorkflowDecisionForActive() {
  getActiveDocHeader()?.openWorkflowDecisionModal?.()
}

// NR0003 (group 0064) §6.2 — MainPanel directly owns the workflow decision result.
// After [Workflow Decision], the action bar must flip workflow → next immediately from the
// POST 201 result, with NO dependency on the DocHeader instance lifetime, its exposed
// refs, headerRevision, or whether the follow-up detail GET succeeds. This per-tab
// override holds the decided signals until the live DocHeader state catches up; once it
// does, the override is dropped so later transitions (advance / reject / done) flow
// through live data and are never frozen at 'next'.
interface WorkflowDecidedOverride {
  reviewStatus: string
  steps: string[]
  headType: string | null
  headLabel: string | null
}
const decidedOverrides = reactive<Record<string, WorkflowDecidedOverride>>({})

function onWorkflowDecided(payload: {
  docId: string
  reviewStatus: string
  steps: string[]
  headType: string | null
  headLabel: string | null
}) {
  decidedOverrides[payload.docId] = {
    reviewStatus: payload.reviewStatus,
    steps: payload.steps,
    headType: payload.headType,
    headLabel: payload.headLabel,
  }
  headerRevision.value += 1
}

// The live DocHeader reports a decided state once its backfill/SSE/advance lands. The
// two-signal test mirrors workflowViewState's wfDecided predicate.
function liveReportsDecided(tabId: string): boolean {
  const h = docHeaderRefs[tabId]
  const rs = exposedValue<string | null>(h?.docReviewStatus) ?? ''
  const ht = exposedValue<string | null>(h?.workflowHeadType)
  return rs.startsWith('wf_') || ht != null
}

// While a decision override is active and live data has not yet caught up, the override
// is authoritative for the decided signals (review status / head / steps).
function activeOverride(tabId: string): WorkflowDecidedOverride | undefined {
  return liveReportsDecided(tabId) ? undefined : decidedOverrides[tabId]
}

function onDocHeaderUpdated(payload?: { docId: string }) {
  headerRevision.value += 1
  // GC the override once live data is decided, so the override never freezes a later
  // legitimate transition (advance / reject / wf_done).
  const id = payload?.docId
  if (id && decidedOverrides[id] && liveReportsDecided(id)) {
    delete decidedOverrides[id]
  }
}

const fullViewVisible = ref(false)
const fullViewTab = ref<Tab | null>(null)
// The chat's full view is a move, not a second mount: the card teleports its ConversationView
// into the dialog body below. The target only exists while the dialog is open, so the flag is
// raised a tick after opening (target rendered) and lowered a tick before closing (so the node
// is back in its card before the dialog's DOM — and anything still inside it — is removed).
const CONV_FULL_VIEW_HOST = '.document-modal__body--conversation'
const convFullViewActive = ref(false)
// Teleport resolves `to` when it mounts and then reuses that result; it only re-resolves when
// `to` itself changes. The chat card mounts long before any dialog exists, so a constant
// selector would have been resolved to null back then and toggling `disabled` alone would move
// the chat into nothing. Swinging `to` null<->selector forces the fresh lookup. Both props read
// the same flag so they can never disagree — an enabled teleport always has a target.
const convFullViewOn = computed(
  () => convFullViewActive.value && fullViewTab.value?.typeCode === 'CH',
)
const convFullViewHost = computed(() => (convFullViewOn.value ? CONV_FULL_VIEW_HOST : null))
const fullViewIcon = computed(() => {
  if (fullViewTab.value?.typeCode === 'CH') return 'chats'
  return fullViewTab.value?.type === 'text' ? 'file-text' : 'markdown-logo'
})
const editVisible = ref(false)
const editTab = ref<Tab | null>(null)
const editContent = ref('')
const editFrontmatter = ref('')
const editBody = ref('')
const editLoading = ref(false)
const editSaving = ref(false)
const editError = ref('')
const editSourceEtag = ref('')
const headerEditModeVisible = ref(false)
const editFullContent = ref('')
const nextActionModalVisible = ref(false)
const nextActionModalStep = ref('')
const nextActionModalTabId = ref('')
const nextActionModalDocRef = ref('')
const nextActionModalProjectId = ref('')
const nextActionModalGroupId = ref('')
const nextActionModalModuleName = ref('')
const nextActionModalTypeCode = ref('')
const nextActionModalCurrentType = ref('')
const nextActionModalInitialDocs = ref<string[]>([])
// flowgate.default.0395 D0007 §6.3 / T0021: work-plan creation dialog, opened from
// the [Workflow Sequence] section of the R/B root.
const workPlanCreateVisible = ref(false)
const workPlanCreateParentDocId = ref('')
const workPlanCreateProjectId = ref('')
const workPlanCreateGroupId = ref('')
// flowgate.default.0405 P0004: the WP-only proposal window. It reuses the three
// workPlanCreate* context refs (same parent doc / project / group) and adds the state the
// four-button row needs — which request is in flight and the one reason line under it.
const workPlanProposalVisible = ref(false)
const workPlanProposalBusy = ref<'' | 'copy' | 'ai'>('')
const workPlanProposalNotice = ref('')
const workPlanProposalAiActive = computed(() => {
  const groupId = workPlanCreateGroupId.value
  return !!groupId && (
    aiInvokeRunsStore.isGroupRunning(groupId)
    || aiInvokeRunsStore.isGroupInlineVisible(groupId)
  )
})
// Continuous work (R0001 group 0086): sequence selection feeds a consent gate that offers
// either an in-app provider run or the external-AI continuous mention path.
const continuousDialogVisible = ref(false)
const continuousWarnVisible = ref(false)
const continuousTabId = ref('')
const continuousDocRef = ref('')
const continuousProjectId = ref('')
const continuousGroupId = ref('')
const continuousTargetSeq = ref<number | null>(null)
const continuousTargetType = ref('')
const continuousTargetLabel = ref('')
const continuousReviewMode = ref(false)
// 0409 B0001 rejection: the default selection for an unmanned continuous chain is [Auto-approve] (DEFAULT_INSTRUCTION_MODE).
const continuousInstructionMode = ref<ContinuationInstructionMode>(DEFAULT_INSTRUCTION_MODE)
// 0317 T0010 rev4: item_seq -> provider_id overrides chosen in ContinuousWorkDialog; carried
// through the consent gate to the start request (session-scoped, never persisted).
const continuousProviderOverrides = ref<Record<number, string>>({})
// 0346 T0005: [Message] tab values, carried from ContinuousWorkDialog's confirm payload
// through to the consent gate's start request (session-scoped, never persisted).
const continuousDefaultMessage = ref('')
const continuousMessageOverrides = ref<Record<number, string>>({})
// 0414 T0012 / P0007: [검수] 탭 값(item_seq -> 횟수, item_seq -> 검수자 provider id), 위
// 필드들과 똑같이 경고창을 거쳐 시작 요청까지만 사는 세션 값이다. 절대 영속되지 않는다.
const continuousReviewCountOverrides = ref<Record<number, number>>({})
const continuousReviewerOverrides = ref<Record<number, string>>({})
// 0352 T0004 §2/§3.7: ai_direct-only per-item_seq N/T server-auto-approve selection, carried
// the same way the fields above are — session-scoped, never persisted.
const continuousAutoApproveItemSeqs = ref<number[]>([])
// flowgate.default.0400 M0005: the per-hop wall-clock budget (seconds) chosen in
// ContinuousWorkDialog's [Basic settings] timeout section, carried through the consent gate the same
// way the fields above are — session-scoped, never persisted as a project setting.
const continuousStepTimeoutSec = ref(3600)
// flowgate.default.0443 T0002 (R0001): the dialog's "재시작 횟수" pick, carried the same
// way stepTimeoutSec above is — session-scoped, never persisted as a project setting.
const continuousRestartMaxAttempts = ref(1)
const continuousStepCount = ref(0)
// R0001 "워크플로 결정부터": true when the run is started before the workflow is decided,
// so the first link is the workflow decision (issued via requestWorkflowDecision, not advance).
const continuousFromDecision = ref(false)
// Mention-add dialog (R0001 group 0004 / L0007). Holds the issued token + selected docs
// so the chosen message can be appended to the mention text at confirm time.
const mmDialogVisible = ref(false)
const mmDialogProjectId = ref('')
const mmDialogDocType = ref('')
const mmDialogDocTypes = ref<{ code: string; label: string }[]>([])
const mmDialogCandidates = ref<MessageEntry[]>([])
const mmDialogToken = ref<IssuedToken | null>(null)
const mmDialogSelectedDocs = ref<string[] | undefined>(undefined)
const mmDialogContext = ref<'next' | 'continuous'>('next')
const nextEmptyDocModalVisible = ref(false)
const nextEmptyDocProjectId = ref('')
const nextEmptyDocGroupId = ref('')
const nextEmptyDocPrevDocId = ref('')
const nextEmptyDocType = ref('')
const nextEmptyDocModule = ref('none')
const nextEmptyDocModuleTitle = ref<string | null>(null)
// R0001 #2 (0048): auto-approved document creation
const createApprovedConfirmVisible = ref(false)
const createApprovedTabId = ref('')
const createApprovedSubmitting = ref(false)
const commandSelectorVisible = ref(false)
const pendingEnvOverrides = ref<Record<string, string> | null>(null)
// AI invoke dialog state (0187; parallel-invoke scopes group 0223)
type AiInvokeScope = 'new' | 'edit' | 'workflow_decide' | 'chat' | 'rework' | 'review' | 'vr_correction' | 'next_step_message' | 'design_handoff'
const aiInvokeVisible = ref(false)
const aiInvokeProject = ref('')
const aiInvokeModule = ref<string | null>(null)
const aiInvokeGroup = ref('')
const aiInvokeDocRef = ref('')
const aiInvokeSequenceDocRef = ref('')
const aiInvokeActionScope = ref<AiInvokeScope>('new')
const aiInvokeInitialMode = ref<'single' | 'continuous'>('single')
const aiInvokeInitialTargetSeq = ref<number | null>(null)
const aiInvokeContinuationReviewMode = ref(false)
const aiInvokeContinuationInstructionMode = ref<ContinuationInstructionMode>(DEFAULT_INSTRUCTION_MODE)
// 0352 T0004 §2/§3.7: ai_direct-only per-item_seq N/T server-auto-approve selection forwarded
// onto the start request, same lifetime as the mode above.
const aiInvokeContinuationAutoApproveItemSeqs = ref<number[]>([])
// 0317 T0010 rev4: item_seq -> provider_id overrides forwarded onto the start request.
const aiInvokeProviderOverrides = ref<Record<number, string>>({})
// 0346 T0005: [Message] tab values forwarded onto the start request.
const aiInvokeDefaultMessage = ref('')
const aiInvokeMessageOverrides = ref<Record<number, string>>({})
// 0414 T0012: [검수] 탭 값을 시작 요청으로 넘기는 자리. openAiInvokeDialog 가 프리셋 없는
// 진입점에서는 빈 맵으로 되돌리므로, 앞선 연속 실행의 검수 선택이 다음 single/continuous
// 실행으로 새지 않는다.
const aiInvokeReviewCountOverrides = ref<Record<number, number>>({})
const aiInvokeReviewerOverrides = ref<Record<number, string>>({})
// flowgate.default.0400 M0005: the per-hop wall-clock budget (seconds) forwarded onto the
// start request. null means "no explicit pick" (e.g. entry points other than
// ContinuousWorkDialog), and the server falls back to its own default.
const aiInvokeStepTimeoutSec = ref<number | null>(null)
// flowgate.default.0443 T0002 (R0001): the restart-count pick forwarded onto the start
// request. null means "no explicit pick" (entry points other than ContinuousWorkDialog),
// and the server falls back to its own default.
const aiInvokeRestartMaxAttempts = ref<number | null>(null)
const aiInvokeAutoStart = ref(false)
const aiInvokeRunsStore = useAiInvokeRunsStore()
// 0351 T4: a conversation-turn search result (GroupExplorer) opens this CH tab and
// asks the mounted ConversationView to scroll to one turn. GroupExplorer does not
// hold a reference to that instance — this component does (convViewRefs) — so the
// two talk through a window event, same idiom as fg:group_tree_changed. The ref may
// not exist yet if this is a brand new tab; retry once past the next paint before
// giving up (dropping a jump is a missed scroll, not a broken feature).
function onConversationJumpSeq(event: Event) {
  const detail = (event as CustomEvent<{ docId?: string; seq?: number }>).detail
  const docId = detail?.docId
  const seq = detail?.seq
  if (!docId || !seq) return
  void nextTick(() => {
    const target = convViewRefs[docId]
    if (target?.jumpToSeq) {
      void target.jumpToSeq(seq)
      return
    }
    requestAnimationFrame(() => {
      void convViewRefs[docId]?.jumpToSeq?.(seq)
    })
  })
}

onMounted(() => {
  window.addEventListener('fg:conversation_jump_seq', onConversationJumpSeq)
  void aiInvokeRunsStore.bootstrap()
})
onBeforeUnmount(() => {
  window.removeEventListener('fg:conversation_jump_seq', onConversationJumpSeq)
})
const activeAiInvokeGroupId = computed(() => {
  const tab = activeTab.value
  if (!tab) return ''
  return tab.gitGroupId
    ?? groupIdFromDocId(tab.id)
    ?? exposedValue<string>(docHeaderRefs[tab.id]?.groupId)
    ?? ''
})
const aiRunBootstrapPending = computed(() => aiInvokeRunsStore.bootstrapPending)
// 0251 B0001: a chat's own AI call is a group-scoped run like any other, so the run surface
// (inset:0 over .doc-main) used to bury the whole conversation the moment a message was sent —
// chat progress belongs on the send button instead. 0258 B0001 then drew the other half of the
// line: a run aimed at ANOTHER document is a next-document transition and must still cover the
// chat, exactly like every other doc type. 0378 rebuilt this area for the durable mutation
// leases and dropped the branch entirely (suppress-doc-ref removed, "CH uses the same rule"),
// which put the chat back under its own run's cover — the regression reported in 0386 B0001.
// Restore the branch here rather than through a prop, so the run/document branches stay the
// mutually exclusive pair 0378 requires and the lease's document-side lockout (action bar,
// info panel, sticky footer) keeps deriving from one flag.
const activeChatOwnRun = computed(() => {
  const tab = activeTab.value
  if (!tab || tab.typeCode !== 'CH') return false
  return aiInvokeRunsStore.runsByGroup[activeAiInvokeGroupId.value]?.docRef === tab.id
})
const activeGroupRunActive = computed(() =>
  !activeChatOwnRun.value
  && aiInvokeRunsStore.isGroupInlineVisible(activeAiInvokeGroupId.value),
)
const activeGroupRunInlineVisible = computed(() => {
  if (activeChatOwnRun.value) return false
  const run = aiInvokeRunsStore.runsByGroup[activeAiInvokeGroupId.value]
  return activeGroupRunActive.value
    || run?.phase === 'paused'
    || run?.phase === 'finished'
    || run?.phase === 'lost'
})
const aiRunDocumentLocked = computed(() =>
  aiRunBootstrapPending.value || activeGroupRunActive.value,
)

watch(activeGroupRunActive, async (running, wasRunning) => {
  if (running) {
    editVisible.value = false
    editTab.value = null
    headerEditModeVisible.value = false
    editDropdownTabId.value = null
    if (rejectDialogVisible.value) {
      rejectDialogVisible.value = false
      onRejectDialogClosed()
    }
    if (fullViewVisible.value) await closeFullView()
    aiInvokeVisible.value = false
    nextActionModalVisible.value = false
    workPlanCreateVisible.value = false
    workPlanProposalVisible.value = false
    workPlanProposalBusy.value = ''
    workPlanProposalNotice.value = ''
    continuousDialogVisible.value = false
    continuousWarnVisible.value = false
    designHandoffVisible.value = false
    timeMachineVisible.value = false
    returnConfirmVisible.value = false
    return
  }
  if (wasRunning) {
    await nextTick()
    const docId = activeTabId.value
    if (!docId) return
    void docHeaderRefs[docId]?.fetchDoc?.(docId)
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
      detail: { doc_id: docId, refresh_key: `ai-run-finished:${Date.now()}` },
    }))
    window.dispatchEvent(new CustomEvent('fg:qa_refresh', { detail: { doc_id: docId } }))
    void textViewerRefs[docId]?.loadContent?.()
  }
})
const aiInvokeSelectedDocs = ref<string[] | null>(null)
const aiInvokeMessages = ref<string[] | null>(null)
const aiInvokeRejectReason = ref<string | null>(null)
const aiInvokeDesignTypes = ref<string[] | null>(null)
const aiInvokeDesignMode = ref<string | null>(null)
const aiInvokeDesignFirstLabel = ref<string | null>(null)
const editDropdownTabId = ref<string | null>(null)
const textWrapEnabled = ref(readTextWrapEnabled())

// DesignHandoffDialog state
const designHandoffVisible = ref(false)
const designHandoffDocRef = ref('')
const designHandoffProjectId = ref('')
const designHandoffGroupId = ref('')
const designHandoffDefaultTypes = ref<('D' | 'P' | 'L' | 'DB')[]>([])
const designHandoffNextStepLabel = ref('')
// The viewed document the handoff advances — keyed for the mention-copied badge (NR0003 rev4).
const designHandoffDocId = ref('')

// ReviewRejectDialog state
const rejectDialogRef = ref<InstanceType<typeof ReviewRejectDialog> | null>(null)
const rejectDialogVisible = ref(false)
const rejectDialogDocId = ref('')
const rejectDialogDocName = ref('')
const rejectDialogTabId = ref('')
// 0419 T0006: true when the dialog was opened from the sidebar [Edit] entry point to
// correct the latest rejection's wording (PATCH), false for the ordinary new-reject
// flow (POST review_transitions/reject) — see onEditRejectionRequested / onReviewRejected.
const rejectDialogEditMode = ref(false)

const rejectDialogExistingReason = computed<string | null>(() => {
  const tabId = rejectDialogTabId.value
  if (!tabId) return null
  return exposedValue<string>(docHeaderRefs[tabId]?.rejectionReason) ?? null
})

// Time-machine (AC reject → reopen the workflow at an earlier step) — M042 §3.4
interface TimeMachineStep {
  docId: string
  seq: number
  typeCode: string | null
  title: string | null
}

interface ReturnPointInfo {
  exists: boolean
  front_seq: number | null
  front_label: string | null
  restorable_count: number
  current_min_seq: number | null
  destination_default: number | null
  destination_min: number | null
}
const timeMachineVisible = ref(false)
const timeMachineDocId = ref('')
const timeMachineSteps = ref<TimeMachineStep[]>([])
const timeMachineLoading = ref(false)
// 0018 R0001 — strip-click time-machine pre-selects the clicked step's doc in the picker.
const timeMachinePreselectDocId = ref<string | null>(null)
// 0332 D0005 §6.3/§6.4 — what the rewind dialog says about source commits: the preview it
// opens with, and the cancel result it turns into a result screen for. `null` result means
// "no result screen" — either the rewind fully succeeded or it has not run yet.
const timeMachineCommitPreview = ref<any | null>(null)
const timeMachineCancelResult = ref<any | null>(null)
const timeMachineRetrying = ref(false)
// The document [다시 시도] resolves the group from. NOT timeMachineDocId: that may be the
// AC doc, and reopen deletes it.
const timeMachineCancelDocId = ref('')
// The rewound step to open once the dialog actually closes. The success path closes
// immediately; the result-screen path opens it when the user presses [닫기] (D0005 §6.4).
const timeMachinePendingStep = ref<TimeMachineStep | null>(null)
const returnPoints = reactive<Record<string, ReturnPointInfo>>({})
// 0142 R0001 — cached workflow sequence per return-point root, used to map a rewound step's
// strip cell to its seq for the reverse time-machine highlight/click (getReturnTargets).
const returnSequences = reactive<Record<string, SequenceSlot[]>>({})
const returnPointRestoring = ref(false)
// 0142 rework — a click on a return-target cell no longer restores instantly. It opens a
// confirm dialog (symmetric with the backward time-machine's dialog); this holds the pending
// restore until the user confirms. Cleared on confirm/cancel.
const returnConfirmVisible = ref(false)
const pendingReturn = ref<{
  tabId: string
  docId: string
  destinationSeq: number
  destinationDocId: string
  destinationLabel: string | null
  destinationType: string | null
} | null>(null)
const returnConfirmMessage = computed(() =>
  pendingReturn.value
    ? t('main.time_machine.return_confirm_message', { doc: shortDocCode(pendingReturn.value.destinationDocId) })
    : '',
)

const DESIGN_TYPES = new Set(['D', 'P', 'L', 'DB'])

function readTextWrapEnabled(): boolean {
  try {
    return localStorage.getItem('flowgate:text-viewer:wrap-lines') === '1'
  } catch {
    return false
  }
}

function getWorkflowDesignTypes(tabId: string): ('D' | 'P' | 'L' | 'DB')[] {
  const h = docHeaderRefs[tabId]
  if (!h) return []
  const steps = exposedValue<string[] | null>(h.workflowSteps)
  if (steps) {
    return steps.filter(s => DESIGN_TYPES.has(s)) as ('D' | 'P' | 'L' | 'DB')[]
  }
  const headType = exposedValue<string>(h.workflowHeadType)
  if (headType && DESIGN_TYPES.has(headType)) return [headType as 'D' | 'P' | 'L' | 'DB']
  return []
}

function getReviewRequestLabel(tabId: string): string {
  if (getWorkflowDesignTypes(tabId).length > 0) {
    return t('main.review_action_bar.btn_design_handoff')
  }
  const nextLabel = getNextStepLabel(tabId)
  return nextLabel
    ? t('main.review_action_bar.btn_next_step', { step: nextLabel })
    : t('main.review_action_bar.btn_review_request')
}

function toggleEditDropdown(tabId: string) {
  editDropdownTabId.value = editDropdownTabId.value === tabId ? null : tabId
}

function closeEditDropdown() {
  editDropdownTabId.value = null
}

function toggleHeaderEditMode() {
  if (!headerEditModeVisible.value) {
    editFullContent.value = editFrontmatter.value ? editFrontmatter.value + '\n' + editBody.value : editBody.value
  } else {
    const parsed = parseFrontmatter(editFullContent.value)
    editFrontmatter.value = parsed.frontmatter
    editBody.value = parsed.body
  }
  headerEditModeVisible.value = !headerEditModeVisible.value
}

function parseFrontmatter(content: string): { frontmatter: string; body: string } {
  if (!content.startsWith('---\n')) return { frontmatter: '', body: content }
  const closingIndex = content.indexOf('\n---', 3)
  if (closingIndex === -1) return { frontmatter: '', body: content }
  const fmEnd = closingIndex + 4
  const frontmatter = content.slice(0, fmEnd)
  const rest = content.slice(fmEnd)
  const body = rest.startsWith('\n') ? rest.slice(1) : rest
  return { frontmatter, body }
}

function getTabSourcePath(tab: Tab): string {
  return tab.mdPath ?? tab.path
}

function isDocumentTab(tab: Tab): boolean {
  return !!tab.typeCode
}

function canDirectEditSource(tab: Tab): boolean {
  return (
    isFileTab(tab)
    && !!tab.projectId
    && !!getTabSourcePath(tab)
    && !tab.gitGroupId
    && (tab.type === 'md' || tab.type === 'text')
  )
}

function sourceContentUrl(tab: Tab): string {
  const base =
    `/api/v1/projects/${encodeURIComponent(tab.projectId ?? '')}/files/src-content`
    + `?path=${encodeURIComponent(getTabSourcePath(tab))}`
  return tab.gitGroupId
    ? `${base}&group_id=${encodeURIComponent(tab.gitGroupId)}`
    : base
}

// Group file tabs stay read-only. src-content resolves only the BASE checkout, so
// enabling direct editing for a group tab would write the wrong tree.
function isDeletedGroupFileTab(tab: Tab | null): boolean {
  return !!tab
    && isFileTab(tab)
    && !!tab.projectId
    && !!tab.gitGroupId
    && explorerStore.isGroupDeletedPath(tab.projectId, tab.gitGroupId, getTabSourcePath(tab))
}

// 0340 T0004 (B0001 / NR0003 §3): the tree blocks every normal open entry point,
// but a tab may already be open when a worker deletes its path. React to the same
// status map and tear down that stale editor/full-view state before a committed blob
// can make the deleted file appear to still exist.
watch(
  () => tabs.value
    .filter((tab) => isDeletedGroupFileTab(tab))
    .map((tab) => tab.id)
    .join('|'),
  (deletedTabIds) => {
    if (!deletedTabIds) return
    const ids = new Set(deletedTabIds.split('|'))
    if (fullViewTab.value && ids.has(fullViewTab.value.id)) void closeFullView()
    if (editTab.value && ids.has(editTab.value.id)) closeEditModal()
    for (const tab of [...tabs.value]) {
      if (ids.has(tab.id)) tabsStore.closeTab(tab.id)
    }
  },
  { immediate: true },
)

if (typeof window !== 'undefined') {
  window.addEventListener('click', () => {
    if (editDropdownTabId.value !== null) closeEditDropdown()
  })
}

function onEditDirect(tab: Tab) {
  closeEditDropdown()
  openEditModal(tab)
}

async function onEditMentCopy(tab: Tab) {
  closeEditDropdown()
  const project = exposedValue<string>(docHeaderRefs[tab.id]?.docProjectId) ?? projectStore.currentProjectId
  const groupId = exposedValue<string>(docHeaderRefs[tab.id]?.groupId)
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId as string)
  // B0001: issue the token INSIDE the deferred producer so the clipboard write stays within
  // the click's user activation (the prior code awaited the token first, letting it lapse).
  let token: IssuedToken | null = null
  await copyMentionDeferred(
    async () => {
      token = await issueToken({
        project,
        ...(gParts?.module != null ? { module: gParts.module } : {}),
        group: gParts?.groupCode ?? (groupId as string),
        doc_ref: tab.id,
        action_scope: 'edit',
      })
      if (!token) throw new ClipboardAbort()
      return composeMention(token)
    },
    {
      tabId: tab.id,
      kind: 'edit',
      successToast: t('main.main_panel.toast_mention_copied'),
      aborted: () => token == null,
    },
  )
}

async function onEditInvokeCommand(tab: Tab) {
  closeEditDropdown()
  const project = exposedValue<string>(docHeaderRefs[tab.id]?.docProjectId) ?? projectStore.currentProjectId
  const groupId = exposedValue<string>(docHeaderRefs[tab.id]?.groupId)
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId as string)
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? (groupId as string),
    doc_ref: tab.id,
    action_scope: 'edit',
  })
  if (!token) return
  pendingEnvOverrides.value = {
    FLOWGATE_TOKEN: token.raw_token,
    FLOWGATE_SCRATCH: token.scratch_dir,
  }
  commandSelectorVisible.value = true
}

// ── AI invoke entry points (0187 D0004 §6): same spots as the command runner,
// but the token is minted server-side and never reaches the browser. ──────────
function openAiInvokeDialog(
  project: string,
  groupId: string,
  docRef: string,
  actionScope: AiInvokeScope,
  preset?: {
    mode?: 'single' | 'continuous'
    targetSeq?: number | null
    reviewMode?: boolean
    instructionMode?: ContinuationInstructionMode
    // 0352 T0004 §2/§3.7: ai_direct-only per-item_seq N/T server-auto-approve selection
    // chosen in ContinuousWorkDialog.
    autoApproveItemSeqs?: number[]
    // 0317 T0010 rev4: item_seq -> provider_id overrides chosen in ContinuousWorkDialog.
    providerOverrides?: Record<number, string>
    // 0346 T0005: [Message] tab values chosen in ContinuousWorkDialog.
    defaultMessage?: string
    messageOverrides?: Record<number, string>
    // 0414 T0012: [검수] 탭 값 chosen in ContinuousWorkDialog.
    reviewCountOverrides?: Record<number, number>
    reviewerOverrides?: Record<number, string>
    // flowgate.default.0400 M0005: the per-hop wall-clock budget (seconds) chosen in
    // ContinuousWorkDialog's timeout section.
    stepTimeoutSec?: number | null
    // flowgate.default.0443 T0002 (R0001): the restart-count pick chosen in
    // ContinuousWorkDialog.
    restartMaxAttempts?: number | null
    autoStart?: boolean
    // 0242 NR0003 recommendation 2: sequence-owning root for the continuous-target picker, when it is
    // NOT the same document the run acts on (docRef). /workflow/sequence is keyed by the root.
    sequenceDocRef?: string
  },
  extras?: {
    selectedDocs?: string[] | null
    messages?: string[] | null
    rejectReason?: string | null
    designTypes?: string[] | null
    designMode?: string | null
    designFirstLabel?: string | null
  },
) {
  const gParts = splitGroupId(groupId)
  aiInvokeProject.value = project
  aiInvokeModule.value = gParts?.module ?? null
  aiInvokeGroup.value = gParts?.groupCode ?? groupId
  aiInvokeDocRef.value = docRef
  aiInvokeSequenceDocRef.value = preset?.sequenceDocRef ?? docRef
  aiInvokeActionScope.value = actionScope
  aiInvokeInitialMode.value = preset?.mode ?? 'single'
  aiInvokeInitialTargetSeq.value = preset?.targetSeq ?? null
  aiInvokeContinuationReviewMode.value = !!preset?.reviewMode
  aiInvokeContinuationInstructionMode.value = preset?.instructionMode ?? DEFAULT_INSTRUCTION_MODE
  aiInvokeContinuationAutoApproveItemSeqs.value = preset?.autoApproveItemSeqs ?? []
  aiInvokeProviderOverrides.value = preset?.providerOverrides ?? {}
  aiInvokeDefaultMessage.value = preset?.defaultMessage ?? ''
  aiInvokeMessageOverrides.value = preset?.messageOverrides ?? {}
  aiInvokeReviewCountOverrides.value = preset?.reviewCountOverrides ?? {}
  aiInvokeReviewerOverrides.value = preset?.reviewerOverrides ?? {}
  aiInvokeStepTimeoutSec.value = preset?.stepTimeoutSec ?? null
  aiInvokeRestartMaxAttempts.value = preset?.restartMaxAttempts ?? null
  aiInvokeAutoStart.value = !!preset?.autoStart
  aiInvokeSelectedDocs.value = extras?.selectedDocs ?? null
  aiInvokeMessages.value = extras?.messages ?? null
  aiInvokeRejectReason.value = extras?.rejectReason ?? null
  aiInvokeDesignTypes.value = extras?.designTypes ?? null
  aiInvokeDesignMode.value = extras?.designMode ?? null
  aiInvokeDesignFirstLabel.value = extras?.designFirstLabel ?? null
  aiInvokeVisible.value = true
}

function onEditInvokeAi(tab: Tab) {
  closeEditDropdown()
  const project = exposedValue<string>(docHeaderRefs[tab.id]?.docProjectId) ?? projectStore.currentProjectId
  const groupId = exposedValue<string>(docHeaderRefs[tab.id]?.groupId)
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  // 0242 NR0003 recommendation 2: the run edits THIS document (tab.id), but the continuous-target picker
  // reads /workflow/sequence, which only answers for the sequence root. Resolve the root the
  // same way every other workflow entry point does — a member doc (T/TR/…) would find no
  // sequence and the picker would fall back to "start from the workflow decision".
  openAiInvokeDialog(project, groupId as string, tab.id, 'edit', {
    sequenceDocRef: nextActionDocRef(tab.id),
  })
}

// 0366 T0004 (rev3 rejection): the proceed dialog ([Proceed to Next Step]) still opened the OLD
// standalone invoke dialog here — the very AiInvokeDialog('new') entry that was deleted from the
// action bar. Route it through the same continuous-work flow the action-bar [Invoke AI] now uses
// (ContinuousWorkDialog → consent gate → AiInvokeDialog), so both entries open the same dialog.
function onNextActionInvokeAi(_selectedDocs?: string[]) {
  const tabId = nextActionModalTabId.value
  // 0441 T0004 item 2: [Invoke AI] on a next step the server assembles would open the
  // continuous-work dialog whose run mints the very token this policy withholds. The TS tab
  // is exempt (tabId is passed): from there the unmanned chain reaches the TSR head through
  // advance_workflow's test-run hand-off, which is the supported route, not manual authoring.
  if (blockServerAssembledNextStep(nextActionModalTypeCode.value || getNextStepCode(tabId), tabId)) return
  const h = docHeaderRefs[tabId]
  const project = nextActionModalProjectId.value || (exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId)
  const groupId = nextActionModalGroupId.value || exposedValue<string>(h?.groupId)
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_workflow_info_unavailable'), 'danger')
    return
  }
  continuousTabId.value = tabId
  continuousDocRef.value = nextActionModalDocRef.value || nextActionDocRef(tabId)
  continuousProjectId.value = project
  continuousGroupId.value = groupId
  // Populate the provider selector shown in the continuous-work dialog (RC3).
  void aiProviderStore.ensureLoaded(project)
  continuousDialogVisible.value = true
}

// Q list (dashboard overview)
// group 0022 D0005 §3.7: 'open queries' aggregate — answer-pending question items across
// the project's documents (doc-bound, not independent Q docs). Item: {doc_id, seq, title}.
interface OpenQueryItem {
  doc_id: string
  seq: number
  title: string | null
  // Host document's type code — needed to open the real document (MdViewer loads
  // by doc-id only when typeCode is set). May be null if the doc row is missing.
  type_code: string | null
}

const qList = ref<OpenQueryItem[]>([])
const qListLoading = ref(false)
const qListError = ref('')

async function fetchQList() {
  const projectId = projectStore.currentProjectId
  if (!projectId) return
  qListLoading.value = true
  qListError.value = ''
  try {
    const res = await getRequest<any>('/api/v1/q', { project_id: projectId })
    qList.value = (res.data as any)?.items ?? []
  } catch (e: any) {
    qListError.value = e?.response?.data?.error_message ?? t('main.main_panel.error_q_list_failed')
  } finally {
    qListLoading.value = false
  }
}

// Click opens the host document the Q is bound to (its own type, so the document
// renders with its [Q&A] panel) — NOT a Q-tree viewer. The original bug opened
// type:'md' with no typeCode, so MdViewer's `:doc-id="tab.typeCode ? tab.id : null"`
// got null → no_md_file. Supplying the host doc's type_code loads the real document.
function openDocFromQuery(item: OpenQueryItem) {
  tabsStore.openTab({
    id: item.doc_id,
    title: item.title ? `${item.doc_id} — ${item.title}` : item.doc_id,
    path: '',
    type: 'md',
    typeCode: item.type_code ?? undefined,
  })
}

function getNextStepLabel(tabId: string): string {
  const h = docHeaderRefs[tabId]
  // NR0003 (group 0064) §6.2 — fall back to the MainPanel-owned decision override so the
  // next-step button text is available immediately after a decision, even when the live
  // DocHeader has not (yet) reported the head (dead instance / failed detail GET).
  const override = activeOverride(tabId)
  const headType = exposedValue<string>(h?.workflowHeadType) ?? override?.headType ?? null
  // AC is the synthetic final-approval step (not a document type) — give it a
  // dedicated label instead of the raw doc-type label.
  if (headType === 'AC') return t('main.review_action_bar.final_approval')
  if (headType) {
    // Prefer the server-confirmed label captured at decision time when present.
    if (override && override.headType === headType && override.headLabel) return override.headLabel
    return docTypeStore.getLabel(headType)
  }
  const steps = exposedValue<string[] | null>(h?.workflowSteps) ?? override?.steps ?? null
  if (!steps || steps.length === 0) return ''
  return docTypeStore.getLabel(steps[0])
}

function exposedValue<T>(value: T | { value: T } | null | undefined): T | null {
  if (value == null) return null
  if (typeof value === 'object' && 'value' in value) return (value as { value: T }).value
  return value as T
}

function getTabTypeCode(tabId: string | null | undefined): string | null {
  if (!tabId) return null
  const tab = tabs.value.find(t => t.id === tabId)
  return tab?.typeCode ?? exposedValue<string | null>(docHeaderRefs[tabId]?.docTypeCode)
}

function returnPointDocId(tabId: string): string {
  return exposedValue<string>(docHeaderRefs[tabId]?.parentRDocId) ?? tabId
}

function hasReturnRegion(rp: ReturnPointInfo | undefined): rp is ReturnPointInfo {
  return !!rp?.exists
    && rp.front_seq != null
    && rp.current_min_seq != null
    && rp.current_min_seq < rp.front_seq
}

// 0142 R0001 — reverse time-machine strip integration. Instead of a stand-alone "return"
// button, the rewound steps light up IN the workflow strip (green, hover-clickable) exactly
// like the backward time-machine's done cells. This resolves which strip indices are those
// return targets, reusing the cached sequence so the highlight matches what a click restores.
function getReturnTargets(tabId: string): number[] {
  const docId = returnPointDocId(tabId)
  const rp = returnPoints[docId]
  if (!hasReturnRegion(rp)) return []
  const items = returnSequences[docId]
  if (!items || items.length === 0) return []
  const cells = getWorkflowViewState(tabId).stepStates
  return returnTargetIndices(cells, items, rp.current_min_seq, rp.front_seq)
}

// 0332 D0005 §6.1 — 칸마다의 소스 커밋 표식. 되돌리기 대상 계산과 같은 슬롯 해석을
// 쓰므로(slotCommitMarks → resolveClickedSlot) 타입이 반복되는 시퀀스에서도 표식과 클릭이
// 서로 다른 칸을 가리킬 수 없다. 시퀀스를 아직 못 받았으면 빈 배열 = 표식 없음.
function getSlotCommits(tabId: string): (SlotCommitMark | null)[] {
  const items = returnSequences[returnPointDocId(tabId)]
  if (!items || items.length === 0) return []
  return slotCommitMarks(getWorkflowViewState(tabId).stepStates, items)
}

async function refreshReturnPoint(tabId: string) {
  const docId = returnPointDocId(tabId)
  if (!docId || !getTabTypeCode(tabId)) return
  try {
    const res = await getRequest<{ return_point?: ReturnPointInfo }>(
      `/api/v1/documents/workflow/${encodeURIComponent(docId)}/return-point`,
    )
    const rp = res.data?.return_point ?? {
      exists: false,
      front_seq: null,
      front_label: null,
      restorable_count: 0,
      current_min_seq: null,
      destination_default: null,
      destination_min: null,
    }
    returnPoints[docId] = rp
    // The sequence maps each strip cell to its slot — for the rewound steps' seq
    // (getReturnTargets) and, since 0332 D0005 §6.1, for each cell's source-commit marker
    // (getSlotCommits). The fetch used to be gated on there being a return region; the
    // marker needs it on every document, so the gate moved INTO getReturnTargets (which
    // already checks hasReturnRegion first) and the rewind behaviour is unchanged.
    try {
      const seqRes = await getRequest<any>(`/api/v1/workflow/${encodeURIComponent(docId)}/sequence`)
      returnSequences[docId] = Array.isArray(seqRes.data?.sequence) ? seqRes.data.sequence : []
    } catch {
      delete returnSequences[docId]
    }
  } catch {
    delete returnPoints[docId]
    delete returnSequences[docId]
  }
}

watch(
  () => [activeTabId.value, headerRevision.value] as const,
  ([tabId]) => {
    if (tabId) void refreshReturnPoint(tabId)
  },
  { immediate: true },
)

// Used by the file-less final-approval panel to show its completed state.
function isCompletedDoc(tabId: string): boolean {
  if (getTabTypeCode(tabId) === 'M') return false
  const s = exposedValue<string | null>(docHeaderRefs[tabId]?.docReviewStatus)
  return s === 'approved' || s === 'wf_done'
}

// The server resolves group-level final approval and terminal lifecycle states.
function canEditDoc(tabId: string): boolean {
  return exposedValue<boolean>(docHeaderRefs[tabId]?.canEditDocument) === true
}

// 0344 TR0008 후속 — 전체 본문을 손으로 고칠 수 없는 문서 타입 (0432.0003-NR §7-2).
// 대화(CH)의 정본은 conversation_turns 표이고, 이관이 끝난 대화의 전체 본문 교체는 서버가
// 409 로 막는다(0344.0005-L §2-16). 화면에도 그 문이 있으면 안 된다. 0344.0008-TR 이 이
// 마무리를 시도했다가 반려된 뒤 후속이 없어 방치돼 있었다.
const NON_EDITABLE_TYPE_CODES = ['CH']

function canEditTab(tab: Tab): boolean {
  if (isFileTab(tab)) return canDirectEditSource(tab)
  // 전체보기 머리의 [편집] 조건에는 CH 검사가 없었다 — 바로 위 주석이 "CH has no [edit]"
  // 이라고 적어 두었는데도 실제로는 노출돼 있었고, 그 문으로 들어가 저장하면 이관된 대화는
  // 아무것도 바뀌지 않았다. 호출부(카드/텍스트/전체보기)마다 막으면 앞으로 늘어날 호출부가
  // 같은 구멍을 다시 파므로 여기 한 곳에서 막는다.
  if (NON_EDITABLE_TYPE_CODES.includes(getTabTypeCode(tab.id) ?? '')) return false
  return canEditDoc(tab.id)
}

function getNextStepCode(tabId: string): string {
  const h = docHeaderRefs[tabId]
  // NR0003 (group 0064) §6.2 — same override fallback as getNextStepLabel.
  const override = activeOverride(tabId)
  const headType = exposedValue<string>(h?.workflowHeadType) ?? override?.headType ?? null
  if (headType) return headType
  const steps = exposedValue<string[] | null>(h?.workflowSteps) ?? override?.steps ?? null
  if (!steps || steps.length === 0) return ''
  return steps[0]
}

function moduleFromGroupId(groupId: string | null | undefined): string | null {
  const parts = (groupId ?? '').split('.')
  return parts.length >= 3 ? parts[1] || null : null
}

function nextActionModuleName(tabId: string, groupId: string): string {
  const docModule = exposedValue<string>(docHeaderRefs[tabId]?.docModule)
  if (docModule && docModule !== 'none') return docModule
  return moduleFromGroupId(groupId) ?? docModule ?? 'none'
}

function guardNextActionAvailable(tabId: string): boolean {
  if (canOpenNextAction(tabId)) return true
  showToast(t('main.main_panel.error_next_step_already_started'), 'warning')
  return false
}

// ── Server-assembled next step (0441 T0004 item 2 / NR0003 recommendation A §4) ───────
// A TSR is assembled by the server out of a finished test run; nobody writes one. The
// action bar no longer OFFERS the manual items for such a head on a non-TS tab, but the
// bar is only the display — these handlers are also reached from the workflow strip's
// next-action list, and a template or event-wiring edit must not be able to re-open the
// hole on its own. Mirrors the server's documents.constants.SERVER_ASSEMBLED_DOC_TYPES,
// which refuses the same requests at /documents/next-empty (422), inbox action:new (409)
// and /workflow/advance (409). Two independent defenses for one policy.
const SERVER_ASSEMBLED_DOC_TYPES = ['TSR']

function isServerAssembledType(typeCode: string | null | undefined): boolean {
  return SERVER_ASSEMBLED_DOC_TYPES.includes(String(typeCode ?? '').toUpperCase())
}

/**
 * True when a manual-authoring action aimed at `typeCode` must be abandoned. Never a silent
 * return: the refusal always goes through the ordinary toast so the user is told which
 * document type it is and where the real action lives.
 *
 * Pass `tabId` for the actions the TS document legitimately keeps ([Copy mention] /
 * [Invoke AI] in the test-run split caret, which hand off to the run). Omit it for
 * [Create empty doc], which is wrong for a server-assembled type on every tab.
 *
 * 0441 TR0005 rev12: this function only ever decides whether to show the refusal toast —
 * it never mints anything itself. [Invoke AI] hands off correctly on its own (the
 * continuous-work dialog sets continuous=true). [Copy mention] does NOT: its caller,
 * onNextActionCopyMention, must branch to the dedicated /documents/test-run-request
 * endpoint for the TS+TSR case BEFORE reaching this check, because the ordinary
 * action_scope='new' token path this function guards can never produce that mention
 * (the server only hands off a TSR to a continuous=true advance, which nothing here sets).
 */
function blockServerAssembledNextStep(
  typeCode: string | null | undefined,
  tabId?: string,
): boolean {
  if (!isServerAssembledType(typeCode)) return false
  if (tabId && (getTabTypeCode(tabId) ?? '').toUpperCase() === 'TS') return false
  showToast(
    t('main.main_panel.error_server_assembled_doc', {
      docType: String(typeCode ?? '').toUpperCase(),
    }),
    'warning',
  )
  return true
}

// AC (final approval) is a file-less workflow step rendered as a real document
// tab. Proceeding to it opens that tab — reusing the existing AC doc when one is
// already the head, else creating it via the BE — so the reviewer approves or
// rejects there. Replaces the old direct "finalize" call, which silently moved
// the R workflow to wf_done with no review step (PM rejected that — M042 §3.1).
async function onOpenFinalApproval(tabId: string) {
  const existingAcId = getWorkflowViewState(tabId).headDocId
  if (existingAcId) {
    openFinalApprovalTab(existingAcId)
    return
  }
  try {
    const res = await postRequest<{ doc_id?: string }>(
      `/api/v1/documents/workflow/final-approval`,
      { doc_id: tabId },
    )
    const acId = res.data?.doc_id
    if (!acId) {
      showToast(t('main.main_panel.error_info_unavailable'), 'danger')
      return
    }
    docHeaderRefs[tabId]?.fetchDoc?.(tabId)
    openFinalApprovalTab(acId)
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(detail, 'danger')
  }
}

function openFinalApprovalTab(acDocId: string) {
  tabsStore.openTab({
    id: acDocId,
    title: `${acDocId} — ${t('main.review_action_bar.final_approval')}`,
    path: '',
    type: 'md',
    typeCode: 'AC',
  })
}

// Navigate to a next-step document that already exists (e.g. after a time-machine
// reopen, step docs are preserved as pending_review) instead of creating a
// duplicate — M042 §3.5.
function openExistingHeadDocTab(tabId: string, headDocId: string) {
  const headType = exposedValue<string>(docHeaderRefs[tabId]?.workflowHeadType) ?? getNextStepCode(tabId)
  const title = exposedValue<string>(docHeaderRefs[tabId]?.headDocTitle) ?? ''
  tabsStore.openTab({
    id: headDocId,
    title: title ? `${headDocId} — ${title}` : headDocId,
    path: '',
    type: 'md',
    typeCode: headType || undefined,
  })
}

// Single entry for the action-bar "proceed" button. AC (final approval) opens the
// approval doc; an already-realised next-step doc is navigated to; otherwise the
// next-document creation flow opens.
function onProceedNextStep(tabId: string) {
  const vs = getWorkflowViewState(tabId)
  if (vs.nextStepCode === 'AC') {
    void onOpenFinalApproval(tabId)
    return
  }
  if (vs.headDocId && vs.headDocId !== tabId) {
    openExistingHeadDocTab(tabId, vs.headDocId)
    return
  }
  if (['R', 'B'].includes(getTabTypeCode(tabId) ?? '')) onNextActionClick(tabId)
  else onNonRNextActionClick(tabId)
}

function onNextActionClick(tabId: string) {
  if (!guardNextActionAvailable(tabId)) return
  const h = docHeaderRefs[tabId]
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  nextActionModalTabId.value = tabId
  nextActionModalDocRef.value = tabId
  nextActionModalCurrentType.value = getTabTypeCode(tabId) ?? 'R'
  nextActionModalInitialDocs.value = []
  nextActionModalStep.value = getNextStepLabel(tabId)
  nextActionModalTypeCode.value = getNextStepCode(tabId)
  nextActionModalProjectId.value = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  nextActionModalGroupId.value = groupId
  nextActionModalModuleName.value = nextActionModuleName(tabId, groupId)
  // 0405 P0004 [Open Proposal Window]: when the next type is a work plan, the shared proceed list
  // does not open. Its five entries don't fit a work plan, and none of its three branches has a
  // place to ask for scope.
  if (openWorkPlanProposalIfWp()) return
  nextActionModalVisible.value = true
}

function onQStatusChanged(payload: { qId: string; status: string; done: boolean }) {
  qStatuses[payload.qId] = payload.status
}

function canShowDocInfoPanel(tabId: string): boolean {
  const tab = tabs.value.find(t => t.id === tabId)
  if (!tab) return false
  const tc = getTabTypeCode(tabId)
  if (!tc) return false
  // DC (group discard) is a terminal record with no review status / history to show.
  if (tc === 'DC') return false
  // CH (conversation/chat): a chat surface, not a reviewed artifact. TR0044.0010
  // rev8 — the info panel's Q&A / review comments / rejection reason are irrelevant noise in a
  // conversation and must not show for CH (reviewer: "only for chat docs, hide Q&A,
  // review comments, and rejection reason").
  if (tc === 'CH') return false
  return true
}

function getWorkflowViewInput(tabId: string): WorkflowViewInput {
  headerRevision.value
  const h = docHeaderRefs[tabId]
  // NR0003 (group 0064) §6.2 — apply the MainPanel-owned decision override while the live
  // DocHeader state is not (yet) decided. This holds the workflow → next transition across
  // a dead/remounted DocHeader instance or a failed/slow detail GET. Once live data reports
  // decided, the override is bypassed (and GC'd in onDocHeaderUpdated).
  const override = activeOverride(tabId)
  const rawSteps = exposedValue<string[] | null>(h?.workflowSteps) ?? override?.steps ?? []
  // D031 v2: expand workflowSteps so stepStates covers all visible strip cells:
  // prepend the R/B workflow root and append AC. AC (final approval) is an explicit step for
  // EVERY workflow, including memo-ending ones (M042 §3.1 — PM rejected the old
  // "memo-end auto-completes, no AC" coupling; the BE no longer silent-finalizes
  // on memo creation, so the AC cell must always exist to host the head=AC state).
  let workflowSteps: string[] = rawSteps
  if (rawSteps.length > 0) {
    const rootType = exposedValue<string | null>(h?.workflowRootType) ?? 'R'
    workflowSteps = [rootType, ...rawSteps, 'AC']
  }
  // BE head index is the position within rawSteps; the expanded array prepends
  // R, so shift by 1. null → buildStepStates falls back to type lookup.
  const rawHeadIndex = exposedValue<number | null>(h?.workflowHeadIndex)
  const headIndex = (rawHeadIndex != null && rawHeadIndex >= 0) ? rawHeadIndex + 1 : null
  return {
    tabTypeCode: getTabTypeCode(tabId),
    tabReviewStatus: override?.reviewStatus ?? exposedValue<string | null>(h?.docReviewStatus),
    workflowSteps,
    headType: override?.headType ?? exposedValue<string | null>(h?.workflowHeadType),
    headIndex,
    headStatus: exposedValue<string | null>(h?.headStatus),
    headDocId: exposedValue<string | null>(h?.headDocId),
    headDocReviewStatus: exposedValue<string | null>(h?.headDocReviewStatus),
    nextStepExists: exposedValue<boolean>(h?.nextStepExists) === true,
    qStatus: qStatuses[tabId] ?? null,
  }
}

function getWorkflowViewState(tabId: string): WorkflowViewState {
  // D030 §2: ActionBar must always render. Removed docLoaded/typeCode early-return guards
  // (T841). resolveWorkflowViewState handles every input state per D030 matrix —
  // when tabTypeCode is null/falsy it returns mode='review' (non-null) as the loading placeholder.
  return resolveWorkflowViewState(getWorkflowViewInput(tabId))
}

function canOpenNextAction(tabId: string): boolean {
  return getWorkflowViewState(tabId).canNextAction
}

function getActionBarMode(tabId: string) {
  const tab = tabs.value.find((t) => t.id === tabId)
  if (tab && isFileTab(tab)) return null
  // DC (group discard) is a terminal action record, not a review target: it must show
  // NO action bar (no approve/reject) — review r2 #4.
  if (getTabTypeCode(tabId) === 'DC') return null
  // TR0079.0003 (rework): a discarded group is terminal — none of its documents are
  // actionable review/workflow targets, so the action bar must collapse for ALL of
  // them, not just the DC carrier. Without this, approve/reject/workflow actions stayed
  // available on a disposed group's docs ("the action bar still allowed actions on
  // documents of a disposed group"). The flag refreshes via the SSE group_disposed → silent doc refetch,
  // so the bar disappears immediately rather than after F5.
  if (exposedValue(docHeaderRefs[tabId]?.groupDisposed) === true) return null
  return getWorkflowViewState(tabId).mode
}

// 0441 TR0005 rev9 (rejection: the removed hint sentence, and "테스트 실행중 아닌데 왜 버튼
// 비활성화 되냐?"): the action bar's next-step slot for a server-assembled head (TSR) used to
// be a permanently disabled button next to a paragraph telling the user, in words, to go open
// the TS document and run the test. The paragraph is gone; this is the same instruction turned
// into the button itself ("문서로 이동", the alternative the 6th rejection named). The group tree
// the sidebar already loads is the source for the TS document -- no new endpoint. Nothing here
// creates, copies or invokes anything, so the manual-TSR ban (§2-§4) is untouched.
async function onOpenTestScenarioDoc(payload: { docId: string; projectId: string; groupId: string }) {
  const pid = payload.projectId
  const gid = payload.groupId
  if (!pid || !gid) {
    showToast(t('main.main_panel.error_info_unavailable'), 'warning')
    return
  }
  let target: { id: string; title?: string; label: string; type_code: string | null } | null = null
  try {
    const nodes = await explorerStore.fetchGroupTree(pid)
    const candidates = nodes.filter(
      n => n.node_type === 'document'
        && n.parent_id === gid
        && String(n.type_code ?? '').toUpperCase() === 'TS',
    )
    // Several TS documents can sit in one group (a re-scenario after a rejected run). The run
    // that assembles the pending TSR head is started from the newest one.
    candidates.sort((a, b) => String(a.number ?? a.id).localeCompare(String(b.number ?? b.id)))
    target = candidates[candidates.length - 1] ?? null
  } catch {
    target = null
  }
  if (!target) {
    showToast(t('main.main_panel.error_info_unavailable'), 'warning')
    return
  }
  const title = target.title || target.label || ''
  tabsStore.openTab({
    id: target.id,
    title: title ? `${target.id} — ${title}` : target.id,
    path: '',
    type: 'md',
    typeCode: target.type_code ?? undefined,
  })
}

function onOpenHeadDocClick(payload: { docId: string; title: string; typeCode: string | null }) {
  tabsStore.openTab({
    id: payload.docId,
    title: payload.title
      ? `${payload.docId} — ${payload.title}`
      : payload.docId,
    path: '',
    type: 'md',
    typeCode: payload.typeCode ?? undefined,
  })
}

function onReviewApproved(tabId: string, nextStatus?: string | null) {
  // Gap D (NR0003 §2/§6 item 2): refresh through applyReviewTransition — optimistic flip
  // to the server-confirmed status + silent retrying backfill — instead of the bare
  // non-silent fetchDoc that blanks the header and, on a slow/failed idle-window GET, left
  // it stuck with only the toast showing. Shared by approve and revision-complete.
  docHeaderRefs[tabId]?.applyReviewTransition?.(nextStatus)
}

function onReviewRejected(tabId: string) {
  // AC (final approval) reject is not an ordinary rework reject: it reopens the
  // workflow at an earlier step via the time-machine dialog — M042 §3.3.
  if (getTabTypeCode(tabId) === 'AC') {
    void openTimeMachine(tabId)
    return
  }
  const tab = tabs.value.find(t => t.id === tabId)
  rejectDialogDocId.value = tabId
  rejectDialogDocName.value = tab?.title ?? tabId
  rejectDialogTabId.value = tabId
  rejectDialogEditMode.value = false
  rejectDialogVisible.value = true
}

// 0419 T0006 (NR0003 follow-up T recommendations 2/3): sidebar [Edit] entry point — open the SAME
// dialog, but in edit mode, to correct the current rejection's wording instead of
// filing a new one. onRejectDialogSaveReason branches on rejectDialogEditMode to
// call the PATCH-correction endpoint instead of the reject-transition POST.
function onEditRejectionRequested(tabId: string) {
  const tab = tabs.value.find(t => t.id === tabId)
  rejectDialogDocId.value = tabId
  rejectDialogDocName.value = tab?.title ?? tabId
  rejectDialogTabId.value = tabId
  rejectDialogEditMode.value = true
  rejectDialogVisible.value = true
}

// Open the time-machine dialog for an AC tab: list the group's realised workflow
// steps (excluding R/Q/AC and auto-complete memos) so the reviewer can pick one
// to roll back to.
async function openTimeMachine(acTabId: string) {
  const h = docHeaderRefs[acTabId]
  const projectId = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  if (!projectId || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  timeMachineDocId.value = acTabId
  timeMachineSteps.value = []
  timeMachinePreselectDocId.value = null  // AC reject opens the picker with no pre-selection
  timeMachineLoading.value = true
  timeMachineVisible.value = true
  resetTimeMachineCancelState()
  void loadCommitPreview(acTabId)
  try {
    const res = await getRequest<any[]>(`/api/v1/documents`, {
      project_id: projectId,
      group_id: groupId,
      limit: 200,
    })
    const docs = Array.isArray(res.data) ? res.data : []
    timeMachineSteps.value = docs
      // M (memo) is an auto-complete note, not a re-doable gate — exclude it as a
      // rollback target so the reviewer can only roll back to reviewable steps.
      .filter((d: any) => !['R', 'B', 'Q', 'AC', 'M'].includes(d.type_code))
      .sort((a: any, b: any) => (a.seq ?? 0) - (b.seq ?? 0))
      .map((d: any) => ({
        docId: d.doc_id,
        seq: d.seq ?? 0,
        typeCode: d.type_code ?? null,
        title: d.title ?? null,
      }))
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(detail, 'danger')
    timeMachineVisible.value = false
  } finally {
    timeMachineLoading.value = false
  }
}

// 0018 R0001 — workflow-strip time-machine. A completed step cell was clicked; roll the
// workflow back to that step. Reuses the AC dialog + reopen endpoint. The clicked slot is
// resolved by slot identity (index, then type-occurrence fallback) so repeated types
// (e.g. a design series appearing twice) roll back the correct cell — NR0003 §3/§5.2.
async function onWorkflowStepTimeMachine(tabId: string, payload: { index: number; code: string }) {
  const h = docHeaderRefs[tabId]
  // The sequence lives on the root workflow doc (R/B). Child docs expose it via parentRDocId;
  // an R/B tab is its own root.
  const rootDocId = exposedValue<string>(h?.parentRDocId) ?? tabId
  if (!rootDocId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  let items: SequenceSlot[] = []
  try {
    const res = await getRequest<any>(`/api/v1/workflow/${encodeURIComponent(rootDocId)}/sequence`)
    items = Array.isArray(res.data?.sequence) ? res.data.sequence : []
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(detail, 'danger')
    return
  }
  const clicked = resolveClickedSlot(getWorkflowViewState(tabId).stepStates, items, payload)
  if (!isRollbackTarget(clicked)) {
    // Structural/auto-complete step, or a slot with no realised reviewable document.
    showToast(t('main.time_machine.not_rollbackable'), 'danger')
    return
  }
  // Picker lists every realised, reviewable slot (sorted by the rolled-back document's seq);
  // pre-select the clicked one so [Roll Back] targets it (still changeable in the list).
  timeMachineSteps.value = items
    .filter((it) => isRollbackTarget(it))
    .sort((a, b) => (a.result_seq ?? 0) - (b.result_seq ?? 0))
    .map((it) => ({
      docId: it.result_doc_id as string,
      seq: it.result_seq ?? 0,
      typeCode: (it.type as string) ?? null,
      title: (it.label as string) ?? null,
    }))
  timeMachineDocId.value = rootDocId  // reopen resolves project/group from this (non-AC → tab kept)
  timeMachinePreselectDocId.value = clicked!.result_doc_id ?? null
  timeMachineLoading.value = false
  timeMachineVisible.value = true
  resetTimeMachineCancelState()
  void loadCommitPreview(rootDocId)
}

// 0332 D0005 §6.3 — the dialog opens with the group's commit state so each step line can
// say what a rewind would revert. Fetched from the return-point call the dialog already
// makes (P0006 §2 put the preview there rather than adding an endpoint). A failure leaves
// it null on purpose: the lines then read "확인할 수 없음" and the confirm button stays
// live, because git state has never been allowed to block a rewind.
async function loadCommitPreview(docId: string) {
  timeMachineCommitPreview.value = null
  if (!docId) return
  try {
    const res = await getRequest<any>(
      `/api/v1/documents/workflow/${encodeURIComponent(docId)}/return-point`,
    )
    timeMachineCommitPreview.value = res.data?.tr_commit_preview ?? null
  } catch {
    timeMachineCommitPreview.value = null
  }
}

function resetTimeMachineCancelState() {
  timeMachineCancelResult.value = null
  timeMachinePendingStep.value = null
  timeMachineCancelDocId.value = ''
  timeMachineRetrying.value = false
}

// Reopen the workflow at the chosen step: every step doc with seq >= target_seq
// is reset to pending_review (docs preserved), the AC doc is deleted, and R
// returns to wf_in_progress. Jump to the rolled-back step so the user can revise.
// 0332 D0005 §6.4 — does this cancel result need the result screen, or is a toast enough?
// A rewind that canceled everything (or had nothing to cancel) is finished business; a
// blocked, skipped or stopped one has a next action attached and must stay on screen.
function cancelNeedsResultScreen(cancel: any): boolean {
  if (!cancel) return false
  return !!cancel.blocked_reason
    || !!cancel.stopped_reason
    || (Array.isArray(cancel.skipped) && cancel.skipped.length > 0)
}

// The close-out the rewind always ends with: drop the stale AC tab, re-read the open
// documents and land the user on the step they rewound to.
function finishTimeMachine(acDocId: string, step: TimeMachineStep) {
  // Only the ephemeral AC doc is deleted by reopen — close its (now stale) tab. A
  // strip-triggered reopen originates from the root R (or a child), which reopen keeps,
  // so its tab must NOT be closed (0018 R0001).
  if (getTabTypeCode(acDocId) === 'AC') tabsStore.closeTab(acDocId)
  for (const tid of Object.keys(docHeaderRefs)) docHeaderRefs[tid]?.fetchDoc?.(tid)
  tabsStore.openTab({
    id: step.docId,
    title: step.title ? `${step.docId} — ${step.title}` : step.docId,
    path: '',
    type: 'md',
    typeCode: step.typeCode ?? undefined,
  })
}

async function onTimeMachineConfirm(payload: TimeMachineStep) {
  const acDocId = timeMachineDocId.value
  if (!acDocId) return
  try {
    const res = await postRequest<any>(`/api/v1/documents/workflow/reopen`, {
      doc_id: acDocId,
      target_seq: payload.seq,
    })
    // 0332 — the rewind is committed no matter what the source cancel did, so the
    // commit markers and the Git status panel are stale from here on either way.
    refreshAfterCancel()
    const cancel = res.data?.tr_commit_cancel ?? null
    if (cancelNeedsResultScreen(cancel)) {
      // Stay open and become the result screen. The step the user rewound to opens when
      // they press [닫기] — closing is what "done here" means (D0005 §6.4).
      timeMachineCancelResult.value = cancel
      timeMachineCancelDocId.value = payload.docId
      timeMachinePendingStep.value = payload
      return
    }
    timeMachineVisible.value = false
    finishTimeMachine(acDocId, payload)
    showToast(reopenToast(cancel), 'success')
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(detail, 'danger')
  }
}

// The success toast carries the cancel count — "되감았다"만으로는 소스가 어떻게 됐는지
// 말하지 않는다(D0005 §6.4). A server that omitted the key (older build, preview failure)
// falls back to the plain sentence this dialog has always shown.
function reopenToast(cancel: any): string {
  if (!cancel) return t('main.time_machine.toast_reopened')
  const n = Array.isArray(cancel.canceled) ? cancel.canceled.length : 0
  return n > 0
    ? t('main.time_machine.toast_reopened_canceled', { n })
    : t('main.time_machine.toast_reopened_nothing')
}

// 0332 D0005 §6.4 — "어느 경우든 갱신 신호로 워크플로 칸의 표식과 Git 상태 패널의 커밋
// 목록이 함께 갱신된다." 둘은 서로 다른 통로다: 패널은 창 이벤트로, 칸의 표식은 시퀀스
// 재조회로 갱신된다. 앞으로 복원(doWorkflowStepReturn)이 이미 쓰는 것과 같은 재조회다.
function refreshAfterCancel() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('fg:git_status_refresh', {
      detail: { project: projectStore.currentProjectId ?? null },
    }))
  }
  if (activeTabId.value) void refreshReturnPoint(activeTabId.value)
}

// [다시 시도] — re-runs ONLY the cancel (P0006 §4). The rewind is already committed, so
// nothing here touches a document; the result screen redraws in place.
async function onTimeMachineRetryCancel() {
  const docId = timeMachineCancelDocId.value
  if (!docId || timeMachineRetrying.value) return
  timeMachineRetrying.value = true
  try {
    const res = await postRequest<any>(
      `/api/v1/documents/workflow/${encodeURIComponent(docId)}/return-point/cancel-commits`,
      {},
    )
    const cancel = res.data?.tr_commit_cancel ?? null
    refreshAfterCancel()
    if (cancelNeedsResultScreen(cancel)) {
      timeMachineCancelResult.value = cancel
      return
    }
    // The retry succeeded: this dialog has nothing left to say.
    const step = timeMachinePendingStep.value
    timeMachineVisible.value = false
    timeMachineCancelResult.value = null
    if (step) {
      timeMachinePendingStep.value = null
      finishTimeMachine(timeMachineDocId.value, step)
    }
    showToast(reopenToast(cancel), 'success')
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(detail, 'danger')
  } finally {
    timeMachineRetrying.value = false
  }
}

// [Git 상태 패널 열기] — the merged-group case's only real next action. Reuses the event
// the approval flow already opens that panel with; no second opener (0405 T0011).
// 0332 TR0014 검토 — 이 창(scoped .modal-bg, z-index 1200)을 먼저 닫아야 한다. 관제소
// 모달(GitActionMenu.vue, 공용 .modal-bg, z-index 1000)은 같은 body 직속 fixed 레이어를
// 쓰므로, 이 창이 떠 있는 채로 열리면 그 아래 깔려 클릭도 못 받는다. 닫기는 [닫기]와
// 같은 정리(onTimeMachineVisibleChange)를 거쳐야 되감긴 단계로의 이동도 그대로 산다.
function onTimeMachineOpenGitPanel() {
  onTimeMachineVisibleChange(false)
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('fg:git_status_open', {
    detail: { project: projectStore.currentProjectId ?? null },
  }))
}

function onTimeMachineVisibleChange(v: boolean) {
  timeMachineVisible.value = v
  if (v) return
  timeMachineCancelResult.value = null
  const step = timeMachinePendingStep.value
  if (step) {
    timeMachinePendingStep.value = null
    finishTimeMachine(timeMachineDocId.value, step)
  }
}

// 0142 R0001 — reverse time-machine. A return-target cell (a rewound step ahead of the head)
// was clicked in the workflow strip; roll the workflow FORWARD to it. This mirrors
// onWorkflowStepTimeMachine: resolve the clicked slot by identity (index, then type-occurrence
// fallback) so repeated types return to the correct cell, restore every untouched step up to
// it, then open that step so the user actually lands there ("go there").
// Short "0011-TR" code from a full doc_id, for user-facing messages that name the step.
function shortDocCode(docId: string): string {
  return docId.split('.').pop() ?? docId
}

// 0142 rework — complaint #1: the backward time-machine confirms via a dialog before rolling
// back, but the forward restore fired the moment a cell was clicked ("너무 확확 돌아간다").
// This now only RESOLVES the click and opens a confirm dialog; the actual restore runs in
// doWorkflowStepReturn once the user confirms — symmetric with the backward direction.
async function onWorkflowStepReturn(tabId: string, payload: { index: number; code: string }) {
  if (returnPointRestoring.value) return
  const docId = returnPointDocId(tabId)
  if (!docId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  // Reuse the cached sequence populated alongside the return point; fall back to a fresh fetch.
  let items: SequenceSlot[] = returnSequences[docId] ?? []
  if (items.length === 0) {
    try {
      const res = await getRequest<any>(`/api/v1/workflow/${encodeURIComponent(docId)}/sequence`)
      items = Array.isArray(res.data?.sequence) ? res.data.sequence : []
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? String(e), 'danger')
      return
    }
  }
  const clicked = resolveClickedSlot(getWorkflowViewState(tabId).stepStates, items, payload)
  if (!isRollbackTarget(clicked) || clicked!.result_seq == null) {
    showToast(t('main.time_machine.not_rollbackable'), 'danger')
    return
  }
  pendingReturn.value = {
    tabId,
    docId,
    destinationSeq: clicked!.result_seq as number,
    destinationDocId: clicked!.result_doc_id as string,
    destinationLabel: (clicked!.label as string) ?? null,
    destinationType: (clicked!.type as string) ?? null,
  }
  returnConfirmVisible.value = true
}

// 0332 T0018 K11 — what the forward restore did to the SOURCE, as one sentence appended
// to the document sentence. Never replaces it: the documents came forward whatever git
// said (D0005 K8), and a server that sends no `tr_commit_restore` (older build, restore
// that touched no step) falls back to exactly the message this dialog always showed.
function restoreSourceSentence(restore: any): string {
  if (!restore) return ''
  if (restore.blocked_reason) {
    return t('main.time_machine.restore_source_blocked', {
      reason: t(`main.time_machine.reason_${restore.blocked_reason}`),
    })
  }
  // TR0019 — same split as the rewind result screen: a kept conflict has a button behind it.
  if (restore.stopped_reason === 'conflict') {
    return restore.conflict_session
      ? t('main.time_machine.restore_source_conflict_parked')
      : t('main.time_machine.restore_source_conflict')
  }
  const n = Array.isArray(restore.reapplied) ? restore.reapplied.length : 0
  if (n > 0) return t('main.time_machine.restore_source_reapplied', { n })
  // Nothing came back and nothing refused: there was nothing canceled to begin with.
  // Saying so out loud is the difference between "정상" and "왜 아무 말이 없지".
  return t('main.time_machine.restore_source_none')
}

// Confirmed forward restore: re-approve untouched steps up to the chosen one, stop at the
// first changed document, then land the user on the step they returned to.
async function doWorkflowStepReturn() {
  const pending = pendingReturn.value
  pendingReturn.value = null
  if (!pending || returnPointRestoring.value) return
  const { tabId, docId, destinationSeq, destinationDocId, destinationLabel, destinationType } = pending

  returnPointRestoring.value = true
  try {
    const res = await postRequest<any>(`/api/v1/documents/workflow/restore`, {
      doc_id: docId,
      destination_seq: destinationSeq,
    })
    const data = res.data ?? {}
    for (const tid of Object.keys(docHeaderRefs)) docHeaderRefs[tid]?.fetchDoc?.(tid)
    await refreshReturnPoint(tabId)
    // 0332 T0018 K11 — the restore may have put source commits back, so the workflow
    // markers and the Git status panel are stale from here on. Same refresh the rewind
    // side runs, for the same reason: without it the screen keeps showing the canceled
    // state and the whole thing reads as "아무것도 안 변했다".
    refreshAfterCancel()
    const restoredCount = Array.isArray(data.restored) ? data.restored.length : 0
    const source = restoreSourceSentence(data.tr_commit_restore ?? null)
    // `string` here was a pre-existing type error: showToast takes the ToastType union,
    // and the four call sites below already pass members of it (0452 T0005 — npm run
    // typecheck is one of the verifications this change has to report green).
    const say = (msg: string, kind: ToastType) => showToast(source ? `${msg} ${source}` : msg, kind)
    // Messages name the document reached, not a raw step count (complaint #3).
    if (data.stopped_doc_id) {
      // Hit an edited step before reaching the clicked target — stop there, keep the return point.
      say(t('main.time_machine.restore_stopped', { doc: shortDocCode(data.stopped_doc_id) }), 'warning')
    } else if (data.reached_front) {
      say(t('main.time_machine.restore_done_full', { doc: shortDocCode(destinationDocId) }), 'success')
    } else if (restoredCount > 0) {
      say(t('main.time_machine.restore_done_partial', { doc: shortDocCode(destinationDocId) }), 'success')
    } else {
      say(t('main.time_machine.restore_noop'), 'warning')
    }
    // "그쪽으로 갈수 있게" — land the user on the step they returned to.
    const landing = data.stopped_doc_id ?? destinationDocId
    if (landing) {
      tabsStore.openTab({
        id: landing,
        title: destinationLabel ? `${landing} — ${destinationLabel}` : landing,
        path: '',
        type: 'md',
        typeCode: destinationType ?? undefined,
      })
    }
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(detail, 'danger')
  } finally {
    returnPointRestoring.value = false
  }
}

async function onOrphanRecovered(tabId: string) {
  await docHeaderRefs[tabId]?.fetchDoc?.(tabId, { silent: true })
}

async function onRejectDialogSaveReason(reason: string) {
  const tabId = rejectDialogTabId.value
  if (!tabId) return
  try {
    // 0419 T0006: edit mode corrects the latest rejection's wording (PATCH) instead
    // of filing a new rejection (POST review_transitions/reject).
    const res = rejectDialogEditMode.value
      ? await patchRequest<any>(`/api/v1/documents/${encodeURIComponent(tabId)}/rejection_reason`, { reason })
      : await postRequest<any>(`/api/v1/documents/review_transitions/reject`, { doc_id: tabId, comment: reason })
    // Gap D (NR0003 §2/§6 item 2): optimistic flip to the confirmed status + silent
    // retrying backfill, not the bare non-silent fetchDoc that could blank the header
    // on a slow idle-window GET and leave only this toast showing.
    const updated = (res.data as any)?.document ?? (res.data as any)?.data ?? res.data
    await docHeaderRefs[tabId]?.applyReviewTransition?.(updated?.doc_review_status ?? 'rejected')
    if (!rejectDialogEditMode.value) showToast(t('main.main_panel.toast_rejected'), 'danger')
    rejectDialogRef.value?.notifySaved?.()
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(t('main.main_panel.toast_reject_failed', { detail }), 'danger')
    rejectDialogRef.value?.notifySaveFailed?.()
    throw e
  }
}

async function onRejectDialogCopyMention(reason: string) {
  const docName = rejectDialogDocName.value
  const text = t('main.main_panel.reject_mention_template', { docName, reason })
  // Text is ready synchronously here (no token round-trip), but still report honestly so a
  // focus/permission failure warns instead of falsely claiming success (B0001).
  const ok = await doClipboardCopy(text)
  if (ok) {
    showToast(t('main.main_panel.toast_reject_mention_copied'), 'success')
    void recordMentionCopy(rejectDialogTabId.value, 'reject')
  } else notifyCopyFailure()
}

// Group 0223: in-app invoke from the reject dialog. Maps to the rework scope — the
// server prepends the rejection context; the live (possibly unsaved) reason rides
// along as reject_reason so the prompt matches what the copy button would embed.
function onRejectDialogInvokeAi(reason: string) {
  const tabId = rejectDialogTabId.value
  if (!tabId) return
  const h = docHeaderRefs[tabId]
  const project = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  openAiInvokeDialog(project, groupId, tabId, 'rework', undefined, { rejectReason: reason || null })
}

function onRejectDialogClosed() {
  // Dialog closed (saved or cancelled): a silent backfill keeps the header fresh without
  // the non-silent blank-then-fail risk we just removed from the reject path (gap D).
  const tabId = rejectDialogTabId.value
  if (tabId) docHeaderRefs[tabId]?.applyReviewTransition?.()
}

type ReviewActionPayload = {
  docId: string
  projectId: string
  groupId: string
  docRef: string
}

async function onWorkflowDecisionCopyMention(payload: ReviewActionPayload) {
  let token: IssuedToken | null = null
  await copyMentionDeferred(
    async () => {
      token = await requestWorkflowDecision(payload.docId)
      if (!token) throw new ClipboardAbort()
      return composeMention(token)
    },
    {
      tabId: payload.docId,
      kind: 'workflow_decision',
      successToast: t('main.main_panel.toast_mention_copied'),
      aborted: () => token == null,
    },
  )
}

async function onWorkflowDecisionInvokeCommand(payload: ReviewActionPayload) {
  const token = await requestWorkflowDecision(payload.docId)
  if (!token) return
  pendingEnvOverrides.value = {
    FLOWGATE_TOKEN: token.raw_token,
    FLOWGATE_SCRATCH: token.scratch_dir,
  }
  commandSelectorVisible.value = true
}


async function onReviewReworkCopyMention(payload: ReviewActionPayload) {
  const project = payload.projectId || projectStore.currentProjectId
  const groupId = payload.groupId
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId)
  let token: IssuedToken | null = null
  await copyMentionDeferred(
    async () => {
      token = await issueToken({
        project,
        ...(gParts?.module != null ? { module: gParts.module } : {}),
        group: gParts?.groupCode ?? groupId,
        doc_ref: payload.docRef,
        action_scope: 'edit',
      })
      if (!token) throw new ClipboardAbort()
      const h = docHeaderRefs[payload.docId]
      const history: RejectionHistoryItem[] = exposedValue<RejectionHistoryItem[]>(h?.rejectionHistory) ?? []
      const lastReason = exposedValue<string>(h?.rejectionReason) ?? null
      const rejectionContext: RejectionContext | undefined =
        history.length > 0 || lastReason
          ? { history, last: lastReason }
          : undefined
      return composeMention(token, undefined, rejectionContext)
    },
    {
      tabId: payload.docId,
      kind: 'rework',
      successToast: t('main.main_panel.toast_mention_copied'),
      aborted: () => token == null,
    },
  )
}

// Group 0223: in-app invoke of the rework mention. The rejection context the copy
// path assembles client-side is rebuilt server-side from the document's rejection
// history (invoke_mention_service.build_rejection_section).
function onReviewReworkInvokeAi(payload: ReviewActionPayload) {
  const project = payload.projectId || projectStore.currentProjectId
  if (!project || !payload.groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  openAiInvokeDialog(project, payload.groupId, payload.docRef, 'rework')
}

// Group 0223: invoke twin of onReviewOpenMentionDialog — same branch order as the
// copy path: VR correction → review request → design handoff (the dialog carries its
// own invoke button) → next-step handoff.
function onReviewInvokeAiEntry(payload: ReviewActionPayload) {
  const project = payload.projectId || projectStore.currentProjectId
  const groupId = payload.groupId
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  const headType = exposedValue<string | null>(docHeaderRefs[payload.docId]?.workflowHeadType)
  if (headType === 'VR') {
    openAiInvokeDialog(project, groupId, payload.docId, 'vr_correction')
    return
  }
  const reviewStatus = exposedValue<string | null>(docHeaderRefs[payload.docId]?.docReviewStatus)
  if (reviewStatus == null || reviewStatus === 'pending_review' || reviewStatus === 'revised') {
    openAiInvokeDialog(project, groupId, payload.docId, 'review')
    return
  }
  const designTypes = getWorkflowDesignTypes(payload.docId)
  if (designTypes.length > 0) {
    designHandoffDocId.value = payload.docId
    designHandoffDocRef.value = payload.docRef
    designHandoffProjectId.value = project
    designHandoffGroupId.value = groupId
    designHandoffDefaultTypes.value = designTypes
    designHandoffNextStepLabel.value = getNextStepLabel(payload.docId)
    designHandoffVisible.value = true
    return
  }
  // ReviewActionBar hands us the viewed tab as docRef, which for a member doc (T/TR/…) is not
  // the sequence root the continuous-target picker needs (0242 NR0003 recommendation 2).
  openAiInvokeDialog(project, groupId, payload.docRef, 'new', {
    sequenceDocRef: nextActionDocRef(payload.docId),
  })
}

async function onReviewInvokeCommand(payload: ReviewActionPayload) {
  const project = payload.projectId || projectStore.currentProjectId
  const groupId = payload.groupId
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId)
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? groupId,
    doc_ref: payload.docRef,
    action_scope: 'edit',
  })
  if (!token) return
  pendingEnvOverrides.value = {
    FLOWGATE_TOKEN: token.raw_token,
    FLOWGATE_SCRATCH: token.scratch_dir,
  }
  commandSelectorVisible.value = true
}

async function onReviewOpenMentionDialog(payload: { docId: string; projectId: string; groupId: string; docRef: string }) {
  // VR stage: server assembles and copies a correction label including the preceding V report path
  const headType = exposedValue<string | null>(docHeaderRefs[payload.docId]?.workflowHeadType)
  if (headType === 'VR') {
    let fetchFailed = false
    let emptyPrompt = false
    const ok = await copyToClipboardDeferred(async () => {
      let text = ''
      try {
        const res = await getRequest<any>(`/api/v1/documents/prompt?doc_id=${encodeURIComponent(payload.docId)}`)
        text = res?.data?.prompt_text ?? ''
      } catch {
        fetchFailed = true
        throw new ClipboardAbort()
      }
      if (!text) {
        emptyPrompt = true
        throw new ClipboardAbort()
      }
      return text
    })
    if (fetchFailed) {
      showToast(t('main.main_panel.error_vr_mention_fetch_failed'), 'danger')
    } else if (emptyPrompt) {
      showToast(t('main.main_panel.error_vr_mention_failed'), 'warning')
    } else if (ok) {
      showToast(t('main.main_panel.toast_vr_mention_copied'), 'success')
      void recordMentionCopy(payload.docId, 'vr_correction')
    } else {
      notifyCopyFailure()
    }
    return
  }

  // Review request (pre-approval): the document itself needs review, NOT a next-step handoff.
  // Copy a review-request mention (read the doc → evaluate → submit a verdict via inbox
  // action:review). This must take priority over the design-handoff / advance branches
  // below, which are CREATE-NEXT handoffs valid only once the doc is approved.
  const reviewStatus = exposedValue<string | null>(docHeaderRefs[payload.docId]?.docReviewStatus)
  if (reviewStatus == null || reviewStatus === 'pending_review' || reviewStatus === 'revised') {
    let token: IssuedToken | null = null
    let noMention = false
    const ok = await copyToClipboardDeferred(async () => {
      token = await requestReview({ doc_id: payload.docId })
      if (!token) throw new ClipboardAbort()
      if (!token.mention) {
        noMention = true
        throw new ClipboardAbort()
      }
      return token.mention
    })
    if (token == null) return
    if (noMention || !ok) {
      notifyCopyFailure()
    } else {
      showToast(t('main.main_panel.toast_mention_copied'), 'success')
      void recordMentionCopy(payload.docId, 'review')
    }
    return
  }

  const designTypes = getWorkflowDesignTypes(payload.docId)
  if (designTypes.length > 0) {
    designHandoffDocId.value = payload.docId
    designHandoffDocRef.value = payload.docRef
    designHandoffProjectId.value = payload.projectId || projectStore.currentProjectId || ''
    designHandoffGroupId.value = payload.groupId
    designHandoffDefaultTypes.value = designTypes
    designHandoffNextStepLabel.value = getNextStepLabel(payload.docId)
    designHandoffVisible.value = true
    return
  }

  const project = payload.projectId || projectStore.currentProjectId
  const groupId = payload.groupId
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId)
  let token: IssuedToken | null = null
  await copyMentionDeferred(
    async () => {
      token = await issueToken({
        project,
        ...(gParts?.module != null ? { module: gParts.module } : {}),
        group: gParts?.groupCode ?? groupId,
        doc_ref: payload.docRef,
        action_scope: 'new',
      })
      if (!token) throw new ClipboardAbort()
      return composeMention(token)
    },
    {
      tabId: payload.docId,
      kind: 'next_step',
      successToast: t('main.main_panel.toast_mention_copied'),
      aborted: () => token == null,
    },
  )
}

function onDesignHandoffCopyMention(payload: { types: string[]; mode: string; copied: boolean }) {
  // B0001 / group 0221: only claim success when the write actually landed — the dialog
  // already opened the manual-copy fallback modal for the failure case.
  if (!payload.copied) return
  showToast(t('main.main_panel.toast_mention_copied'), 'success')
  void recordMentionCopy(designHandoffDocId.value, 'design_handoff')
}

// Group 0223: invoke twin of onDesignHandoffCopyMention — the picked types/mode ride to
// the server, which rebuilds the identical handoff text ahead of a tokened new mention.
function onDesignHandoffInvokeAi(payload: { types: string[]; mode: string; firstLabel: string }) {
  const project = designHandoffProjectId.value || projectStore.currentProjectId || ''
  const groupId = designHandoffGroupId.value
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  openAiInvokeDialog(project, groupId, designHandoffDocRef.value, 'design_handoff', undefined, {
    designTypes: payload.types,
    designMode: payload.mode,
    designFirstLabel: payload.firstLabel,
  })
}

function getNextStepLabelForNonR(tabId: string): string {
  return getNextStepLabel(tabId)
}

function onNonRNextActionClick(tabId: string) {
  if (!guardNextActionAvailable(tabId)) return
  const h = docHeaderRefs[tabId]
  const parentR = exposedValue<string | null>(h?.parentRDocId)
  if (!parentR) {
    showToast(t('main.main_panel.error_parent_r_not_found'), 'danger')
    return
  }
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  nextActionModalTabId.value = tabId
  nextActionModalDocRef.value = parentR
  nextActionModalCurrentType.value = getTabTypeCode(tabId) ?? ''
  nextActionModalInitialDocs.value = []
  nextActionModalStep.value = getNextStepLabelForNonR(tabId)
  nextActionModalTypeCode.value = getNextStepCode(tabId)
  nextActionModalProjectId.value = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  nextActionModalGroupId.value = groupId
  nextActionModalModuleName.value = nextActionModuleName(tabId, groupId)
  // 0405 P0004 [Open Proposal Window]: when the next type is a work plan, the shared proceed list
  // does not open. Its five entries don't fit a work plan, and none of its three branches has a
  // place to ask for scope.
  if (openWorkPlanProposalIfWp()) return
  nextActionModalVisible.value = true
}

async function onNextActionInvokeCommand(_selectedDocs?: string[]) {
  const tabId = nextActionModalTabId.value
  // 0441 T0004 item 2 rev11: the proceed dialog's [Invoke command] mints an
  // action_scope='new' token the same as [Copy mention] does, but has no TS-caret
  // counterpart (the split caret only ever offers copy-mention / invoke-ai for a TSR
  // head) — so unlike those two, this one is unconditional, matching create-empty.
  if (blockServerAssembledNextStep(nextActionModalTypeCode.value || getNextStepCode(tabId))) return
  const docRef = nextActionModalDocRef.value || tabId
  const project = nextActionModalProjectId.value || (exposedValue<string>(docHeaderRefs[tabId]?.docProjectId) ?? projectStore.currentProjectId)
  const groupId = nextActionModalGroupId.value || exposedValue<string>(docHeaderRefs[tabId]?.groupId)
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_workflow_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId as string)
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? (groupId as string),
    doc_ref: docRef,
    action_scope: 'new',
  })
  if (!token) return
  pendingEnvOverrides.value = {
    FLOWGATE_TOKEN: token.raw_token,
    FLOWGATE_SCRATCH: token.scratch_dir,
  }
  commandSelectorVisible.value = true
}

async function onNextActionCopyMention(selectedDocs?: string[]) {
  const tabId = nextActionModalTabId.value
  const docRef = nextActionModalDocRef.value || tabId
  const typeCode = nextActionModalTypeCode.value || getNextStepCode(tabId)
  // 0441 TR0005 rev12 (review finding on rev11): a TSR head reached from the TS tab is not
  // an instruction to hand-write the report — it hands off to the same run the [Run] button
  // starts, just copied as a mention instead of fired immediately. That hand-off already
  // exists server-side as the manned POST /documents/test-run-request route
  // (test_run_service.issue_test_run_request), which mints an action_scope='test_run' token
  // keyed to the TS document itself. The exemption below used to fall through to the shared
  // action_scope='new' + doc_ref=root-R path instead: issueToken maps that to a
  // non-continuous /workflow/advance call, and the server refuses ANY non-continuous advance
  // on a server-assembled head (workflow_decision_service.py:935) — continuous=true is only
  // ever set by the separate continuous-work dialog. So the exempted button always 409'd and
  // never produced a mention. Route it through the endpoint built for this instead.
  if (isServerAssembledType(typeCode) && (getTabTypeCode(tabId) ?? '').toUpperCase() === 'TS') {
    let requestFailed = false
    await copyMentionDeferred(
      async () => {
        try {
          const res = await postRequest<any>('/api/v1/documents/test-run-request', { doc_id: tabId })
          return String(res.data?.mention ?? '')
        } catch (e: unknown) {
          requestFailed = true
          showToast(testRunErrorMessage(e), 'danger')
          throw new ClipboardAbort()
        }
      },
      {
        tabId,
        kind: 'next_step',
        successToast: t('main.next_action_modal.copy_mention_toast'),
        aborted: () => requestFailed,
      },
    )
    return
  }
  // 0441 T0004 item 2: the shared mention-issuing delegate. onActionBarCopyNextMention
  // already refuses before seeding the refs; guarding the delegate too covers the
  // next-action list, which reaches this function without passing through the action bar.
  if (blockServerAssembledNextStep(typeCode, tabId)) return
  const project = nextActionModalProjectId.value || (exposedValue<string>(docHeaderRefs[tabId]?.docProjectId) ?? projectStore.currentProjectId)
  const groupId = nextActionModalGroupId.value || exposedValue<string>(docHeaderRefs[tabId]?.groupId)
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_workflow_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId as string)
  let token: IssuedToken | null = null
  await copyMentionDeferred(
    async () => {
      token = await issueToken({
        project,
        ...(gParts?.module != null ? { module: gParts.module } : {}),
        group: gParts?.groupCode ?? (groupId as string),
        doc_ref: docRef,
        action_scope: 'new',
        selected_docs: selectedDocs,
      })
      if (!token) throw new ClipboardAbort()
      return composeMention(token, selectedDocs)
    },
    {
      tabId,
      kind: 'next_step',
      successToast: t('main.next_action_modal.copy_mention_toast'),
      aborted: () => token == null,
    },
  )
}

// Copy a token's mention (optionally prepending chosen project message(s)). Mirrors the
// copy logic in onNextActionCopyMention; returns whether the clipboard write succeeded.
async function copyTokenMention(token: IssuedToken, selectedDocs?: string[], appendMessages?: string[]): Promise<boolean> {
  // Token is already in hand here (issued when the dialog opened), so the text is built
  // synchronously; an honest write reports real success/failure (B0001). composeMention
  // handles both the server-mention and the legacy null-mention fallback, plus appendMessages.
  return doClipboardCopy(composeMention(token, selectedDocs, undefined, appendMessages))
}

// [Copy mention (add message)] — R0001 group 0004 / L0007 §2.2.
// Fetch candidates first (gating). On error: toast, copy nothing. Empty: fall back to a
// plain mention copy + fallback toast. Otherwise: open the dialog to pick a message.
async function onNextActionCopyMentionWithMessage(selectedDocs?: string[]) {
  const tabId = nextActionModalTabId.value
  // 0441 T0004 item 2 rev11: [Copy mention (add message)] issues the same
  // action_scope='new' token as plain [Copy mention], but — like [Invoke command] —
  // has no TS-caret counterpart, so it is refused unconditionally rather than
  // exempting the TS tab. Guard before the message-candidate fetch, not just before
  // the token call, so a server-assembled head triggers no request at all.
  if (blockServerAssembledNextStep(nextActionModalTypeCode.value || getNextStepCode(tabId))) return
  const docRef = nextActionModalDocRef.value || tabId
  const project = nextActionModalProjectId.value || (exposedValue<string>(docHeaderRefs[tabId]?.docProjectId) ?? projectStore.currentProjectId)
  const groupId = nextActionModalGroupId.value || exposedValue<string>(docHeaderRefs[tabId]?.groupId)
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_workflow_info_unavailable'), 'danger')
    return
  }
  const docType = nextActionModalTypeCode.value || ''

  // 1. Fetch candidates (P0006 §4.5). Failure → error toast, no copy (L0007 §2.2 / §5).
  let candidates: MessageEntry[]
  try {
    const res = await getRequest<{ data: MessageEntry[] }>(
      `/api/v1/projects/${encodeURIComponent(project)}/messages`,
      { doc_type: docType },
    )
    candidates = buildCandidateList(res.data?.data ?? [], docType)
  } catch {
    showToast(t('main.next_action_modal.copy_mention_error_toast'), 'danger')
    return
  }

  // 2. Issue the token (needed by both remaining paths).
  const gParts = splitGroupId(groupId as string)
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? (groupId as string),
    doc_ref: docRef,
    action_scope: 'new',
    selected_docs: selectedDocs,
  })
  if (!token) return

  // 3. No candidates → plain mention copy + fallback toast (L0007 §2.2 fallback).
  if (candidates.length === 0) {
    const ok = await copyTokenMention(token, selectedDocs)
    if (ok) {
      showToast(t('main.next_action_modal.copy_mention_fallback_toast'), 'warning')
      void recordMentionCopy(tabId, 'next_step_message')
    } else notifyCopyFailure()
    return
  }

  // 4. Candidates present → open the selection dialog.
  let docTypes: { code: string; label: string }[] = []
  try {
    const res = await getRequest<{ data: { code: string; label: string; is_active?: number }[] }>(
      `/api/v1/projects/${encodeURIComponent(project)}/document-types`,
      { locale: locale.value },
    )
    docTypes = (res.data?.data ?? []).filter((d) => d.is_active).map((d) => ({ code: d.code, label: d.label }))
  } catch {
    docTypes = [] // dialog still works; dropdown falls back to [All] + the current type
  }
  mmDialogContext.value = 'next'
  mmDialogToken.value = token
  mmDialogSelectedDocs.value = selectedDocs
  mmDialogProjectId.value = project
  mmDialogDocType.value = docType
  mmDialogDocTypes.value = docTypes
  mmDialogCandidates.value = candidates
  mmDialogVisible.value = true
}

async function onMmDialogSelect(messages: string[]) {
  mmDialogVisible.value = false
  const token = mmDialogToken.value
  if (!token) return
  const ok = await copyTokenMention(token, mmDialogSelectedDocs.value, messages)
  if (ok) {
    if (mmDialogContext.value === 'continuous') {
      showToast(t('main.continuous_work.toast_started'), 'success')
      void recordMentionCopy(
        continuousTabId.value,
        continuousFromDecision.value ? 'workflow_decision' : 'next_step',
      )
    } else {
      showToast(t('main.next_action_modal.copy_mention_toast'), 'success')
      void recordMentionCopy(nextActionModalTabId.value, 'next_step_message')
    }
  } else notifyCopyFailure()
  mmDialogToken.value = null
  mmDialogContext.value = 'next'
}

function onMmDialogCancel() {
  mmDialogVisible.value = false
  mmDialogToken.value = null
  mmDialogContext.value = 'next'
}

// Group 0223: invoke twin of onMmDialogSelect — the server re-issues its own token
// and prepends the picked message(s) to the same next-step mention
// (invoke_mention_service.prepend_messages_section). The copy-path token issued when
// the dialog opened is simply left unused, exactly as on cancel.
function onMmDialogSelectInvoke(messages: string[]) {
  mmDialogVisible.value = false
  mmDialogToken.value = null
  const tabId = nextActionModalTabId.value
  const docRef = nextActionModalDocRef.value || tabId
  const project = mmDialogProjectId.value || projectStore.currentProjectId || ''
  const groupId = nextActionModalGroupId.value || (exposedValue<string>(docHeaderRefs[tabId]?.groupId) ?? '')
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_workflow_info_unavailable'), 'danger')
    return
  }
  openAiInvokeDialog(project, groupId, docRef, 'next_step_message', undefined, {
    selectedDocs: mmDialogSelectedDocs.value ?? null,
    messages,
  })
}

function onNextActionCreateEmpty(_selectedDocs?: string[]) {
  const tabId = nextActionModalTabId.value
  const docRef = nextActionModalDocRef.value || tabId
  const project = nextActionModalProjectId.value || (exposedValue<string>(docHeaderRefs[tabId]?.docProjectId) ?? projectStore.currentProjectId ?? '')
  const groupId = nextActionModalGroupId.value || (exposedValue<string>(docHeaderRefs[tabId]?.groupId) ?? '')
  const moduleName = nextActionModalModuleName.value || (exposedValue<string>(docHeaderRefs[tabId]?.docModule) ?? 'none')
  const docType = nextActionModalTypeCode.value || getNextStepCode(tabId)
  if (!project || !groupId || !docRef || !docType) {
    showToast(t('main.main_panel.error_next_step_unavailable'), 'danger')
    return
  }
  if (['AC', 'RJ', 'V', 'C'].includes(docType)) {
    showToast(t('main.main_panel.error_empty_doc_not_allowed', { docType }), 'warning')
    return
  }
  // 0441 T0004 item 2: no tab has a legitimate [Create empty doc] for a server-assembled
  // type — not even the TS tab, whose split caret deliberately omits it — so this one is
  // unconditional. Stops before /documents/next-empty is called at all; the server refuses
  // the same request with 422 if this ever gets bypassed.
  if (blockServerAssembledNextStep(docType)) return
  // 0395 T0026 rework: a work plan has no "empty document". Creating one from just a title
  // leaves a file with neither quantities nor a provider decided, and the work-plan screen
  // cannot open that file as a table (user-reported). The same [Create Empty Document] entry
  // point now hands off to the window that settles the plan all the way through.
  //
  // 0405 T0011: that window used to be a create dialog with only [+ Create]. Despite sharing
  // the name [Create Work Plan], unlike the window opened from the sequence cell it had no
  // [Document Creation] / [Copy Mention] / [Invoke AI]. The two entry points are now merged
  // into one window (the dedicated proposal window).
  if (docType === 'WP') {
    nextActionModalDocRef.value = docRef
    nextActionModalProjectId.value = project
    nextActionModalGroupId.value = groupId
    nextActionModalTypeCode.value = docType
    if (openWorkPlanProposalIfWp()) return
    workPlanCreateParentDocId.value = docRef
    workPlanCreateProjectId.value = project
    workPlanCreateGroupId.value = groupId
    workPlanCreateVisible.value = true
    return
  }
  nextEmptyDocProjectId.value = project
  nextEmptyDocGroupId.value = groupId
  nextEmptyDocPrevDocId.value = docRef
  nextEmptyDocType.value = docType
  nextEmptyDocModule.value = moduleName
  // 0454 T0006 §4 — full variant (module nodes are never pruned, so this lookup is
  // variant-insensitive; it is spelled out so no reader has to work that out).
  const cachedNodes = explorerStore.getCachedGroupTree(project, true) || []
  const moduleNode = cachedNodes.find(n => n.node_type === 'module' && n.label === moduleName)
  nextEmptyDocModuleTitle.value = moduleNode?.title ?? null
  nextEmptyDocModalVisible.value = true
}

function onNextEmptyDocCreated(payload: { docId: string; openAfter: boolean; projectId: string }) {
  showToast(t('main.main_panel.toast_empty_doc_created'), 'success')
  const header = docHeaderRefs[nextActionModalTabId.value]
  header?.fetchDoc?.(nextActionModalTabId.value)
  emit('related-doc-created', payload)
}

// 0084 TR0005 (A): the direct action-bar handlers (copy-mention / create-empty) must
// resolve the same workflow doc-ref the modal path (onNonRNextActionClick) uses. The
// workflow sequence is keyed by the root R, so for a non-R member doc (CH/N/T/…) the
// token's doc_ref / new doc's prev_doc_id must be the parent R — not the member doc.
// Seeding tabId there sent the member doc to the root-only sequence lookup, which 400'd
// (sequence_not_decided) and degraded the next-step mention to a CH-bound copy (B0001).
// R/B docs are their own sequence root (parent_r_doc_id is null), so they fall back to
// tabId — mirroring onProceedNextStep's R/B-vs-non-R split.
function nextActionDocRef(tabId: string): string {
  if (['R', 'B'].includes(getTabTypeCode(tabId) ?? '')) return tabId
  return exposedValue<string | null>(docHeaderRefs[tabId]?.parentRDocId) || tabId
}

// R0001 #1 (0048): action-bar split "create empty doc" — opens the empty-doc input
// dialog directly (the dialog was previously buried in the proceed modal, D0004 §3-2-A).
function onActionBarCreateEmpty(tabId: string) {
  if (!guardNextActionAvailable(tabId)) return
  // 0441 T0004 item 2: refuse here as well as inside onNextActionCreateEmpty below. This
  // entry point seeds the modal-scoped refs before delegating, so stopping only at the
  // delegate would leave those refs pointing at a document that must never be created.
  if (blockServerAssembledNextStep(getNextStepCode(tabId))) return
  const h = docHeaderRefs[tabId]
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  nextActionModalTabId.value = tabId
  nextActionModalDocRef.value = nextActionDocRef(tabId)
  nextActionModalCurrentType.value = getTabTypeCode(tabId) ?? 'R'
  nextActionModalTypeCode.value = getNextStepCode(tabId)
  nextActionModalProjectId.value = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  nextActionModalGroupId.value = groupId
  nextActionModalModuleName.value = nextActionModuleName(tabId, groupId)
  onNextActionCreateEmpty()
}

// R0001 ③-b (0053): copy the next-step mention directly from the action-bar dropdown,
// without opening NextActionModal. We seed the same modal-scoped refs that
// onNextActionCopyMention reads, then reuse it verbatim. selected_docs is left
// undefined so the backend auto-merges the "R + previous + 2-previous" predecessors
// (token_routes 2-predecessor merge). The 'next_step' mention-copy badge is recorded
// by onNextActionCopyMention as usual.
function onActionBarCopyNextMention(tabId: string) {
  if (!guardNextActionAvailable(tabId)) return
  // 0441 T0004 item 2: a next-step mention for a server-assembled head is an instruction to
  // write that document by hand, carried by an action_scope='new' token — NR0003 §2 named
  // this the second way the reported manual TSR could be produced. The TS tab keeps its own
  // caret items (they belong to the test run), so tabId is passed.
  if (blockServerAssembledNextStep(getNextStepCode(tabId), tabId)) return
  const h = docHeaderRefs[tabId]
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  nextActionModalTabId.value = tabId
  nextActionModalDocRef.value = nextActionDocRef(tabId)
  nextActionModalCurrentType.value = getTabTypeCode(tabId) ?? 'R'
  nextActionModalTypeCode.value = getNextStepCode(tabId)
  nextActionModalProjectId.value = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  nextActionModalGroupId.value = groupId
  nextActionModalModuleName.value = nextActionModuleName(tabId, groupId)
  void onNextActionCopyMention()
}


function testRunErrorMessage(e: unknown): string {
  const code = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
  switch (code) {
    case 'permission_denied':
      return t('main.test_run_strip.err_denied')
    case 'run_in_progress':
      return t('main.test_run_strip.err_in_progress')
    case 'doc_not_approved':
      return t('main.test_run_strip.err_not_approved')
    case 'group_disposed':
      return t('main.test_run_strip.err_disposed')
    case 'src_root_missing':
      return t('main.test_run_strip.err_src_missing')
    case 'no_test_cases':
      return t('main.test_run_strip.err_no_cases')
    default:
      return t('main.test_run_strip.err_failed')
  }
}

async function onActionBarRunTest(tabId: string) {
  if ((getTabTypeCode(tabId) ?? '').toUpperCase() !== 'TS') return
  try {
    await postRequest('/api/v1/documents/test-run', { doc_id: tabId })
    showToast(t('main.test_run_strip.run_started'), 'info')
    docHeaderRefs[tabId]?.fetchDoc?.(tabId)
  } catch (e: unknown) {
    showToast(testRunErrorMessage(e), 'error')
  }
}

// ── Work plan (flowgate.default.0395 D0007 §6.3 / T0021) ──────────────────────
// Entry point is the [Workflow Sequence] section (DocWorkflow), which supplies the
// sequence-owning R/B root — the strip is drawn on member documents too, and the
// viewed tab is not necessarily the document a work plan may attach to.
function onCreateWorkPlan(tabId: string, parentDocId: string) {
  const h = docHeaderRefs[tabId]
  const docRef = parentDocId || tabId
  const project = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  // 0405 P0004 [Open Proposal Window]: this event only fires when the workflow sequence's head
  // cell is a work plan (DocWorkflow.onStepClick's WP branch, fixed as the sole entry point by
  // 0395 T0036). Since this click is the document screen's [Proceed to Next Step], the window
  // that must open here is not the existing create dialog but the dedicated proposal window
  // that asks for the scope to hand off.
  nextActionModalTabId.value = tabId
  nextActionModalDocRef.value = docRef
  nextActionModalCurrentType.value = getTabTypeCode(tabId) ?? 'R'
  nextActionModalInitialDocs.value = []
  nextActionModalStep.value = getNextStepLabel(tabId)
  nextActionModalTypeCode.value = getNextStepCode(tabId) || 'WP'
  nextActionModalProjectId.value = project
  nextActionModalGroupId.value = groupId
  nextActionModalModuleName.value = nextActionModuleName(tabId, groupId)
  if (openWorkPlanProposalIfWp()) return
  // Only opens the existing create dialog when this fires while the head cell is not a work plan.
  workPlanCreateParentDocId.value = docRef
  workPlanCreateProjectId.value = project
  workPlanCreateGroupId.value = groupId
  workPlanCreateVisible.value = true
}

/**
 * 0405 P0004 — opens the dedicated proposal window only when the next type is WP. Every other
 * type returns false and falls straight through to the shared proceed list (no regression).
 */
function openWorkPlanProposalIfWp(): boolean {
  if (String(nextActionModalTypeCode.value || '').toUpperCase() !== 'WP') return false
  workPlanCreateParentDocId.value = nextActionModalDocRef.value
  workPlanCreateProjectId.value = nextActionModalProjectId.value
  workPlanCreateGroupId.value = nextActionModalGroupId.value
  workPlanProposalBusy.value = ''
  workPlanProposalNotice.value = ''

  workPlanProposalVisible.value = true
  return true
}

function workPlanProposalFailureText(failure: AdvanceFailure): string {
  if (failure.code === 'sequence_exhausted') return t('main.work_plan_proposal_dialog.notice_sequence_exhausted')
  if (failure.code === 'head_in_progress') return t('main.work_plan_proposal_dialog.notice_head_in_progress')
  return failure.message || t('main.work_plan_proposal_dialog.notice_issue_failed')
}

/**
 * [Copy Mention] — issues a token carrying the scope and puts that mention on the clipboard.
 * The window does not close before the request: it closes only after the copy succeeds, and
 * on a 409 stays open with just the reason line swapped in.
 */
async function onWorkPlanProposalCopyMention(scope: WorkPlanScope) {
  const docRef = workPlanCreateParentDocId.value
  if (!docRef || workPlanProposalBusy.value) return
  workPlanProposalNotice.value = ''
  workPlanProposalBusy.value = 'copy'
  const outcome: { token: IssuedToken | null; error: AdvanceFailure | null } = { token: null, error: null }
  try {
    await copyMentionDeferred(
      async () => {
        const res = await advanceWithWorkPlanScope({
          docId: docRef, workPlanScope: scope, refDocIds: [docRef],
        })
        outcome.token = res.token
        outcome.error = res.error
        if (!outcome.token) throw new ClipboardAbort()
        return composeMention(outcome.token, [docRef])
      },
      {
        tabId: nextActionModalTabId.value,
        kind: 'next_step',
        successToast: t('main.work_plan_proposal_dialog.copy_toast'),
        aborted: () => outcome.token == null,
      },
    )
  } finally {
    workPlanProposalBusy.value = ''
  }
  if (outcome.error) {
    workPlanProposalNotice.value = workPlanProposalFailureText(outcome.error)
    return
  }
  if (outcome.token) workPlanProposalVisible.value = false
}

/**
 * [Invoke AI] — a single run that bypasses the shared continuous-work window. The server folds
 * work_plan_proposal into advance_workflow, so the text the AI reads here comes from the same
 * function as the text [Copy Mention] puts on the clipboard.
 */
async function onWorkPlanProposalInvokeAi(payload: { scope: WorkPlanScope; providerId: string }) {
  const docRef = workPlanCreateParentDocId.value
  const project = workPlanCreateProjectId.value
  const groupId = workPlanCreateGroupId.value
  if (!docRef || !project || !groupId || workPlanProposalBusy.value) return
  workPlanProposalNotice.value = ''
  workPlanProposalBusy.value = 'ai'
  const gParts = splitGroupId(groupId)
  try {
    await postRequest('/api/v1/ai-invoke/start', {
      project,
      ...(gParts?.module != null ? { module: gParts.module } : {}),
      group: gParts?.groupCode ?? groupId,
      doc_ref: docRef,
      action_scope: 'work_plan_proposal',
      mode: 'single',
      provider_id: payload.providerId,
      selected_docs: [docRef],
      work_plan_scope: payload.scope,
    })
    workPlanProposalVisible.value = false
  } catch (e: any) {
    const data = e?.response?.data ?? {}
    // The server message for 409 run_in_progress is in English. The screen already has its own
    // wording for this reason, so it uses the reactive store state instead and leaves the reason
    // string empty — this keeps a stray English line from slipping into the Korean-language screen.
    if (data.code === 'run_in_progress') {

      workPlanProposalNotice.value = ''
      workPlanProposalVisible.value = false
      return
    }
    workPlanProposalNotice.value = data.message
      || data.detail
      || t('main.work_plan_proposal_dialog.notice_ai_failed')
  } finally {
    workPlanProposalBusy.value = ''
  }
}

function onWorkPlanCreated(payload: { docId: string; title: string }) {
  tabsStore.openTab({
    id: payload.docId,
    title: payload.title,
    path: '',
    type: 'md',
    typeCode: 'WP',
    projectId: workPlanCreateProjectId.value || undefined,
  })
}

// 0399 M0020 — the only path that puts a work plan into the workflow is the sequence cell's
// [Apply Work Plan]. The wiring that used to open a preview overlay from the action bar and
// apply it right there used to live here, and was removed along with the overlay.

// ── Continuous (unmanned) work (R0001 group 0086) ──────────────────────────────
// Entry from the 'workflow'/'next' action-bar dropdowns. Opens the sequence-pick dialog;
// the dialog reads /workflow/sequence by the ROOT R (nextActionDocRef) and handles the
// undecided/all-done cases itself, so this is NOT gated by guardNextActionAvailable.
function onActionBarContinuousWork(tabId: string) {
  // 0441 T0004 item 2: same rule as the next-action list's [Invoke AI] above — a non-TS tab
  // sitting on a server-assembled head has no manual entry, and the dialog it would open is
  // the AI-invoke token path. Started from the TS tab, the chain still reaches the TSR head
  // through the server's test-run hand-off.
  if (blockServerAssembledNextStep(getNextStepCode(tabId), tabId)) return
  const h = docHeaderRefs[tabId]
  continuousTabId.value = tabId
  continuousDocRef.value = nextActionDocRef(tabId)
  continuousProjectId.value = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  continuousGroupId.value = exposedValue<string>(h?.groupId) ?? ''
  // Populate the provider selector shown in the continuous-work dialog (RC3).
  void aiProviderStore.ensureLoaded(continuousProjectId.value)
  continuousDialogVisible.value = true
}

// Sequence-pick confirmed → carry the run parameters into the warning/consent gate.
function onContinuousDialogConfirm(payload: {
  targetSeq: number
  targetType: string
  targetLabel: string
  reviewMode: boolean
  instructionMode: ContinuationInstructionMode
  stepCount: number
  fromDecision: boolean
  providerOverrides: Record<number, string>
  defaultMessage: string
  messageOverrides: Record<number, string>
  autoApproveItemSeqs: number[]
  stepTimeoutSec: number
  restartMaxAttempts: number
  reviewCountOverrides: Record<number, number>
  reviewerOverrides: Record<number, string>
}) {
  continuousTargetSeq.value = payload.targetSeq
  continuousTargetType.value = payload.targetType
  continuousTargetLabel.value = payload.targetLabel
  continuousReviewMode.value = payload.reviewMode
  continuousInstructionMode.value = payload.instructionMode
  continuousProviderOverrides.value = payload.providerOverrides
  continuousDefaultMessage.value = payload.defaultMessage
  continuousMessageOverrides.value = payload.messageOverrides
  continuousReviewCountOverrides.value = payload.reviewCountOverrides
  continuousReviewerOverrides.value = payload.reviewerOverrides
  continuousAutoApproveItemSeqs.value = payload.autoApproveItemSeqs
  continuousStepTimeoutSec.value = payload.stepTimeoutSec
  continuousRestartMaxAttempts.value = payload.restartMaxAttempts
  continuousStepCount.value = payload.stepCount
  continuousFromDecision.value = payload.fromDecision
  continuousDialogVisible.value = false
  continuousWarnVisible.value = true
}

// Consent given → start the in-app provider immediately. Pre-decision runs use the
// workflow_decide token/mention and the run-to-end sentinel; decided sequences retain the
// concrete target chosen in ContinuousWorkDialog.
async function onContinuousWarnConfirm() {
  continuousWarnVisible.value = false
  const project = continuousProjectId.value
  const groupId = continuousGroupId.value
  const docRef = continuousDocRef.value
  const targetSeq = continuousTargetSeq.value
  if (!project || !groupId || !docRef || targetSeq == null) {
    showToast(t('main.main_panel.error_workflow_info_unavailable'), 'danger')
    return
  }
  openAiInvokeDialog(
    project,
    groupId,
    docRef,
    continuousFromDecision.value ? 'workflow_decide' : 'new',
    {
      mode: 'continuous',
      targetSeq,
      reviewMode: continuousReviewMode.value,
      instructionMode: continuousInstructionMode.value,
      autoApproveItemSeqs: continuousAutoApproveItemSeqs.value,
      providerOverrides: continuousProviderOverrides.value,
      defaultMessage: continuousDefaultMessage.value,
      messageOverrides: continuousMessageOverrides.value,
      reviewCountOverrides: continuousReviewCountOverrides.value,
      reviewerOverrides: continuousReviewerOverrides.value,
      stepTimeoutSec: continuousStepTimeoutSec.value,
      restartMaxAttempts: continuousRestartMaxAttempts.value,
      autoStart: true,
    },
  )
}

async function issueContinuousToken(): Promise<IssuedToken | null> {
  const project = continuousProjectId.value
  const groupId = continuousGroupId.value
  const docRef = continuousDocRef.value
  const targetSeq = continuousTargetSeq.value
  if (!project || !groupId || !docRef || targetSeq == null) {
    showToast(t('main.main_panel.error_workflow_info_unavailable'), 'danger')
    return null
  }
  if (continuousFromDecision.value) {
    return requestWorkflowDecision(docRef, {
      continuous: true,
      continuationReviewMode: continuousReviewMode.value,
      // 0406 T0022 task 2: this entry point sent no mode at all, so the server quietly folded
      // it to auto_approved — even choosing [Write Instruction Then Proceed] in the same
      // window, only the "워크플로 결정부터" chain had its N/T auto-approved by the server.
      continuationInstructionMode: continuousInstructionMode.value,
    })
  }
  const groupParts = splitGroupId(groupId)
  return issueToken({
    project,
    ...(groupParts?.module != null ? { module: groupParts.module } : {}),
    group: groupParts?.groupCode ?? groupId,
    doc_ref: docRef,
    action_scope: 'new',
    continuous: true,
    continuationTargetSeq: targetSeq,
    continuationReviewMode: continuousReviewMode.value,
    // 0406 T0022 task 2: the semi-automatic chain opened via [Copy Mention] must also start in the same mode.
    continuationInstructionMode: continuousInstructionMode.value,
  })
}

async function onContinuousWarnCopyMention() {
  continuousWarnVisible.value = false
  let token: IssuedToken | null = null
  await copyMentionDeferred(
    async () => {
      token = await issueContinuousToken()
      if (!token) throw new ClipboardAbort()
      return composeMention(token)
    },
    {
      tabId: continuousTabId.value,
      kind: continuousFromDecision.value ? 'workflow_decision' : 'next_step',
      successToast: t('main.continuous_work.toast_started'),
      aborted: () => token == null,
    },
  )
}

async function onContinuousWarnCopyMentionWithMessage() {
  continuousWarnVisible.value = false
  const project = continuousProjectId.value
  const docType = continuousTargetType.value
  if (!project) {
    showToast(t('main.main_panel.error_workflow_info_unavailable'), 'danger')
    return
  }

  let candidates: MessageEntry[]
  try {
    const res = await getRequest<{ data: MessageEntry[] }>(
      `/api/v1/projects/${encodeURIComponent(project)}/messages`,
      { doc_type: docType || '*' },
    )
    candidates = buildCandidateList(res.data?.data ?? [], docType || '*')
  } catch {
    showToast(t('main.next_action_modal.copy_mention_error_toast'), 'danger')
    return
  }

  const token = await issueContinuousToken()
  if (!token) return
  if (candidates.length === 0) {
    const ok = await copyTokenMention(token)
    if (ok) {
      showToast(t('main.continuous_work.toast_started'), 'success')
      void recordMentionCopy(
        continuousTabId.value,
        continuousFromDecision.value ? 'workflow_decision' : 'next_step',
      )
    } else notifyCopyFailure()
    return
  }

  let docTypes: { code: string; label: string }[] = []
  try {
    const res = await getRequest<{ data: { code: string; label: string; is_active?: number }[] }>(
      `/api/v1/projects/${encodeURIComponent(project)}/document-types`,
      { locale: locale.value },
    )
    docTypes = (res.data?.data ?? []).filter(d => d.is_active).map(d => ({ code: d.code, label: d.label }))
  } catch {
    docTypes = []
  }
  mmDialogContext.value = 'continuous'
  mmDialogToken.value = token
  mmDialogSelectedDocs.value = undefined
  mmDialogProjectId.value = project
  mmDialogDocType.value = docType || '*'
  mmDialogDocTypes.value = docTypes
  mmDialogCandidates.value = candidates
  mmDialogVisible.value = true
}
// R0001 #2 (0048): action-bar split "create approved doc" — confirm once, then create
// + approve in one server call (next-approved). Offered only for N/T next steps; TS is
// excluded (group 0121 R0001 — a test-scenario directive is token-issued/AI-authored,
// never auto-approved). approve permission is enforced by the server (403 → error toast).
function onActionBarCreateApproved(tabId: string) {
  if (!guardNextActionAvailable(tabId)) return
  const code = (getNextStepCode(tabId) || '').toUpperCase()
  if (!['N', 'T'].includes(code)) {
    showToast(t('main.main_panel.error_approved_doc_not_allowed', { docType: code }), 'warning')
    return
  }
  createApprovedTabId.value = tabId
  createApprovedConfirmVisible.value = true
}

async function doCreateApprovedDocument() {
  const tabId = createApprovedTabId.value
  if (!tabId || createApprovedSubmitting.value) return
  const h = docHeaderRefs[tabId]
  const project = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  const moduleName = nextActionModuleName(tabId, groupId)
  const typeCode = (getNextStepCode(tabId) || '').toUpperCase()
  if (!project || !groupId || !tabId || !typeCode) {
    showToast(t('main.main_panel.error_next_step_unavailable'), 'danger')
    return
  }
  createApprovedSubmitting.value = true
  try {
    const res = await postRequest<any>('/api/v1/documents/next-approved', {
      project_id: project,
      group_id: groupId,
      prev_doc_id: tabId,
      type_code: typeCode,
      module: moduleName || 'none',
    })
    const docId: string = (res.data as any)?.doc_id ?? ''
    showToast(t('main.review_action_bar.toast_create_approved_success'), 'success')
    h?.fetchDoc?.(tabId)
    // r2: open the newly created approved doc after creation. Previously openAfter
    // was false, so the doc was created server-side but the FE never navigated to it
    // → "created but nothing moved, looks like it wasn't created". Matches every other
    // creation flow (empty-doc / related-doc / requirement all default openAfter: true).
    emit('related-doc-created', { docId, openAfter: true, projectId: project })
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    const msg = Array.isArray(detail)
      ? detail.map((d: any) => d.msg ?? d).join(', ')
      : (detail ?? t('main.review_action_bar.toast_create_approved_failed'))
    showToast(msg, 'danger')
  } finally {
    createApprovedSubmitting.value = false
  }
}

// TR0044.0010 rev3: auto-create the next conversation document directly (no dialog).
// The reviewer wants a single [Create conversation doc] that "just creates" the CH doc. CH is an
// auto-complete type, so /next-empty creates it already-approved (L-AUTO). If the CH
// head doc already exists, navigate to it instead (parity with onProceedNextStep).
async function onActionBarCreateConversation(tabId: string) {
  const vs = getWorkflowViewState(tabId)
  if (vs.headDocId && vs.headDocId !== tabId) {
    openExistingHeadDocTab(tabId, vs.headDocId)
    return
  }
  if (!guardNextActionAvailable(tabId)) return
  const h = docHeaderRefs[tabId]
  const project = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  const moduleName = nextActionModuleName(tabId, groupId)
  const typeCode = (getNextStepCode(tabId) || '').toUpperCase()
  if (!project || !groupId || typeCode !== 'CH') {
    showToast(t('main.main_panel.error_next_step_unavailable'), 'danger')
    return
  }
  try {
    const res = await postRequest<any>('/api/v1/documents/next-empty', {
      project_id: project,
      group_id: groupId,
      prev_doc_id: tabId,
      type_code: 'CH',
      title: t('main.review_action_bar.conversation_default_title'),
      module: moduleName || 'none',
    })
    const docId: string = (res.data as any)?.doc_id ?? ''
    showToast(t('main.review_action_bar.toast_conversation_created'), 'success')
    h?.fetchDoc?.(tabId)
    emit('related-doc-created', { docId, openAfter: true, projectId: project })
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    const msg = Array.isArray(detail)
      ? detail.map((d: any) => d.msg ?? d).join(', ')
      : (detail ?? t('main.review_action_bar.toast_conversation_failed'))
    showToast(msg, 'danger')
  }
}

// TR0044.0010 rev3/rev4: conversation mention copy. Real-time chat isn't wired yet, so
// a turn is delivered to the AI manually — the reviewer asked for a mention-copy button on the
// chat. We issue an edit-scope token bound to the CH doc, but NOT the server's STANDARD
// edit mention: rev4 reject — "the mention should be copied in a chat-only form. Strip out
// Q info and other useless info and keep it compact". What we copy is a compact chat-only
// mention with just: read the conversation, append one AI turn (§6), submit via inbox
// edit. The AI reads the conversation and appends its reply turn, the same inbox-edit path
// AI turns already use.
// 0293: that mention used to be assembled here in TS. It now comes back as token.mention
// from /token/issue for action_scope:'chat', so the [Copy Mention] text and the in-app Invoke AI
// prompt are produced by the same server function instead of two hand-synced copies.
// Text of the last failed mention copy, per CH tab (B0001 / group 0240). Feeds
// ConversationView's inline manual-copy panel — the CH-only replacement for the
// full-screen ClipboardFallbackModal. Keyed by tab so two open chats can't cross-feed.
const convManualCopy = ref<Record<string, string>>({})

function setConvManualCopy(tabId: string, text: string | null) {
  const next = { ...convManualCopy.value }
  if (text) next[tabId] = text
  else delete next[tabId]
  convManualCopy.value = next
}

async function onConversationCopyMention(tabId: string, opts?: { auto?: boolean }) {
  const h = docHeaderRefs[tabId]
  const project = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId)
  // High-frequency path (auto-copies on every send), so the activation-preserving deferred
  // write matters most here (B0001). Token issuance happens inside the producer.
  let token: IssuedToken | null = null
  const ok = await copyToClipboardDeferred(async () => {
    token = await issueToken({
      project,
      ...(gParts?.module != null ? { module: gParts.module } : {}),
      group: gParts?.groupCode ?? groupId,
      doc_ref: tabId,
      // 0293: 'chat' asks for the compact CH mention. The token itself is still an edit
      // grant (the response's action_scope reads 'edit'), so nothing downstream changes.
      action_scope: 'chat',
    })
    // No local fallback: the chat mention exists only server-side now, and a mention
    // without its token would be useless to paste anyway.
    if (!token?.mention) throw new ClipboardAbort()
    return token.mention
  })
  if (token == null) return
  if (ok) {
    // 0085: an auto-copy (fired by every send when the toggle is on) stays silent so it
    // doesn't spam a success toast each turn; the manual button still confirms with one.
    if (!opts?.auto) showToast(t('main.main_panel.toast_mention_copied'), 'success')
    setConvManualCopy(tabId, null)
    void recordMentionCopy(tabId, 'edit')
    return
  }
  // Copy failed. NEITHER path may call notifyCopyFailure() here: it opens the full-screen
  // ClipboardFallbackModal, and R0001 asked for CH to stay dialog-free.
  //
  // Group 0221 routed failures to that modal; group 0235 carved the AUTO path out to a
  // toast but left the MANUAL path on the modal, betting that "the manual button is a fresh
  // click, so re-copying from the modal is meaningful". That bet was wrong (group 0240
  // NR0003): the manual click ALSO awaits issueToken() before the write, so on this HTTP
  // origin it fails exactly as reliably as the auto copy — and the auto-failure toast sent
  // the user straight to that button, producing a toast → click → fail → full-screen modal
  // loop on every single send. Both paths now recover through ConversationView's inline
  // panel: identical affordance (pre-selected text + fresh-click re-copy that DOES land,
  // because the text is already resolved), zero screen coverage.
  //
  // consumeLastFailedCopyText() also clears the record so a later unrelated failure with no
  // text of its own can't pull this mention back into the modal on some other surface.
  const failedText = consumeLastFailedCopyText()
  if (!failedText) {
    // The producer never yielded text, so there is nothing to hand-copy — just report it.
    showToast(t('main.main_panel.toast_copy_failed'), 'warning')
    return
  }
  setConvManualCopy(tabId, failedText)
  // The inline panel is its own (non-silent) notice for a manual click the user is watching;
  // an auto copy fires unattended after a send, so that one still says what happened.
  if (opts?.auto) showToast(t('main.conversation_view.auto_copy_failed'), 'warning')
}

// Honest clipboard write of a ready string (B0001 / group 0133). Returns whether the
// clipboard was actually set so the caller can toast success vs. warning — the prior version
// swallowed failures and every caller falsely claimed success, so an intermittently-failed
// write left the user pasting stale/empty clipboard content ("truncated"/"blank"/"nothing").
function doClipboardCopy(text: string): Promise<boolean> {
  return copyToClipboard(text)
}

// Every failed copy lands here (B0001 / group 0221). When the text made it out of the
// producer, open the manual-copy fallback modal — on this HTTP LAN deploy the write itself
// (execCommand) is what fails intermittently, and re-copying from the modal's fresh click is
// the reliable recovery. Only when no text is known (producer failed) fall back to a toast;
// its wording says "failed, retry", not the old misdiagnosis "browser not supported".
function notifyCopyFailure() {
  if (!openClipboardFallback()) {
    showToast(t('main.main_panel.toast_copy_failed'), 'warning')
  }
}

// Centralized mention copy that preserves the click's user activation across an async text
// producer (e.g. one that issues a token over the network) — the B0001 root cause was awaiting
// the token BEFORE the clipboard write, letting the activation lapse. The producer may throw
// ClipboardAbort to bail silently (e.g. token issuance failed and already toasted); pass
// `aborted()` so we can distinguish that from a genuine clipboard failure.
async function copyMentionDeferred(
  produce: () => Promise<string>,
  opts: {
    tabId: string | null | undefined
    kind: MentionKind
    successToast: string
    aborted?: () => boolean
  },
): Promise<boolean> {
  const ok = await copyToClipboardDeferred(produce)
  if (opts.aborted?.()) return false
  if (ok) {
    showToast(opts.successToast, 'success')
    void recordMentionCopy(opts.tabId, opts.kind)
  } else {
    notifyCopyFailure()
  }
  return ok
}

// A teleported chat is re-attached under a new parent, and a re-attached scroll container
// comes back at the top. Pin it to the newest message again once the move has landed, on both
// legs — the chat should open and close showing where the conversation actually is.
function repinChat(tabId: string) {
  void nextTick(() => convViewRefs[tabId]?.scrollToBottom?.())
}
async function openFullView(tab: Tab) {
  fullViewTab.value = tab
  fullViewVisible.value = true
  if (tab.typeCode === 'CH') {
    // The dialog body is the teleport target, so it has to be in the DOM before the move.
    await nextTick()
    if (fullViewVisible.value && fullViewTab.value?.id === tab.id) {
      convFullViewActive.value = true
      repinChat(tab.id)
    }
  }
}
async function closeFullView() {
  if (convFullViewActive.value) {
    // Give the chat back to its card while the dialog still stands: removing the dialog first
    // would tear the teleported node out of the document along with it.
    const chatTabId = fullViewTab.value?.id
    convFullViewActive.value = false
    await nextTick()
    if (chatTabId) repinChat(chatTabId)
  }
  fullViewVisible.value = false
  fullViewTab.value = null
}
// The teleported chat lives in the active tab's card, so a tab switch would unmount it and
// leave the dialog empty. Nothing can reach the tab bar while the modal covers it today; this
// keeps the two consistent regardless.
watch(activeTabId, () => {
  if (convFullViewActive.value) void closeFullView()
})
async function editFromFullView(tab: Tab) {
  await closeFullView()
  await openEditModal(tab)
}
async function openEditModal(tab: Tab) {
  editTab.value = tab
  editVisible.value = true
  editContent.value = ''
  editError.value = ''
  editSourceEtag.value = ''
  editLoading.value = true
  headerEditModeVisible.value = false
  try {
    let raw = ''
    if (isDocumentTab(tab)) {
      const res = await getRequest<{ content: string }>(`/api/v1/documents/content?doc_id=${encodeURIComponent(tab.id)}`)
      raw = (res.data as any)?.content ?? ''
    } else if (tab.projectId && getTabSourcePath(tab)) {
      const res = await api.get<string>(sourceContentUrl(tab), { responseType: 'text' })
      raw = res.data ?? ''
      editSourceEtag.value = String(res.headers?.etag ?? '')
    } else {
      throw new Error(t('main.main_panel.error_info_unavailable'))
    }
    const parsed = parseFrontmatter(raw)
    editFrontmatter.value = parsed.frontmatter
    editBody.value = parsed.body
  } catch (e: any) {
    editError.value = e?.response?.data?.detail ?? e?.message ?? t('main.document_preview.load_failed')
  } finally {
    editLoading.value = false
  }
}
function closeEditModal() {
  if (editSaving.value) return
  editVisible.value = false
  editTab.value = null
  editContent.value = ''
  editFrontmatter.value = ''
  editBody.value = ''
  editError.value = ''
  editSourceEtag.value = ''
  headerEditModeVisible.value = false
}
async function saveEditContent() {
  if (!editTab.value || editSaving.value) return
  editSaving.value = true
  editError.value = ''
  const content = headerEditModeVisible.value
    ? editFullContent.value
    : (editFrontmatter.value ? editFrontmatter.value + '\n' + editBody.value : editBody.value)
  // flowgate.default.0176 T0010 §a: base-checkout dirty status returned by a
  // source-file save (null for a document-content save, which never touches it).
  let baseGit: { dirty?: boolean; files?: string[] } | null = null
  try {
    if (isDocumentTab(editTab.value)) {
      await patchRequest(`/api/v1/documents/content`, {
        doc_id: editTab.value.id,
        content,
      })
    } else if (editTab.value.projectId && getTabSourcePath(editTab.value)) {
      const resp = await api.patch(
        sourceContentUrl(editTab.value),
        { content },
        editSourceEtag.value
          ? { headers: { 'If-Match': editSourceEtag.value } }
          : undefined,
      )
      baseGit = resp?.data?.base_git ?? null
      // 0177 L0002 §2.6-a badge trigger 2/4: the save response carries the fresh
      // base-checkout dirty set — feed the file-tree "modified" badges directly.
      if (baseGit) {
        explorerStore.setBaseDirtyFiles(
          editTab.value.projectId,
          Array.isArray(baseGit.files) ? baseGit.files : [],
        )
      }
      if (editTab.value.gitGroupId) {
        const projectId = editTab.value.projectId
        const groupId = editTab.value.gitGroupId
        explorerStore.invalidateProject(projectId)
        await Promise.allSettled([
          explorerStore.fetchGroupBranchTree(projectId, groupId),
          explorerStore.fetchGroupBranchChanges(projectId, groupId),
        ])
      }
    } else {
      throw new Error(t('main.main_panel.error_info_unavailable'))
    }
    await mdViewerRefs[editTab.value.id]?.loadContent?.()
    await textViewerRefs[editTab.value.id]?.loadContent?.()
    // 0177 NR0016 §1: a successful save always closes the modal — a source-file
    // save on the base checkout is dirty by definition, so the former keep-open
    // warning (0176 T0010 §a) held the dialog hostage on every save. The tree
    // "M" markers (fed above) and the header Git panel carry the persistent
    // state; one concise toast is the only inline signal.
    if (baseGit?.dirty) {
      showToast(t('main.git_finalize.base_dirty_after_save'), 'warning')
    } else {
      showToast(t('main.document_preview.save_success'), 'success')
    }
    editSaving.value = false
    closeEditModal()
  } catch (e: any) {
    editError.value = e?.response?.data?.detail ?? e?.message ?? t('main.document_preview.save_failed')
    showToast(editError.value, 'danger')
  } finally {
    editSaving.value = false
  }
}
const activeProjects = computed(() =>
  projectStore.projects.filter((p) => p.is_active === 1).length || '—',
)

const totalDocs = computed(() => {
  const pid = projectStore.currentProjectId
  if (!pid) return '—'
  // 0454 T0007 rev2 review fix — reads the overview summary the sidebar's own tree fetch
  // cached (see explorerStore.fetchGroupTree). Still counts every document in the project,
  // including ones inside completed / discarded groups.
  const summary = explorerStore.getCachedGroupOverviewSummary(pid)
  if (!summary) return '—'
  return summary.total_documents
})

// "in-progress workflows": total active workflows for the project (overall in-progress load).
const inProgressWorkflows = computed<number | string>(() => {
  const total = dashboardEntry.value?.data?.active_workflows.total
  return typeof total === 'number' ? total : '—'
})

// "in progress": groups currently mid-workflow, counted by the group's LAST
// (highest-numbered) document type — an instruction awaiting its report
// (T / N / TS) or a report awaiting the next decision (xR family: NR / TR /
// TSR, i.e. a multi-letter code ending in 'R'; plain 'R' requirement is not
// counted). Completed groups (final-approved / discarded heads) are excluded
// since the card means "in progress". The card label is just "in progress" — the
// parenthetical definition is not shown on the card.
//
// 0454 T0007 review fix — this used to be computed here client-side (WORKING_HEAD_TYPES /
// isWorkingHeadType) off the full group tree; that logic moved server-side into
// build_overview_summary / _is_working_head_type (tree_routes.py), which this computed now
// just reads the result of. Left as dead code by that move — vue-tsc's noUnusedLocals catches
// exactly this (rev2 fix: removed here instead of left unused).
const workingGroups = computed<number | string>(() => {
  const pid = projectStore.currentProjectId
  if (!pid) return '—'
  // 0454 T0007 review fix — reads the lightweight overview summary instead of the full
  // group tree. working_groups is computed server-side by build_overview_summary using the
  // exact same last-doc-per-parent rule as before (see tree_routes.py).
  const summary = explorerStore.getCachedGroupOverviewSummary(pid)
  if (!summary) return '—'
  return summary.working_groups
})

const typeDistribution = computed(() => {
  const pid = projectStore.currentProjectId
  if (!pid) return []
  // 0454 T0007 review fix — reads the lightweight overview summary instead of the full
  // group tree; type_distribution arrives in the same (first-seen) order the old client-side
  // scan produced, so this sort keeps the exact same result.
  const summary = explorerStore.getCachedGroupOverviewSummary(pid)
  if (!summary) return []
  return [...summary.type_distribution].sort((a, b) => b.count - a.count)
})

const typeDistMax = computed(() =>
  // Bar denominator is the WHOLE distribution, not the current page — keep it so
  // small counts on later pages don't get re-normalized to 100%.
  typeDistribution.value.reduce((max, item) => Math.max(max, item.count), 1),
)

// Type distribution is paginated so the card stays short even when a project
// has many document types (PM 0069: rows were dumped in arbitrary order and
// the list grew unbounded). The list above is sorted by count desc; here we
// slice it into fixed-size pages.
const DIST_PAGE_SIZE = 6
const distPage = ref(0)
const distPageCount = computed(() =>
  Math.max(1, Math.ceil(typeDistribution.value.length / DIST_PAGE_SIZE)),
)
const distPageItems = computed(() => {
  const start = distPage.value * DIST_PAGE_SIZE
  return typeDistribution.value.slice(start, start + DIST_PAGE_SIZE)
})

// Distribution changes reactively (project switch / tree refresh). A stale page
// index would slice past the end and blank the card, so reset to the first page
// whenever the distribution changes.
watch(typeDistribution, () => {
  distPage.value = 0
})

const TYPE_COLOR_MAP: Record<string, string> = {
  R: '#2563eb', DS: '#7c3aed', D: '#ea580c', T: '#0891b2',
  TR: '#0284c7', M: '#64748b', Q: '#d97706', AC: '#16a34a',
  N: '#0284c7', NR: '#6366f1', TS: '#db2777', L: '#7c3aed',
  A: '#16a34a', B: '#dc2626', P: '#0d9488', DB: '#ca8a04',
  WP: '#14b8a6',
}

function typeBarWidth(count: number): number {
  return Math.round((count / typeDistMax.value) * 100)
}

function typeBarColor(type: string): string {
  return TYPE_COLOR_MAP[type] ?? '#94a3b8'
}

const SUPPORTED_ACTIVITY_TYPES = new Set([
  'document_created',
  'document_edited',
  'document_state_changed',
  'workflow_state_changed',
  'question_answered',
  'group_approved',
])

// Cards preview only the newest few rows so the overview stays short even when
// many documents are active (PM: long lists made the whole page scroll). The
// "show-all / collapse" toggles reveal or re-collapse the rest in place.
// PM: expanding must NOT resize the box — the list keeps the exact preview
// height and only gains an inner scrollbar. We measure the preview list right
// before expanding and lock that height while expanded.
const ACTIVITY_PREVIEW_COUNT = 10
const WORKFLOW_PREVIEW_COUNT = 3
const OPEN_Q_PREVIEW_COUNT = 5
const activitiesExpanded = ref(false)
const workflowsExpanded = ref(false)
const qListExpanded = ref(false)
const activityListEl = ref<HTMLElement | null>(null)
const workflowListEl = ref<HTMLElement | null>(null)
const qListEl = ref<HTMLElement | null>(null)
const activityListLockHeight = ref<number | null>(null)
const workflowListLockHeight = ref<number | null>(null)
const qListLockHeight = ref<number | null>(null)

function toggleExpanded(
  expanded: Ref<boolean>,
  listEl: Ref<HTMLElement | null>,
  lockHeight: Ref<number | null>,
): void {
  if (!expanded.value) {
    // Collapsed list is content-fit, so its current height IS the preview height.
    lockHeight.value = listEl.value?.offsetHeight ?? null
  } else {
    lockHeight.value = null
  }
  expanded.value = !expanded.value
}

function toggleActivitiesExpanded(): void {
  toggleExpanded(activitiesExpanded, activityListEl, activityListLockHeight)
}

function toggleWorkflowsExpanded(): void {
  toggleExpanded(workflowsExpanded, workflowListEl, workflowListLockHeight)
}

function toggleQListExpanded(): void {
  toggleExpanded(qListExpanded, qListEl, qListLockHeight)
}

const activityListLockStyle = computed(() =>
  activitiesExpanded.value && activityListLockHeight.value !== null
    ? { height: `${activityListLockHeight.value}px` }
    : undefined,
)
const workflowListLockStyle = computed(() =>
  workflowsExpanded.value && workflowListLockHeight.value !== null
    ? { height: `${workflowListLockHeight.value}px` }
    : undefined,
)
const qListLockStyle = computed(() =>
  qListExpanded.value && qListLockHeight.value !== null
    ? { height: `${qListLockHeight.value}px` }
    : undefined,
)

const allRecentActivities = computed(() =>
  (dashboardEntry.value?.data?.recent_activities.items ?? [])
    .filter((item) => SUPPORTED_ACTIVITY_TYPES.has(item.activity_type)),
)
const recentActivities = computed(() =>
  activitiesExpanded.value
    ? allRecentActivities.value
    : allRecentActivities.value.slice(0, ACTIVITY_PREVIEW_COUNT),
)
const allActiveWorkflows = computed(() =>
  dashboardEntry.value?.data?.active_workflows.items ?? [],
)
const activeWorkflows = computed(() =>
  workflowsExpanded.value
    ? allActiveWorkflows.value
    : allActiveWorkflows.value.slice(0, WORKFLOW_PREVIEW_COUNT),
)

// Open queries reuse the same preview/fold idiom as activities & workflows above:
// the raw qList is the source, openQueries is the display slice (preview when
// collapsed, full when expanded). No paging — consistency + low regression.
const allOpenQueries = computed(() => qList.value)
const openQueries = computed(() =>
  qListExpanded.value
    ? allOpenQueries.value
    : allOpenQueries.value.slice(0, OPEN_Q_PREVIEW_COUNT),
)

// Activity row presentation (dot colour / action label / relative time) is shared with the 🔔
// notification center via useActivityFormat (R0001 group 0045 / NR0003 option A).
const { activityColor, activityActionLabel, formatDashboardTime } = useActivityFormat()

function workflowStageLabel(workflow: DashboardWorkflow): string {
  const key = workflow.stage.state === 'pending'
    ? 'main.overview.workflow_pending'
    : workflow.stage.state === 'done'
      ? 'main.overview.workflow_done'
      : 'main.overview.workflow_in_progress'
  return t(key, { type: workflow.stage.type_code })
}

// Overview card navigation (recent activity / workflow rows) shares one implementation with the 🔔
// notification center via useDashboardNavigation (R0001 group 0045 / NR0003 option A).
const { openDashboardTarget } = useDashboardNavigation()

const docInfoCollapsed = ref(false)
const guideDismissed = ref(false)
const showQuickOpen = ref(false)
const quickQuery = ref('')
const quickInputRef = ref<HTMLInputElement | null>(null)

function getDismissKey(): string {
  const projectId = projectStore.currentProjectId
  return projectId ? `fg_guide_dismissed_${projectId}` : 'fg_guide_dismissed'
}

function updateGuideDismissedState() {
  guideDismissed.value = localStorage.getItem(getDismissKey()) === '1'
}

onMounted(() => {
  updateGuideDismissedState()
})

function dismissGuide() {
  guideDismissed.value = true
  localStorage.setItem(getDismissKey(), '1')
}

function reopenGuide() {
  localStorage.removeItem(getDismissKey())
  guideDismissed.value = false
}

watch(() => projectStore.currentProjectId, (projectId) => {
  updateGuideDismissedState()
  fetchQList()
  if (projectId) void dashboardStore.fetchSummary(projectId)
}, { immediate: true })

// 0454 T0007 — the overview cards below (총 문서 수 / 진행 중 / 타입 분포) read
// `explorerStore.getCachedGroupOverviewSummary`. This file no longer drives that cache itself —
// see explorer.ts's fetchGroupTree docstring (rev5): every group-tree fetch now asks the server
// for `overview_summary` and writes it into the same cache as a side effect, so GroupExplorer.vue
// (the sidebar) already keeps it current on every trigger that matters here — project switch,
// toggle, SSE/manual refresh, `fg:group_tree_changed` — without this file issuing a request of
// its own.
//
// rev0 warmed the FULL group tree here on every default-screen entry, landing on top of the
// sidebar's own pruned default fetch and doubling the transfer cost the display-state pruning
// was meant to cut (review finding on rev0). rev1 replaced that with a dedicated
// `fetchGroupOverviewSummary` call to a separate lightweight endpoint — smaller on the wire, but
// it reran the server's full get_group_tree DB path a second time per page load, and nothing
// refetched it when `invalidateProject` cleared it on an SSE/manual refresh, so the cards went
// stale until an unrelated project/tab switch happened to retrigger the old watcher here (both
// were rev1 review findings). rev2 folded the aggregate into the SAME `/groups/tree` response
// GroupExplorer (the sidebar) already fetches, which fixed both — but it also changed what
// `/groups/tree` returns to every pre-existing caller, breaking the T0006 §1.1 compatibility
// promise (review finding on rev2). rev3 put the aggregate back on its own route and had THIS
// file fetch it directly, on the same triggers as the sidebar's own tree load — but the two were
// still two independent requests from two independent Vue watchers, so nothing guaranteed they
// overlapped: a sequential arrival cost a second full `get_group_tree` DB call every time (rev4
// review finding). rev5 removes the second request entirely instead of tuning its timing —
// GroupExplorer's OWN tree fetch now carries the aggregate, and this file just reads the cache
// it fills.

watch(() => props.overviewRefreshToken, () => {
  if (projectStore.currentProjectId) void fetchQList()
  // A coalesced screen refresh is also an SSE recovery boundary. The store keeps
  // handoff-pending cards pollable and chooses between adoption and bounded completion.
  void aiInvokeRunsStore.refreshAllRunning()
})

watch(showQuickOpen, async (val) => {
  if (val) {
    quickQuery.value = ''
    await nextTick()
    quickInputRef.value?.focus()
  }
})

const { register, unregister } = useShortcuts(
  () => { /* Quick Open disabled */ },
  () => emit('create-requirement'),
)
onMounted(register)
onBeforeUnmount(unregister)

watch(textWrapEnabled, (enabled) => {
  try {
    localStorage.setItem('flowgate:text-viewer:wrap-lines', enabled ? '1' : '0')
  } catch {
    /* ignore storage errors */
  }
})
</script>

<style scoped>
.dashboard-refreshing {
  color: var(--text-m);
  font-size: .72rem;
}

.overview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.overview-refresh {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dashboard-row {
  width: 100%;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.dashboard-row:hover:not(:disabled) {
  background: var(--surface-hover, rgba(148, 163, 184, .08));
}

.dashboard-row--disabled {
  cursor: default;
}

.dashboard-list-body {
  min-height: 0;
}

/* Work 2 (0080): the .content-grid stretches both columns to equal height, so the
   left "recent activity" card border already aligns with the right column. The reported gap
   is the card's *content* falling short of the stretched height. Make the left card a
   flex column whose list fills the slack so its bottom line meets the right column.
   (Only the left card is a direct grid child; the right column is .right-col.) */
.content-grid > .card {
  display: flex;
  flex-direction: column;
}
.content-grid > .card > .dashboard-list-body {
  display: flex;
  flex: 1;
  flex-direction: column;
}
/* Grow the list to fill ONLY when collapsed. When expanded the inline height-lock +
   internal scroll governs (see .dashboard-scroll-list); growing it would defeat the
   no-jump lock. */
.content-grid > .card > .dashboard-list-body > .act-list:not(.dashboard-scroll-list) {
  flex: 1 1 auto;
  min-height: 0;
}
/* The show-all button always rides the card bottom border — covers the expanded
   (locked + scrolling) state and any residual slack. */
.content-grid > .card > .dashboard-list-body > .dashboard-view-all {
  margin-top: auto;
}

.dashboard-scroll-list {
  /* Expanded (show-all) height is locked inline to the measured preview height,
     so the box never resizes on toggle — it only gains this inner scrollbar. */
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.activity-target {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.activity-target .doc-tag {
  flex-shrink: 0;
}

.activity-doc-id {
  flex-shrink: 0;
  color: var(--text);
  font-size: .78rem;
  white-space: nowrap;
}

.activity-target-title {
  overflow: hidden;
  color: var(--text-m);
  font-size: .76rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-group-icon {
  flex-shrink: 0;
  color: var(--text-m);
}

.workflow-list {
  display: flex;
  flex-direction: column;
}

.workflow-item {
  display: block;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border);
}

.workflow-item:last-child {
  border-bottom: 0;
}

.workflow-content {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 7px;
  font-size: .8rem;
}

.workflow-heading,
.workflow-requirement,
.workflow-footer {
  display: flex;
  min-width: 0;
  align-items: center;
}

.workflow-heading {
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}

/* The full requirement doc id leads the card; it must stay fully readable, so it
   wraps rather than truncating (the group is encoded in the id — no separate
   group-name line). */
.workflow-heading > strong {
  min-width: 0;
  color: var(--text);
  font-size: .8rem;
  overflow-wrap: anywhere;
}

.workflow-requirement {
  overflow: hidden;
  color: var(--text-m);
  font-size: .75rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dashboard-view-all {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  border: 0;
  border-top: 1px solid var(--border);
  background: transparent;
  color: var(--text-m);
  cursor: pointer;
  font: inherit;
  font-size: .74rem;
  font-weight: 600;
}

.dashboard-view-all:hover {
  background: var(--surface-hover, rgba(148, 163, 184, .08));
  color: var(--text);
}

.dashboard-view-all i {
  font-size: .62rem;
}

.dist-pager-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: var(--text-m);
  cursor: pointer;
  font-size: .62rem;
}

.dist-pager-btn:hover:not(:disabled) {
  background: var(--surface-hover, rgba(148, 163, 184, .08));
  color: var(--text);
}

.dist-pager-btn:disabled {
  opacity: .4;
  cursor: default;
}

.workflow-status-badge {
  flex-shrink: 0;
  padding: 2px 7px;
  border-radius: 999px;
  font-size: .68rem;
  font-weight: 600;
}

.workflow-status-badge--pending {
  background: #fef3c7;
  color: #b45309;
}

.workflow-status-badge--in_progress {
  background: #dbeafe;
  color: #1d4ed8;
}

.workflow-status-badge--done {
  background: #dcfce7;
  color: #15803d;
}

.workflow-progress-track {
  display: block;
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--border);
}

.workflow-progress-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  transition: width .2s ease;
}

.workflow-footer {
  justify-content: space-between;
  gap: 10px;
}

.workflow-stage-flow {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 6px;
}

.workflow-stage-flow i {
  color: var(--text-m);
  font-size: .55rem;
}

.workflow-stage-flow .doc-tag {
  font-size: .62rem;
  padding: 1px 4px;
}

.workflow-content small {
  overflow: hidden;
  color: var(--text-m);
  font-size: .72rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dashboard-inline-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid var(--border);
  color: var(--danger);
  font-size: .72rem;
}

.dashboard-inline-error button {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-weight: 600;
}

/* .card (global) has overflow:hidden which clips the edit-dropdown-menu.
   Override only for md-preview-card so the dropdown can extend beyond the card boundary. */
.md-preview-card {
  overflow: visible;
}

/* CH (conversation) — the card body hosts the chat (scrolling log + pinned
   composer). It fills the conversation card (which itself flexes to fill the
   space between the workflow strip and the sticky action bar — see
   .content-wrap--conversation below), so the message list scrolls internally
   and the whole surface grows/shrinks fluidly with the window instead of being
   a fixed-height box that forces a page scrollbar. TR0044.0010 rev7. */
.conv-card-bd {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 0;
}
.conv-card-bd > * {
  flex: 1;
  min-height: 0;
}
.doc-tag.c-CH {
  background: #06b6d4;
  color: #fff;
}

/* AC (final approval) guidance panel — file-less step, no body preview. */
.ac-final-approval-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 10px;
  padding: 48px 24px;
}
.ac-fa-icon {
  font-size: 2.5rem;
  color: var(--primary, #2563eb);
  opacity: 0.85;
}
.ac-fa-icon-done {
  color: var(--success, #16a34a);
}
.ac-fa-title {
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--text, #1e293b);
  margin: 6px 0 0;
}
.ac-fa-desc {
  font-size: 0.875rem;
  color: var(--text-m, #64748b);
  max-width: 420px;
  line-height: 1.5;
  margin: 0;
}

.text-preview-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.text-preview-body {
  flex: 1;
  min-height: 0;
  padding: 0;
}

.content-wrap--text-preview {
  overflow: hidden;
}

.content-wrap--text-preview .content-panel.active,
.content-wrap--text-preview .doc-with-panel,
.content-wrap--text-preview .doc-main {
  height: 100%;
  min-height: 0;
}

.content-wrap--text-preview .doc-with-panel {
  align-items: stretch;
}

/* CH (conversation): like text-preview, the chat surface must fill the viewport
   between the workflow strip and the sticky action bar — fluidly growing and
   shrinking as the window resizes — rather than being a fixed-height box that
   spills past the action bar and forces the page into its own scrollbar.
   The page scroll is turned off (overflow:hidden); only the message log inside
   ConversationView scrolls. TR0044.0010 rev7. */
.content-wrap--conversation {
  overflow: hidden;
}

.content-wrap--conversation .content-panel.active,
.content-wrap--conversation .doc-with-panel,
.content-wrap--conversation .doc-main {
  height: 100%;
  min-height: 0;
}

.content-wrap--conversation .doc-with-panel {
  align-items: stretch;
}

/* doc-main stacks the document header + workflow strip (natural height) and lets
   the conversation card flex to consume whatever height is left. */
.content-wrap--conversation .doc-main {
  display: flex;
  flex-direction: column;
}

.content-wrap--conversation .conv-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.document-modal__body :deep(.text-viewer) {
  height: 100%;
}

/* CH full view keeps the same single-scroll flex chain as the inline card. */
.document-modal__body--conversation {
  display: flex;
  min-height: 0;
  padding: 0;
  overflow: hidden;
}

.document-modal__body--conversation :deep(.conv-view) {
  flex: 1;
  min-width: 0;
  min-height: 0;
}

/* Narrow windows give the chat the whole overlay instead of the centred 1180px box. The
   height is taken from the overlay (`%`), never from the viewport: `100dvh - 16px` is
   taller than the below-header container at *every* window height, so it clipped the
   composer by a constant 18px on every screen under 820px wide. The container cap from
   `.modal-bg--below-header > .modal-box` is left in place — `max-height: none` here is
   what let the box outgrow its track in the first place. */
@media (max-width: 820px) {
  .document-modal:has(.document-modal__body--conversation) {
    width: calc(100vw - 16px);
    height: calc(100% - 16px);
    max-width: none;
  }
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.text-wrap-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: .76rem;
  color: var(--text-m);
  user-select: none;
}

.text-wrap-toggle input {
  margin: 0;
  accent-color: var(--primary);
}

.document-modal__body {
  padding: 0;
}

.document-modal__body :deep(.md-viewer) {
  height: 100%;
}

.document-editor {
  padding: 0;
  /* The comfortable editor height comes from `.document-modal`'s own height, not
     from a minimum here. A vh minimum is a flex *shrink floor* decoupled from the
     px-capped box track, so past ~1187px viewport height it pushes `.modal-ft`
     out of `.modal-box { overflow: hidden }` and the save button is unreachable. */
  min-height: 0;
  display: flex;
  /* Override the shared `.modal-bd { overflow-y: auto }` for the edit modal so
     the inner textarea is the *sole* scroll container. With a height pinned on
     the textarea AND this body scrollable, both scrolled at once →
     the reported double scrollbar. Clipping here leaves only the textarea. */
  overflow: hidden;
}

.document-editor__textarea {
  width: 100%;
  /* Fill the editor track instead of pinning height to a viewport unit. A vh
     pin is decoupled from the body track (bounded by `.modal-box max-height:
     88vh` minus header/footer); when the two mismatch both scroll. min-height:0
     + stretch makes the textarea exactly fill the body and be the only scroller. */
  flex: 1 1 auto;
  min-height: 0;
  resize: none;
  border: 0;
  outline: none;
  padding: 18px 20px;
  background: #0f172a;
  color: #e2e8f0;
  font-family: 'JetBrains Mono', monospace;
  font-size: .8125rem;
  line-height: 1.7;
}

.document-editor__textarea--frontmatter {
  border-bottom: 1px solid var(--border);
}

.document-editor__state {
  width: 100%;
  padding: 40px 24px;
  text-align: center;
  color: var(--text-m);
}

.document-editor__state--error {
  color: var(--danger);
}

.edit-dropdown-wrap {
  position: relative;
  display: inline-block;
}
.edit-caret {
  margin-left: 4px;
  font-size: 0.7em;
  opacity: 0.7;
}
.edit-dropdown-menu {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  background: var(--bg, #fff);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
  min-width: 160px;
  z-index: 100;
  display: flex;
  flex-direction: column;
  padding: 4px;
}
.edit-dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: none;
  border: none;
  text-align: left;
  font-size: 0.85rem;
  color: var(--text, #1f2937);
  cursor: pointer;
  border-radius: 4px;
  white-space: nowrap;
}
.edit-dropdown-item:hover {
  background: var(--bg-hover, rgba(0, 0, 0, 0.05));
}
.edit-dropdown-item i {
  width: 16px;
  text-align: center;
  color: var(--text-m, #6b7280);
}
.edit-dropdown-enter-active,
.edit-dropdown-leave-active {
  transition: opacity 0.14s ease, transform 0.14s ease;
}
.edit-dropdown-enter-from,
.edit-dropdown-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.doc-main {
  position: relative;
}

/* 0339: archive stays amber/reversible; only purge uses the destructive red. */

.git-archive-overlay {
  z-index: 1200;
}
.git-archive-modal {
  width: min(920px, calc(100vw - 32px));
  max-height: min(820px, calc(100dvh - 32px));
}
.git-archive-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}
.git-archive-intro {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface-h);
  color: var(--text-s);
  font-size: .78rem;
  line-height: 1.6;
}
.git-archive-intro :deep(svg) {
  flex: 0 0 auto;
  margin-top: 2px;
  color: #d97706;
}
.git-archive-state {
  display: flex;
  min-height: 120px;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-m);
}
.git-archive-state--error {
  color: var(--danger);
}
.git-archive-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.git-archive-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
}
.git-archive-row__body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  color: var(--text-m);
  font-size: .73rem;
}
.git-archive-row__body strong {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  overflow-wrap: anywhere;
  color: var(--text);
  font-size: .8rem;
}
.git-archive-row__body code {
  font-family: 'JetBrains Mono', monospace;
}
.git-archive-row__body .git-archive-sha {
  padding: 0;
  background: none;
  color: var(--text-s);
}
.git-archive-row__body .git-archive-refs {
  overflow-wrap: anywhere;
  padding: 5px 7px;
  border-radius: 5px;
  background: var(--surface-h);
  color: var(--text-s);
  line-height: 1.55;
  white-space: normal;
}
.git-archive-reason {
  color: var(--text-s);
}
.git-archive-branch {
  color: var(--primary);
  font-family: 'JetBrains Mono', monospace;
}
.git-archive-purge {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px 16px;
  margin-top: 4px;
  padding: 12px;
  border: 1px dashed rgba(220, 38, 38, .45);
  border-radius: var(--r);
  background: rgba(254, 242, 242, .45);
  color: var(--text-s);
  font-size: .76rem;
}
.git-archive-purge strong,
.git-archive-purge p {
  grid-column: 1 / -1;
  margin: 0;
}
.git-archive-purge strong {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--danger);
}
.git-archive-purge label {
  display: flex;
  align-items: center;
  gap: 7px;
}
@media (max-width: 680px) {
  .git-archive-row {
    grid-template-columns: auto minmax(0, 1fr);
  }
  .git-archive-row > .btn {
    grid-column: 2;
    justify-self: start;
  }
  .git-archive-purge {
    grid-template-columns: 1fr;
  }
  .git-archive-purge .btn {
    justify-self: start;
  }
}.ro-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 9px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-m);
  background: var(--surface-h);
  font-size: .66rem;
  font-weight: 700;
}

.ro-badge-sm { margin-left: auto; }
</style>
