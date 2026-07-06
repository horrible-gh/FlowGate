<template>
  <main class="app-main">
    <TabBar @new-tab="showQuickOpen = true" />

    <div
      class="content-wrap"
      :class="{
        'has-sticky-footer': activeTabId != null && getActionBarMode(activeTabId) != null,
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
          <!-- 0155: test-failure strip (confirmed design B) — self-hides unless the
               viewed doc's latest test run failed. Sits above DocHeader as the first
               child of .doc-main, per the confirmed layout. A failed run assembles no
               TSR, so this is the only in-context signal of the failure (R0001). -->
          <TestFailStrip
            :test-run="exposedValue(docHeaderRefs[tab.id]?.testRun) ?? null"
            :doc-id="tab.id"
          />
          <!-- CH (conversation) is a normal workflow node: it shows the standard document
               header AND the workflow strip, exactly like every other doc. The chat surface
               (bubbles + composer + mention-copy) renders below in its own card. TR0044.0010 rev6
               — restores the header/workflow that rev5 wrongly hid: the thing the reviewer
               wanted gone was the redundant chat intro, never the document header itself
               ("where did you sell off the document header and workflow"). -->
          <DocHeader
            v-if="tab.typeCode && (editTab?.id !== tab.id || headerEditModeVisible)"
            :ref="(el) => bindActiveRef(docHeaderRefs, tab.id, el)"
            :tab="tab"
            @related-doc-created="emit('related-doc-created', $event)"
            @doc-updated="onDocHeaderUpdated"
            @workflow-decided="onWorkflowDecided"
          />
          <DocWorkflow
            v-if="tab.typeCode && tab.typeCode !== 'DC'"
            :tab="tab"
            :workflow-decided="getWorkflowViewState(tab.id).mode !== 'workflow'"
            :parent-r-doc-id="exposedValue(docHeaderRefs[tab.id]?.parentRDocId) ?? null"
            :step-states="getWorkflowViewState(tab.id).stepStates"
            :can-next-action="getWorkflowViewState(tab.id).canNextAction"
            :return-targets="getReturnTargets(tab.id)"
            @sequence-updated="docHeaderRefs[tab.id]?.fetchDoc?.(tab.id)"
            @next-action="onProceedNextStep(tab.id)"
            @time-machine="onWorkflowStepTimeMachine(tab.id, $event)"
            @return-to="onWorkflowStepReturn(tab.id, $event)"
          />
          <!-- 0115: git finalize panel — self-hiding unless the group has a git
               worktree (status !== 'none'); shown on workflow roots only. -->
          <GitFinalizePanel
            v-if="tab.typeCode === 'R' || tab.typeCode === 'B'"
            :group-id="exposedValue(docHeaderRefs[tab.id]?.groupId) ?? ''"
          />
          <!-- AC (final approval): file-less workflow step — no body file, so it
               must render by typeCode regardless of tab.type. When reopened from
               the tree the tab type resolves to 'unsupported' (no md), which would
               otherwise fall through to the unsupported-view. -->
          <div v-if="tab.typeCode === 'AC'" class="card md-preview-card">
            <div class="card-hd">
              <span class="card-title">
                <i class="fa-solid fa-clipboard-check" style="color:var(--text-m);"></i>
                {{ t('main.review_action_bar.final_approval') }}
              </span>
            </div>
            <div class="card-bd ac-final-approval-body">
              <template v-if="isCompletedDoc(tab.id)">
                <i class="fa-solid fa-circle-check ac-fa-icon ac-fa-icon-done"></i>
                <p class="ac-fa-title">{{ t('main.final_approval.panel_title_done') }}</p>
                <p class="ac-fa-desc">{{ t('main.final_approval.panel_desc_done') }}</p>
              </template>
              <template v-else>
                <i class="fa-solid fa-stamp ac-fa-icon"></i>
                <p class="ac-fa-title">{{ t('main.final_approval.panel_title') }}</p>
                <p class="ac-fa-desc">{{ t('main.final_approval.panel_desc') }}</p>
              </template>
            </div>
          </div>
          <!-- DC (group discard): file-less terminal record. Like AC it has no .md
               body, so it must render by typeCode (otherwise the tab type resolves to
               'unsupported' and shows the bogus "preview not supported" error —
               TR0029.0008 review r2 #2). It is terminal, not a review step: no action
               bar, no workflow strip, no info panel (review r2 #3, #4). -->
          <div v-else-if="tab.typeCode === 'DC'" class="card md-preview-card">
            <div class="card-hd">
              <span class="card-title">
                <i class="fa-solid fa-ban" style="color:var(--danger, #dc2626);"></i>
                {{ t('main.group_discard.panel_title') }}
              </span>
            </div>
            <div class="card-bd ac-final-approval-body">
              <i class="fa-solid fa-circle-xmark ac-fa-icon" style="color:var(--danger, #dc2626);"></i>
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
            </div>
            <div class="card-bd conv-card-bd">
              <ConversationView
                :doc-id="tab.id"
                :project-id="tab.projectId ?? null"
                @copy-mention="(opts) => onConversationCopyMention(tab.id, opts)"
              />
            </div>
          </div>
          <div v-else-if="tab.type === 'qtui'" class="card md-preview-card">
            <div class="card-hd">
              <span class="card-title">
                <span class="doc-tag c-Q" style="font-size:.68rem; padding:2px 5px; margin-right:4px;">Q</span>
                {{ tab.title }}
              </span>
            </div>
            <div class="card-bd" style="padding:16px;">
              <QTDetailViewer :q-id="tab.id" @status-changed="onQStatusChanged" />
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
                <QTDetailViewer :q-id="tab.id" @status-changed="onQStatusChanged" />
              </div>
            </template>

            <!-- Regular document: MDViewer -->
            <template v-else>
            <div class="card-hd">
              <span class="card-title">
                <i class="fa-brands fa-markdown" style="color:var(--text-m);"></i>
                {{ t('main.document_preview.title') }}
              </span>
              <div class="card-actions">
                <div v-if="canEditDoc(tab.id)" class="edit-dropdown-wrap">
                  <button
                    class="btn btn-outline btn-sm"
                    type="button"
                    @click.stop="toggleEditDropdown(tab.id)"
                  >
                    <i class="fa-solid fa-pen"></i> {{ t('main.document_preview.edit') }}
                    <i class="fa-solid fa-caret-down edit-caret"></i>
                  </button>
                  <transition name="edit-dropdown">
                    <div v-if="editDropdownTabId === tab.id" class="edit-dropdown-menu" @click.stop>
                      <button class="edit-dropdown-item" type="button" @click="onEditDirect(tab)">
                        <i class="fa-solid fa-pen-to-square"></i> {{ t('main.main_panel.edit_direct') }}
                      </button>
                      <button v-if="tab.typeCode" class="edit-dropdown-item" type="button" @click="onEditMentCopy(tab)">
                        <i class="fa-regular fa-copy"></i> {{ t('main.main_panel.copy_mention') }}
                      </button>
                      <button v-if="tab.typeCode" class="edit-dropdown-item" type="button" @click="onEditInvokeCommand(tab)">
                        <i class="fa-solid fa-terminal"></i> {{ t('main.main_panel.invoke_command') }}
                      </button>
                    </div>
                  </transition>
                </div>
                <button class="btn btn-secondary btn-sm" type="button" @click="openFullView(tab)">
                  <i class="fa-solid fa-expand"></i> {{ t('main.document_preview.full_view') }}
                </button>
              </div>
            </div>
            <div class="card-bd">
              <MdViewer
                :ref="(el) => bindActiveRef(mdViewerRefs, tab.id, el)"
                :path="tab.mdPath ?? tab.path"
                :doc-id="tab.typeCode ? tab.id : null"
                :project-id="tab.projectId ?? null"
              />
            </div>
            </template>
          </div>
          <div v-else-if="tab.type === 'text'" class="card text-preview-card">
            <div class="card-hd">
              <span class="card-title">
                <i class="fa-regular fa-file-lines" style="color:var(--text-m);"></i>
                {{ t('main.document_preview.text_title') }}
              </span>
              <div class="card-actions">
                <label class="text-wrap-toggle">
                  <input v-model="textWrapEnabled" type="checkbox" />
                  <span>{{ t('main.document_preview.wrap_lines') }}</span>
                </label>
                <button class="btn btn-outline btn-sm" type="button" @click="onEditDirect(tab)">
                  <i class="fa-solid fa-pen"></i> {{ t('main.document_preview.edit') }}
                </button>
                <button class="btn btn-secondary btn-sm" type="button" @click="openFullView(tab)">
                  <i class="fa-solid fa-expand"></i> {{ t('main.document_preview.full_view') }}
                </button>
              </div>
            </div>
            <div class="card-bd text-preview-body">
              <TextViewer
                :ref="(el) => bindActiveRef(textViewerRefs, tab.id, el)"
                :path="tab.path"
                :project-id="tab.projectId ?? null"
                :wrap-lines="textWrapEnabled"
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
          </div><!-- doc-main -->
          <DocInfoPanel
            v-if="canShowDocInfoPanel(tab.id)"
            :doc-id="tab.id"
            :type-code="getTabTypeCode(tab.id)"
            :review-status="exposedValue(docHeaderRefs[tab.id]?.docReviewStatus) ?? null"
            :reject-reason="exposedValue(docHeaderRefs[tab.id]?.rejectionReason) ?? null"
            :rejection-history="exposedValue(docHeaderRefs[tab.id]?.rejectionHistory) ?? []"
            :ai-review="exposedValue(docHeaderRefs[tab.id]?.aiReview) ?? null"
            :ai-review-history="exposedValue(docHeaderRefs[tab.id]?.aiReviewHistory) ?? []"
            :q-status="qStatuses[tab.id] ?? null"
            :workflow-steps="exposedValue(docHeaderRefs[tab.id]?.workflowSteps) ?? null"
            :self-index="exposedValue(docHeaderRefs[tab.id]?.workflowSelfIndex) ?? null"
            :step-states="getWorkflowViewState(tab.id).stepStates"
            :next-step-index="getWorkflowViewState(tab.id).nextStepIndex"
            :collapsed="docInfoCollapsed"
            @toggle="docInfoCollapsed = !docInfoCollapsed"
            @next-action="onProceedNextStep(tab.id)"
            @open-review-history="openReviewHistory(tab.id)"
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
            <i
              class="fa-solid fa-rotate"
              :class="{ 'fa-spin': dashboardEntry?.refreshing || dashboardEntry?.initialLoading }"
            ></i>
            <span>{{ t('main.overview.refresh') }}</span>
          </button>
        </div>

        <!-- Guide card -->
        <div v-if="!guideDismissed" class="guide-card">
          <div class="guide-card-hd">
            <span class="guide-card-title">
              <i class="fa-solid fa-rocket"></i> {{ t('main.overview.guide_title') }}
            </span>
            <button class="guide-dismiss" :aria-label="t('common.close')" @click="dismissGuide">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
          <p class="guide-desc">{{ t('main.overview.guide_desc') }}</p>
          <div class="guide-flow">
            <div class="guide-step gs-start">
              <span class="gs-tag" style="background:#2563eb;">R</span>
              {{ t('main.overview.step_req') }}
              <span class="gs-hint">{{ t('main.main_panel.text_48') }}</span>
            </div>
            <span class="guide-arr"><i class="fa-solid fa-chevron-right"></i></span>
            <div class="guide-step"><span class="gs-tag" style="background:#7c3aed;">DS</span> {{ t('main.overview.step_ds') }}</div>
            <span class="guide-arr"><i class="fa-solid fa-chevron-right"></i></span>
            <div class="guide-step"><span class="gs-tag" style="background:#ea580c;">D</span> {{ t('main.overview.step_d') }}</div>
            <span class="guide-arr"><i class="fa-solid fa-chevron-right"></i></span>
            <div class="guide-step"><span class="gs-tag" style="background:#0891b2;">T</span> {{ t('main.overview.step_t') }}</div>
            <span class="guide-arr"><i class="fa-solid fa-chevron-right"></i></span>
            <div class="guide-step"><span class="gs-tag" style="background:#0284c7;">TR</span> {{ t('main.overview.step_tr') }}</div>
            <span class="guide-arr"><i class="fa-solid fa-chevron-right"></i></span>
            <div class="guide-step"><span class="gs-tag" style="background:#16a34a;">AC</span> {{ t('main.overview.step_ac') }}</div>
          </div>
          <div class="guide-actions">
            <button class="btn btn-primary btn-sm" @click="$emit('create-requirement')">
              <i class="fa-solid fa-plus"></i> {{ t('main.overview.guide_cta') }}
            </button>
            <span class="guide-kbdhint"><kbd class="kbd">Alt</kbd>+<kbd class="kbd">N</kbd> {{ t('main.overview.guide_kbd') }}</span>
            <button class="btn btn-ghost btn-sm" style="color:var(--text-m); font-size:.75rem; margin-left:auto;" @click="dismissGuide">
              <i class="fa-solid fa-eye-slash"></i> {{ t('main.overview.guide_dismiss') }}
            </button>
          </div>
        </div>

        <!-- Reopened guide indicator (when dismissed) -->
        <div v-else-if="projectStore.currentProjectId" class="guide-reopen">
          <button class="btn btn-outline btn-sm" @click="reopenGuide">
            <i class="fa-solid fa-eye"></i> {{ t('main.overview.guide_reopen') }}
          </button>
        </div>

        <!-- Stats grid -->
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-icon" style="background:#dcfce7;"><i class="fa-solid fa-diagram-project ic-green"></i></div>
            <div>
              <div class="stat-num" style="color:var(--success);">{{ activeProjects }}</div>
              <div class="stat-lbl">{{ t('main.overview.active_projects') }}</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#dbeafe;"><i class="fa-solid fa-file-lines ic-blue"></i></div>
            <div>
              <div class="stat-num" style="color:var(--primary);">{{ totalDocs }}</div>
              <div class="stat-lbl">{{ t('main.overview.total_docs') }}</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#ede9fe;"><i class="fa-solid fa-bars-progress" style="color:#7c3aed;"></i></div>
            <div>
              <div class="stat-num" style="color:#7c3aed;">{{ inProgressWorkflows }}</div>
              <div class="stat-lbl">{{ t('main.overview.wf_in_progress') }}</div>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#fef3c7;"><i class="fa-solid fa-gears ic-yellow"></i></div>
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
                <i class="fa-solid fa-spinner fa-spin"></i>
                <p>{{ t('main.overview.loading') }}</p>
              </div>
              <div v-else-if="dashboardEntry?.error && !dashboardEntry.data" class="empty">
                <i class="fa-solid fa-triangle-exclamation"></i>
                <p>{{ t('main.overview.load_failed') }}</p>
                <button class="btn btn-outline btn-sm" type="button" @click="dashboardStore.retryCurrent">
                  {{ t('main.overview.retry') }}
                </button>
              </div>
              <div v-else-if="recentActivities.length === 0" class="empty">
                <i class="fa-regular fa-clock"></i>
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
                      <i class="fa-regular fa-folder activity-group-icon"></i>
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
                  <i class="fa-solid fa-chevron-up"></i> {{ t('main.overview.collapse') }}
                </template>
                <template v-else>
                  <i class="fa-solid fa-chevron-down"></i>
                  {{ t('main.overview.view_all', { total: allRecentActivities.length }) }}
                </template>
              </button>
              <div v-if="dashboardEntry?.error && dashboardEntry.data" class="dashboard-inline-error">
                {{ t('main.overview.load_failed') }}
                <button type="button" @click="dashboardStore.retryCurrent">{{ t('main.overview.retry') }}</button>
              </div>
            </div>
          </div>

          <!-- Right column: Workflow + Type Distribution -->
          <div class="right-col">
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
                  <i class="fa-solid fa-spinner fa-spin"></i>
                  <p>{{ t('main.overview.loading') }}</p>
                </div>
                <div v-else-if="dashboardEntry?.error && !dashboardEntry.data" class="empty">
                  <i class="fa-solid fa-triangle-exclamation"></i>
                  <p>{{ t('main.overview.load_failed') }}</p>
                  <button class="btn btn-outline btn-sm" type="button" @click="dashboardStore.retryCurrent">
                    {{ t('main.overview.retry') }}
                  </button>
                </div>
                <div v-else-if="activeWorkflows.length === 0" class="empty">
                  <i class="fa-solid fa-diagram-project"></i>
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
                          <i class="fa-solid fa-chevron-right"></i>
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
                    <i class="fa-solid fa-chevron-up"></i> {{ t('main.overview.collapse') }}
                  </template>
                  <template v-else>
                    <i class="fa-solid fa-chevron-down"></i>
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
                  <i class="fa-solid fa-chart-pie"></i>
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
                      <i class="fa-solid fa-chevron-left"></i>
                    </button>
                    <span class="text-xs text-m">{{ t('main.overview.dist_page', { current: distPage + 1, total: distPageCount }) }}</span>
                    <button
                      type="button"
                      class="dist-pager-btn"
                      :disabled="distPage >= distPageCount - 1"
                      :aria-label="t('main.overview.dist_next')"
                      @click="distPage++"
                    >
                      <i class="fa-solid fa-chevron-right"></i>
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
                  <i class="fa-solid fa-circle-question"></i>
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
                      <i class="fa-solid fa-chevron-up"></i> {{ t('main.overview.collapse') }}
                    </template>
                    <template v-else>
                      <i class="fa-solid fa-chevron-down"></i>
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

    <ReviewActionBar
      v-if="activeTabId != null && activeTab && getActionBarMode(activeTabId) != null"
      :mode="getActionBarMode(activeTabId)!"
      :doc-id="activeTabId"
      :project-id="exposedValue(docHeaderRefs[activeTabId]?.docProjectId) ?? ''"
      :group-id="exposedValue(docHeaderRefs[activeTabId]?.groupId) ?? ''"
      :doc-ref="activeTabId"
      :doc-title="activeTab.title"
      :review-status="exposedValue(docHeaderRefs[activeTabId]?.docReviewStatus) ?? null"
      :ai-review-arrived="!!exposedValue(docHeaderRefs[activeTabId]?.aiReview)"
      :doc-type="getTabTypeCode(activeTabId) ?? undefined"
      :next-step-label="getNextStepLabel(activeTabId)"
      :next-step-code="getWorkflowViewState(activeTabId).nextStepCode ?? undefined"
      :review-request-label="getReviewRequestLabel(activeTabId)"
      :can-next-action="getWorkflowViewState(activeTabId).canNextAction"
      :head-doc-id="exposedValue(docHeaderRefs[activeTabId]?.headDocId) ?? null"
      :head-doc-label="getWorkflowViewState(activeTabId).headDocLabel"
      :head-doc-title="exposedValue(docHeaderRefs[activeTabId]?.headDocTitle) ?? null"
      :viewed-doc-id="activeTabId"
      @approve="onReviewApproved(activeTabId, $event)"
      @reject="onReviewRejected(activeTabId)"
      @revision-complete="onReviewApproved(activeTabId, $event)"
      @open-mention-dialog="onReviewOpenMentionDialog"
      @copy-rework-mention="onReviewReworkCopyMention"
      @invoke-command="onReviewInvokeCommand"
      @decide-workflow="openWorkflowDecisionForActive"
      @copy-workflow-mention="onWorkflowDecisionCopyMention"
      @invoke-workflow-command="onWorkflowDecisionInvokeCommand"
      @next-action="onProceedNextStep(activeTabId)"
      @copy-next-mention="onActionBarCopyNextMention(activeTabId)"
      @create-empty="onActionBarCreateEmpty(activeTabId)"
      @create-approved="onActionBarCreateApproved(activeTabId)"
      @create-conversation="onActionBarCreateConversation(activeTabId)"
      @continuous-work="onActionBarContinuousWork(activeTabId)"
      @open-head-doc="onOpenHeadDocClick"
    />

    <!-- Document Full View Modal -->
    <teleport to="body">
      <div v-if="fullViewVisible && fullViewTab" class="modal-bg" @keydown.escape="closeFullView">
        <div class="modal-box document-modal">
          <div class="modal-hd">
            <span class="modal-title">
              <i :class="fullViewTab.type === 'text' ? 'fa-regular fa-file-lines' : 'fa-brands fa-markdown'" style="color:var(--text-m);"></i>
              {{ fullViewTab.title }}
            </span>
            <div class="modal-hd-actions">
              <button
                class="btn btn-outline btn-sm"
                type="button"
                @click="editFromFullView(fullViewTab)"
              >
                <i class="fa-solid fa-pen"></i> {{ t('main.document_preview.edit') }}
              </button>
              <button class="modal-close" type="button" @click="closeFullView">
                <i class="fa-solid fa-xmark"></i>
              </button>
            </div>
          </div>
          <div class="modal-bd document-modal__body">
            <TextViewer
              v-if="fullViewTab.type === 'text'"
              :path="fullViewTab.path"
              :project-id="fullViewTab.projectId ?? null"
              :wrap-lines="textWrapEnabled"
            />
            <MdViewer
              v-else
              :path="fullViewTab.mdPath ?? fullViewTab.path"
              :doc-id="fullViewTab.typeCode ? fullViewTab.id : null"
              :project-id="fullViewTab.projectId ?? null"
            />
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
              <i class="fa-solid fa-pen" style="color:var(--primary);"></i>
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
                <i class="fa-solid" :class="headerEditModeVisible ? 'fa-eye' : 'fa-eye-slash'"></i>
                {{ headerEditModeVisible ? t('main.main_panel.header_hide') : t('main.main_panel.header_edit') }}
              </button>
              <button class="modal-close" type="button" :disabled="editSaving" @click="closeEditModal">
                <i class="fa-solid fa-xmark"></i>
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
              <i class="fa-solid fa-floppy-disk"></i>
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
      @copy-mention="onNextActionCopyMention"
      @copy-mention-with-message="onNextActionCopyMentionWithMessage"
      @create-empty="onNextActionCreateEmpty"
    />

    <!-- Continuous (unmanned) work — sequence pick → warning/consent → token + mention copy -->
    <ContinuousWorkDialog
      v-model:visible="continuousDialogVisible"
      :doc-ref="continuousDocRef"
      @confirm="onContinuousDialogConfirm"
    />
    <ContinuousWarningDialog
      v-model:visible="continuousWarnVisible"
      :step-count="continuousStepCount"
      :target-label="continuousTargetLabel"
      :review-mode="continuousReviewMode"
      :from-decision="continuousFromDecision"
      @confirm="onContinuousWarnConfirm"
    />

    <MentionMessageDialog
      :visible="mmDialogVisible"
      :project-id="mmDialogProjectId"
      :doc-type="mmDialogDocType"
      :doc-types="mmDialogDocTypes"
      :candidates="mmDialogCandidates"
      @select="onMmDialogSelect"
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
    />

    <!-- Review Reject Dialog -->
    <ReviewRejectDialog
      ref="rejectDialogRef"
      :visible="rejectDialogVisible"
      :doc-id="rejectDialogDocId"
      :doc-name="rejectDialogDocName"
      :doc-type="tabs.find(t => t.id === rejectDialogTabId)?.typeCode ?? null"
      :existing-reason="rejectDialogExistingReason"
      @save-reason="onRejectDialogSaveReason"
      @copy-mention="onRejectDialogCopyMention"
      @update:visible="(v: boolean) => { rejectDialogVisible = v; if (!v) onRejectDialogClosed() }"
    />

    <!-- Time Machine Dialog (AC reject → reopen at an earlier step) -->
    <TimeMachineDialog
      :visible="timeMachineVisible"
      :steps="timeMachineSteps"
      :loading="timeMachineLoading"
      :preselect-doc-id="timeMachinePreselectDocId"
      @confirm="onTimeMachineConfirm"
      @update:visible="(v: boolean) => { timeMachineVisible = v }"
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

    <ReviewHistoryDialog
      :visible="reviewHistoryVisible"
      :reviews="reviewHistoryReviews"
      :rejections="reviewHistoryRejections"
      @update:visible="(v: boolean) => { reviewHistoryVisible = v }"
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

    <!-- Quick Open Dialog -->
    <div v-if="showQuickOpen" class="modal-overlay" @click.self="showQuickOpen = false">
      <div class="modal" style="max-width:480px;">
        <div class="modal-hd">
          <span>{{ t('main.quick_open.placeholder') }}</span>
          <button class="modal-close" @click="showQuickOpen = false"><i class="fa-solid fa-xmark"></i></button>
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
import { useTabsStore, type Tab } from '../stores/tabs'
import { useProjectStore } from '../stores/project'
import { useExplorerStore } from '../stores/explorer'
import {
  useDashboardStore,
  type DashboardWorkflow,
} from '../stores/dashboard'
import { useShortcuts } from '../composables/useShortcuts'
import { useDashboardNavigation } from '../composables/useDashboardNavigation'
import { useActivityFormat } from '../composables/useActivityFormat'
import { useToast } from './common/useToast'
import { useDocTypeStore } from '../stores/docTypeStore'
import { resolveWorkflowViewState, type WorkflowViewInput, type WorkflowViewState } from '../workflow/workflowViewState'
import { resolveClickedSlot, isRollbackTarget, returnTargetIndices, type SequenceSlot } from '../workflow/timeMachineSlot'
import { useFlowGateToken, splitGroupId, buildConversationMention, type RejectionHistoryItem, type RejectionContext } from '../composables/useFlowGateToken'
import { useMentionCopy, type MentionKind } from '../composables/useMentionCopy'
import TabBar from './TabBar.vue'
import TextViewer from './TextViewer.vue'
import MdViewer from './MdViewer.vue'
import DocHeader from './DocHeader.vue'
import TestFailStrip from './TestFailStrip.vue'
import DocWorkflow from './DocWorkflow.vue'
import GitFinalizePanel from './GitFinalizePanel.vue'
import ReviewActionBar from './ReviewActionBar.vue'
import ReviewRejectDialog from './ReviewRejectDialog.vue'
import TimeMachineDialog from './TimeMachineDialog.vue'
import ReviewHistoryDialog from './ReviewHistoryDialog.vue'
import DesignHandoffDialog from './DesignHandoffDialog.vue'
import type { AiReview } from '../types/aiReview'
import NextActionModal from './NextActionModal.vue'
import ContinuousWorkDialog from './ContinuousWorkDialog.vue'
import ContinuousWarningDialog from './ContinuousWarningDialog.vue'
import MentionMessageDialog from './MentionMessageDialog.vue'
import { buildCandidateList, type MessageEntry } from '../utils/mentionMessages'
import { copyToClipboard, copyToClipboardDeferred, ClipboardAbort } from '../utils/clipboard'
import type { IssuedToken } from '../composables/useFlowGateToken'
import NextEmptyDocModal from './NextEmptyDocModal.vue'
import ConfirmModal from './ConfirmModal.vue'
import CommandSelectorModal from './CommandSelectorModal.vue'
import QTDetailViewer from './QTDetailViewer.vue'
import DocInfoPanel from './DocInfoPanel.vue'
import ConversationView from './ConversationView.vue'

const { t, locale } = useI18n()
const props = withDefaults(defineProps<{
  overviewRefreshToken?: number
}>(), {
  overviewRefreshToken: 0,
})
const docTypeStore = useDocTypeStore()
const { showToast } = useToast()
const {
  issueToken,
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
const dashboardStore = useDashboardStore()
const dashboardEntry = computed(() => dashboardStore.currentEntry)

const tabs = computed(() => tabsStore.tabs)
const activeTabId = computed(() => tabsStore.activeTabId)
const activeTab = computed(() => tabsStore.activeTab)

// Template refs for DocHeader instances (keyed by tab id).
const docHeaderRefs = reactive<Record<string, any>>({})
const mdViewerRefs = reactive<Record<string, any>>({})
const textViewerRefs = reactive<Record<string, any>>({})
const qStatuses = reactive<Record<string, string>>({})
const headerRevision = ref(0)

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
const editVisible = ref(false)
const editTab = ref<Tab | null>(null)
const editContent = ref('')
const editFrontmatter = ref('')
const editBody = ref('')
const editLoading = ref(false)
const editSaving = ref(false)
const editError = ref('')
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
// Continuous (unmanned) work (R0001 group 0086): sequence-pick dialog → warning/consent gate
// → issue the first continuation token via /workflow/advance and copy its continuous mention.
const continuousDialogVisible = ref(false)
const continuousWarnVisible = ref(false)
const continuousTabId = ref('')
const continuousDocRef = ref('')
const continuousProjectId = ref('')
const continuousGroupId = ref('')
const continuousTargetSeq = ref<number | null>(null)
const continuousTargetLabel = ref('')
const continuousReviewMode = ref(false)
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
// Full AI review/rejection history modal (variant C), opened from "view full history" in the right panel.
const reviewHistoryVisible = ref(false)
const reviewHistoryReviews = ref<AiReview[]>([])
const reviewHistoryRejections = ref<RejectionHistoryItem[]>([])
function openReviewHistory(tabId: string) {
  reviewHistoryReviews.value = exposedValue(docHeaderRefs[tabId]?.aiReviewHistory) ?? []
  reviewHistoryRejections.value = exposedValue(docHeaderRefs[tabId]?.rejectionHistory) ?? []
  reviewHistoryVisible.value = true
}

const timeMachineVisible = ref(false)
const timeMachineDocId = ref('')
const timeMachineSteps = ref<TimeMachineStep[]>([])
const timeMachineLoading = ref(false)
// 0018 R0001 — strip-click time-machine pre-selects the clicked step's doc in the picker.
const timeMachinePreselectDocId = ref<string | null>(null)
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
