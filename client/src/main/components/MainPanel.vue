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
            @sequence-updated="docHeaderRefs[tab.id]?.fetchDoc?.(tab.id)"
            @next-action="onProceedNextStep(tab.id)"
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
      @confirm="onTimeMachineConfirm"
      @update:visible="(v: boolean) => { timeMachineVisible = v }"
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
import { useFlowGateToken, splitGroupId, buildConversationMention, type RejectionHistoryItem, type RejectionContext } from '../composables/useFlowGateToken'
import { useMentionCopy } from '../composables/useMentionCopy'
import TabBar from './TabBar.vue'
import TextViewer from './TextViewer.vue'
import MdViewer from './MdViewer.vue'
import DocHeader from './DocHeader.vue'
import DocWorkflow from './DocWorkflow.vue'
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
import { buildCandidateList, prependMessagesSection, type MessageEntry } from '../utils/mentionMessages'
import type { IssuedToken } from '../composables/useFlowGateToken'
import NextEmptyDocModal from './NextEmptyDocModal.vue'
import ConfirmModal from './ConfirmModal.vue'
import CommandSelectorModal from './CommandSelectorModal.vue'
import QTDetailViewer from './QTDetailViewer.vue'
import DocInfoPanel from './DocInfoPanel.vue'
import ConversationView from './ConversationView.vue'

const { t, locale } = useI18n()
const docTypeStore = useDocTypeStore()
const { showToast } = useToast()
const {
  issueToken,
  requestReview,
  requestWorkflowDecision,
  copyMentToClipboard,
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
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? (groupId as string),
    doc_ref: tab.id,
    action_scope: 'edit',
  })
  if (!token) return
  if (token.mention) {
    await doClipboardCopy(token.mention)
    showToast(t('main.main_panel.toast_mention_copied'), 'success')
    void recordMentionCopy(tab.id, 'edit')
    return
  }
  const ok = await copyMentToClipboard(token)
  if (ok) {
    showToast(t('main.main_panel.toast_mention_copied'), 'success')
    void recordMentionCopy(tab.id, 'edit')
  } else showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
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
  timeMachineLoading.value = true
  timeMachineVisible.value = true
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

// Reopen the workflow at the chosen step: every step doc with seq >= target_seq
// is reset to pending_review (docs preserved), the AC doc is deleted, and R
// returns to wf_in_progress. Jump to the rolled-back step so the user can revise.
async function onTimeMachineConfirm(payload: TimeMachineStep) {
  const acDocId = timeMachineDocId.value
  if (!acDocId) return
  try {
    await postRequest(`/api/v1/documents/workflow/reopen`, {
      doc_id: acDocId,
      target_seq: payload.seq,
    })
    timeMachineVisible.value = false
    // The AC doc is deleted by reopen — close its (now stale) tab.
    tabsStore.closeTab(acDocId)
    for (const tid of Object.keys(docHeaderRefs)) docHeaderRefs[tid]?.fetchDoc?.(tid)
    tabsStore.openTab({
      id: payload.docId,
      title: payload.title ? `${payload.docId} — ${payload.title}` : payload.docId,
      path: '',
      type: 'md',
      typeCode: payload.typeCode ?? undefined,
    })
    showToast(t('main.time_machine.toast_reopened'), 'success')
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(detail, 'danger')
  }
}

async function onRejectDialogSaveReason(reason: string) {
  const tabId = rejectDialogTabId.value
  if (!tabId) return
  try {
    const res = await postRequest<any>(
      `/api/v1/documents/review_transitions/reject`,
      { doc_id: tabId, comment: reason },
    )
    // Gap D (NR0003 §2/§6 item 2): optimistic flip to the confirmed 'rejected' status +
    // silent retrying backfill, not the bare non-silent fetchDoc that could blank the
    // header on a slow idle-window GET and leave only this toast showing.
    const updated = (res.data as any)?.document ?? (res.data as any)?.data ?? res.data
    await docHeaderRefs[tabId]?.applyReviewTransition?.(updated?.doc_review_status ?? 'rejected')
    showToast(t('main.main_panel.toast_rejected'), 'danger')
    rejectDialogRef.value?.notifySaved()
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(t('main.main_panel.toast_reject_failed', { detail }), 'danger')
    rejectDialogRef.value?.notifySaveFailed()
    throw e
  }
}

async function onRejectDialogCopyMention(reason: string) {
  const docName = rejectDialogDocName.value
  const text = t('main.main_panel.reject_mention_template', { docName, reason })
  await doClipboardCopy(text)
  showToast(t('main.main_panel.toast_reject_mention_copied'), 'success')
  void recordMentionCopy(rejectDialogTabId.value, 'reject')
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
  const token = await requestWorkflowDecision(payload.docId)
  if (!token) return
  const ok = await copyMentToClipboard(token)
  if (ok) {
    showToast(t('main.main_panel.toast_mention_copied'), 'success')
    void recordMentionCopy(payload.docId, 'workflow_decision')
  } else showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
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
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? groupId,
    doc_ref: payload.docRef,
    action_scope: 'edit',
  })
  if (!token) return
  const h = docHeaderRefs[payload.docId]
  const history: RejectionHistoryItem[] = exposedValue<RejectionHistoryItem[]>(h?.rejectionHistory) ?? []
  const lastReason = exposedValue<string>(h?.rejectionReason) ?? null
  const rejectionContext: RejectionContext | undefined =
    history.length > 0 || lastReason
      ? { history, last: lastReason }
      : undefined
  const ok = await copyMentToClipboard(token, undefined, rejectionContext)
  if (ok) {
    showToast(t('main.main_panel.toast_mention_copied'), 'success')
    void recordMentionCopy(payload.docId, 'rework')
  } else showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
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
    try {
      const res = await getRequest<any>(`/api/v1/documents/prompt?doc_id=${encodeURIComponent(payload.docId)}`)
      const text = res?.data?.prompt_text ?? ''
      if (text) {
        await doClipboardCopy(text)
        showToast(t('main.main_panel.toast_vr_mention_copied'), 'success')
        void recordMentionCopy(payload.docId, 'vr_correction')
      } else {
        showToast(t('main.main_panel.error_vr_mention_failed'), 'warning')
      }
    } catch {
      showToast(t('main.main_panel.error_vr_mention_fetch_failed'), 'danger')
    }
    return
  }

  // Review request (pre-approval): the document itself needs review, NOT a next-step handoff.
  // Copy a review-request mention (read the doc → evaluate → submit a verdict via inbox
  // action:review). This must take priority over the design-handoff / advance branches
  // below, which are CREATE-NEXT handoffs valid only once the doc is approved.
  const reviewStatus = exposedValue<string | null>(docHeaderRefs[payload.docId]?.docReviewStatus)
  if (reviewStatus == null || reviewStatus === 'pending_review' || reviewStatus === 'revised') {
    const token = await requestReview({ doc_id: payload.docId })
    if (!token) return
    if (token.mention) {
      await doClipboardCopy(token.mention)
      showToast(t('main.main_panel.toast_mention_copied'), 'success')
      void recordMentionCopy(payload.docId, 'review')
    } else {
      showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
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
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? groupId,
    doc_ref: payload.docRef,
    action_scope: 'new',
  })
  if (!token) return
  if (token.mention) {
    await doClipboardCopy(token.mention)
    showToast(t('main.main_panel.toast_mention_copied'), 'success')
    void recordMentionCopy(payload.docId, 'next_step')
    return
  }
  const ok = await copyMentToClipboard(token)
  if (ok) {
    showToast(t('main.main_panel.toast_mention_copied'), 'success')
    void recordMentionCopy(payload.docId, 'next_step')
  } else showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
}

function onDesignHandoffCopyMention(_payload: { types: string[]; mode: string }) {
  showToast(t('main.main_panel.toast_mention_copied'), 'success')
  void recordMentionCopy(designHandoffDocId.value, 'design_handoff')
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
  nextActionModalVisible.value = true
}

async function onNextActionInvokeCommand(_selectedDocs?: string[]) {
  const tabId = nextActionModalTabId.value
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
    selected_docs: selectedDocs,
  })
  if (!token) return
  if (token.mention) {
    await doClipboardCopy(token.mention)
    showToast(t('main.next_action_modal.copy_mention_toast'), 'success')
    void recordMentionCopy(tabId, 'next_step')
    return
  }
  const ok = await copyMentToClipboard(token, selectedDocs)
  if (ok) {
    showToast(t('main.next_action_modal.copy_mention_toast'), 'success')
    void recordMentionCopy(tabId, 'next_step')
  } else showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
}

// Copy a token's mention (optionally prepending chosen project message(s)). Mirrors the
// copy logic in onNextActionCopyMention; returns whether the clipboard write succeeded.
async function copyTokenMention(token: IssuedToken, selectedDocs?: string[], appendMessages?: string[]): Promise<boolean> {
  if (token.mention) {
    const text = appendMessages && appendMessages.length > 0
      ? prependMessagesSection(token.mention, appendMessages, t('main.next_action_modal.mm_section_header'))
      : token.mention
    await doClipboardCopy(text)
    return true
  }
  return copyMentToClipboard(token, selectedDocs, undefined, appendMessages)
}

// [Copy mention (add message)] — R0001 group 0004 / L0007 §2.2.
// Fetch candidates first (gating). On error: toast, copy nothing. Empty: fall back to a
// plain mention copy + fallback toast. Otherwise: open the dialog to pick a message.
async function onNextActionCopyMentionWithMessage(selectedDocs?: string[]) {
  const tabId = nextActionModalTabId.value
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
    } else showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
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
    showToast(t('main.next_action_modal.copy_mention_toast'), 'success')
    void recordMentionCopy(nextActionModalTabId.value, 'next_step_message')
  } else showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
  mmDialogToken.value = null
}

function onMmDialogCancel() {
  mmDialogVisible.value = false
  mmDialogToken.value = null
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
  nextEmptyDocProjectId.value = project
  nextEmptyDocGroupId.value = groupId
  nextEmptyDocPrevDocId.value = docRef
  nextEmptyDocType.value = docType
  nextEmptyDocModule.value = moduleName
  const cachedNodes = explorerStore.getCachedGroupTree(project) || []
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

// ── Continuous (unmanned) work (R0001 group 0086) ──────────────────────────────
// Entry from the 'workflow'/'next' action-bar dropdowns. Opens the sequence-pick dialog;
// the dialog reads /workflow/sequence by the ROOT R (nextActionDocRef) and handles the
// undecided/all-done cases itself, so this is NOT gated by guardNextActionAvailable.
function onActionBarContinuousWork(tabId: string) {
  const h = docHeaderRefs[tabId]
  continuousTabId.value = tabId
  continuousDocRef.value = nextActionDocRef(tabId)
  continuousProjectId.value = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  continuousGroupId.value = exposedValue<string>(h?.groupId) ?? ''
  continuousDialogVisible.value = true
}

// Sequence-pick confirmed → carry the run parameters into the warning/consent gate.
function onContinuousDialogConfirm(payload: { targetSeq: number; targetLabel: string; reviewMode: boolean; stepCount: number; fromDecision: boolean }) {
  continuousTargetSeq.value = payload.targetSeq
  continuousTargetLabel.value = payload.targetLabel
  continuousReviewMode.value = payload.reviewMode
  continuousStepCount.value = payload.stepCount
  continuousFromDecision.value = payload.fromDecision
  continuousDialogVisible.value = false
  continuousWarnVisible.value = true
}

// Consent given → issue the FIRST continuation token via /workflow/advance (continuous=true)
// and copy its continuous mention. The server self-chains the remaining steps from the inbox
// response; the worker reads next_token off each 201 (no human re-issue per step).
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
  // R0001 "워크플로 결정부터": the workflow is not decided yet, so /workflow/advance would
  // 400 (sequence_not_decided). Instead issue the workflow-decision token (continuous): the
  // worker decides the sequence, and the server self-chains the rest from the decide
  // response. The decision mention carries the same delegation/unmanned block.
  if (continuousFromDecision.value) {
    const token = await requestWorkflowDecision(docRef, {
      continuous: true,
      continuationReviewMode: continuousReviewMode.value,
    })
    if (!token) return
    if (token.mention) {
      await doClipboardCopy(token.mention)
      showToast(t('main.continuous_work.toast_started'), 'success')
      void recordMentionCopy(continuousTabId.value, 'continuous')
    } else {
      showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
    }
    return
  }
  const gParts = splitGroupId(groupId)
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? groupId,
    doc_ref: docRef,
    action_scope: 'new',
    continuous: true,
    continuationTargetSeq: targetSeq,
    continuationReviewMode: continuousReviewMode.value,
  })
  if (!token) return
  if (token.mention) {
    await doClipboardCopy(token.mention)
    showToast(t('main.continuous_work.toast_started'), 'success')
    void recordMentionCopy(continuousTabId.value, 'continuous')
    return
  }
  const ok = await copyMentToClipboard(token)
  if (ok) {
    showToast(t('main.continuous_work.toast_started'), 'success')
    void recordMentionCopy(continuousTabId.value, 'continuous')
  } else showToast(t('main.main_panel.toast_clipboard_not_supported'), 'warning')
}

// R0001 #2 (0048): action-bar split "create approved doc" — confirm once, then create
// + approve in one server call (next-approved). Offered only for N/T/TS next steps;
// approve permission is enforced by the server (403 → error toast).
function onActionBarCreateApproved(tabId: string) {
  if (!guardNextActionAvailable(tabId)) return
  const code = (getNextStepCode(tabId) || '').toUpperCase()
  if (!['N', 'T', 'TS'].includes(code)) {
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
// chat. We issue an edit-scope token bound to the CH doc, but DO NOT copy the server's
// standard edit mention (token.mention): rev4 reject — "the mention should be copied in a
// chat-only form. Strip out Q info and other useless info and keep it compact". Instead we build a compact,
// chat-only mention (buildConversationMention) with just: read the conversation, append
// one AI turn (§6), submit via inbox edit. The AI reads the conversation and appends its
// reply turn, the same inbox-edit path AI turns already use.
async function onConversationCopyMention(tabId: string, opts?: { auto?: boolean }) {
  const h = docHeaderRefs[tabId]
  const project = exposedValue<string>(h?.docProjectId) ?? projectStore.currentProjectId ?? ''
  const groupId = exposedValue<string>(h?.groupId) ?? ''
  if (!project || !groupId) {
    showToast(t('main.main_panel.error_info_unavailable'), 'danger')
    return
  }
  const gParts = splitGroupId(groupId)
  const token = await issueToken({
    project,
    ...(gParts?.module != null ? { module: gParts.module } : {}),
    group: gParts?.groupCode ?? groupId,
    doc_ref: tabId,
    action_scope: 'edit',
  })
  if (!token) return
  const mention = buildConversationMention({
    rawToken: token.raw_token,
    docId: tabId,
    project,
    module: gParts?.module ?? null,
    groupName: groupId,
  })
  await doClipboardCopy(mention)
  // 0085: an auto-copy (fired by every send when the toggle is on) stays silent so it
  // doesn't spam a success toast each turn; the manual button still confirms with one.
  if (!opts?.auto) showToast(t('main.main_panel.toast_mention_copied'), 'success')
  void recordMentionCopy(tabId, 'edit')
}

async function doClipboardCopy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const el = document.createElement('textarea')
    el.value = text
    el.style.cssText = 'position:fixed;left:-9999px;top:-9999px;'
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }
}

function openFullView(tab: Tab) {
  fullViewTab.value = tab
  fullViewVisible.value = true
}
function closeFullView() {
  fullViewVisible.value = false
  fullViewTab.value = null
}
function editFromFullView(tab: Tab) {
  closeFullView()
  openEditModal(tab)
}
async function openEditModal(tab: Tab) {
  editTab.value = tab
  editVisible.value = true
  editContent.value = ''
  editError.value = ''
  editLoading.value = true
  headerEditModeVisible.value = false
  try {
    let raw = ''
    if (isDocumentTab(tab)) {
      const res = await getRequest<{ content: string }>(`/api/v1/documents/content?doc_id=${encodeURIComponent(tab.id)}`)
      raw = (res.data as any)?.content ?? ''
    } else if (tab.projectId && getTabSourcePath(tab)) {
      const url = `/api/v1/projects/${encodeURIComponent(tab.projectId)}/files/src-content?path=${encodeURIComponent(getTabSourcePath(tab))}`
      const res = await api.get<string>(url, { responseType: 'text' })
      raw = res.data ?? ''
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
  headerEditModeVisible.value = false
}
async function saveEditContent() {
  if (!editTab.value || editSaving.value) return
  editSaving.value = true
  editError.value = ''
  const content = headerEditModeVisible.value
    ? editFullContent.value
    : (editFrontmatter.value ? editFrontmatter.value + '\n' + editBody.value : editBody.value)
  try {
    if (isDocumentTab(editTab.value)) {
      await patchRequest(`/api/v1/documents/content`, {
        doc_id: editTab.value.id,
        content,
      })
    } else if (editTab.value.projectId && getTabSourcePath(editTab.value)) {
      const url = `/api/v1/projects/${encodeURIComponent(editTab.value.projectId)}/files/src-content?path=${encodeURIComponent(getTabSourcePath(editTab.value))}`
      await api.patch(url, { content })
    } else {
      throw new Error(t('main.main_panel.error_info_unavailable'))
    }
    await mdViewerRefs[editTab.value.id]?.loadContent?.()
    await textViewerRefs[editTab.value.id]?.loadContent?.()
    showToast(t('main.document_preview.save_success'), 'success')
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
  const nodes = explorerStore.getCachedGroupTree(pid)
  if (!nodes) return '—'
  return nodes.filter((n) => n.node_type === 'document').length
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
const WORKING_HEAD_TYPES = new Set(['T', 'N', 'TS'])
const isWorkingHeadType = (typeCode: string | null): boolean => {
  if (!typeCode) return false
  if (WORKING_HEAD_TYPES.has(typeCode)) return true
  return typeCode.length >= 2 && typeCode.endsWith('R') // xR series
}
const workingGroups = computed<number | string>(() => {
  const pid = projectStore.currentProjectId
  if (!pid) return '—'
  const nodes = explorerStore.getCachedGroupTree(pid)
  if (!nodes) return '—'
  // Pick each group's last document = highest doc number among the document
  // nodes sharing the same immediate parent (group / subgroup). Numbers are
  // zero-padded ("0001".."0078") so a string compare is the correct order.
  const lastDocByParent = new Map<string, (typeof nodes)[number]>()
  for (const n of nodes) {
    if (n.node_type !== 'document' || !n.parent_id) continue
    const cur = lastDocByParent.get(n.parent_id)
    if (!cur || (n.number ?? '') > (cur.number ?? '')) {
      lastDocByParent.set(n.parent_id, n)
    }
  }
  let count = 0
  for (const doc of lastDocByParent.values()) {
    if (doc.is_final_approved || doc.is_discarded) continue
    if (isWorkingHeadType(doc.type_code)) count++
  }
  return count
})

const typeDistribution = computed(() => {
  const pid = projectStore.currentProjectId
  if (!pid) return []
  const nodes = explorerStore.getCachedGroupTree(pid)
  if (!nodes) return []
  const counts: Record<string, number> = {}
  nodes.forEach((n) => {
    if (n.node_type === 'document' && n.type_code) {
      counts[n.type_code] = (counts[n.type_code] ?? 0) + 1
    }
  })
  return Object.entries(counts)
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count)
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

.modal-hd-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.document-modal {
  width: min(1180px, 94vw);
  height: min(860px, 90vh);
}

.document-modal__body {
  padding: 0;
}

.document-modal__body :deep(.md-viewer) {
  height: 100%;
}

.document-modal--edit {
  width: min(1120px, 94vw);
}

.document-editor {
  padding: 0;
  min-height: 420px;
  display: flex;
}

.document-editor__textarea {
  width: 100%;
  min-height: 62vh;
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
</style>
