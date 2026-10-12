<template>
  <section class="tr2 card" data-testid="tr2-body">
    <header class="tr2-header">
      <div class="tr2-title">
        <div class="tr2-title-row">
          <span class="doc-tag c-TR2 tr2-chip" data-testid="tr2-chip">TR2 · {{ t('main.tr2_body.chip') }}</span>
          <strong data-testid="tr2-title">{{ title }}</strong>
          <span v-if="state" class="tr2-pill" :class="`tr2-tone-${tone}`" data-testid="tr2-state-pill">● {{ t(`main.tr2_body.state.${state}`) }}</span>
        </div>
        <p class="tr2-apply-notice" data-testid="tr2-apply-notice"><span class="tr2-heading-id">{{ tab.id }}</span> · {{ t('main.tr2_body.apply_notice') }}</p>
      </div>
      <div class="tr2-actions">
        <button type="button" class="btn btn-outline btn-sm" :disabled="loading" @click="refresh"><AppIcon name="arrow-clockwise" /> {{ t('main.tr2_body.actions.refresh') }}</button>
        <button type="button" class="btn btn-outline btn-sm" :disabled="loading || !view" @click="runDiagnostic"><AppIcon name="shield" /> {{ t('main.tr2_body.actions.diagnostic') }}</button>
      </div>
    </header>

    <div v-if="error" class="tr2-error" role="alert">
      <span>{{ error }}</span>
      <button v-if="stale" type="button" class="btn btn-outline btn-sm" data-testid="tr2-reload" @click="reload">{{ t('main.tr2_body.actions.reload') }}</button>
    </div>
    <p v-if="notice" class="tr2-notice" role="status">{{ notice }}</p>
    <p v-if="loading && !view && !broken">{{ t('main.tr2_body.loading') }}</p>

    <!-- 반영안 복구 (T0030 §6): the stored proposal itself is unreadable. Never the apply rollback. -->
    <Tr2RevisionPanel v-if="broken" :key="`recovery-${recoveryKey}`" :doc-id="tab.id" mode="recovery" :read-only="readOnly" @changed="onRecovered" />

    <div v-if="confirming" class="tr2-confirm" role="alertdialog" data-testid="tr2-confirm">
      <span>{{ confirmText }}</span>
      <button type="button" class="btn btn-danger btn-sm" data-testid="tr2-confirm-yes" @click="confirmYes">{{ t('main.tr2_body.actions.confirm') }}</button>
      <button type="button" class="btn btn-outline btn-sm" @click="confirming = null">{{ t('main.tr2_body.actions.cancel') }}</button>
    </div>

    <template v-if="view && state">

      <div class="tr2-strip" :class="`tr2-tone-${tone}`" data-testid="tr2-state-strip">
        <span class="tr2-state-symbol" aria-hidden="true">{{ tone === 'safe' || tone === 'done' ? '✓' : '!' }}</span>
        <div class="tr2-state-copy">
          <strong>{{ te(`main.tr2_body.v4.status_${state}`) ? t(`main.tr2_body.v4.status_${state}`) : t(`main.tr2_body.state.${state}`) }}</strong>
          <p>{{ t(`main.tr2_body.state_desc.${state}`, { phase: phaseLabel(latest?.phase), code: '', reason: notReadyReason }) }}</p>
        </div>
        <span class="tr2-revision-badge">Revision {{ view.document.revision_no }}</span>
        <div v-if="retry?.allowed" class="tr2-retry" data-testid="tr2-retry-block">
          <span class="tr2-retry-copy">{{ retry.mode === 'recover_stale' ? t('main.tr2_body.retry.stale') : t('main.tr2_body.v4.retry_hint') }}</span>
          <button type="button" class="btn btn-primary btn-sm" data-testid="tr2-retry" :disabled="retrying || readOnly" @click="ask({ kind: 'retry' })">{{ retrying ? t('main.tr2_body.retry.running') : t('main.tr2_body.actions.retry') }}</button>
        </div>
        <span v-else-if="retryBlockedKey" class="tr2-note" data-testid="tr2-retry-blocked">{{ t(retryBlockedKey) }}</span>
      </div>

      <p v-if="lockKey" class="tr2-note" data-testid="tr2-lock">{{ t(lockKey) }}</p>
      <div v-if="revertPending" class="tr2-retry" data-testid="tr2-revert-retry-block">
        <button type="button" class="btn btn-primary btn-sm" data-testid="tr2-revert-retry" :disabled="cancelRetrying" @click="retryCommitCancel">{{ cancelRetrying ? t('main.tr2_body.revert_retry.running') : t('main.tr2_body.revert_retry.action') }}</button>
      </div>
      <p v-if="staleWhileEditing" class="tr2-error">{{ t('main.tr2_body.errors.stale_while_editing') }}</p>

      <div class="tr2-summary tr2-review-summary" data-testid="tr2-review-summary">
        <div><span class="tr2-stat-icon"><AppIcon name="file-text" /></span><span>{{ t('main.tr2_body.workspace.total') }}</span><strong>{{ items.length }}</strong></div>
        <div><span class="tr2-stat-icon"><AppIcon name="pencil-simple" /></span><span>{{ t('main.tr2_body.workspace.active') }}</span><strong>{{ counts.edit + counts.create }}</strong></div>
        <div><span class="tr2-stat-icon"><AppIcon name="clock" /></span><span>{{ t('main.tr2_body.workspace.excluded') }}</span><strong>{{ counts.deferred }}</strong></div>
      </div>

      <Tr2ImportDialog :open="importOpen" :busy="saving || sourceSaving" :has-actions="importMode === 'json' ? batchRows.length > 0 || batchLoading : sourceEditing || sourceBusy" :title="t('main.tr2_body.import.heading')" @close="closeImport">
        <p class="tr2-note">{{ t('main.tr2_body.import.hint') }}</p>
        <div class="tr2-import-tabs" role="tablist" :aria-label="t('main.tr2_body.import.heading')">
          <button type="button" role="tab" :aria-selected="importMode === 'source'" :disabled="saving || batchLoading || sourceBusy" data-testid="tr2-import-tab-source" @click="importMode = 'source'">{{ t('main.tr2_body.import.source') }}</button>
          <button type="button" role="tab" :aria-selected="importMode === 'json'" :disabled="saving || batchLoading || sourceBusy" data-testid="tr2-import-tab-json" @click="importMode = 'json'">{{ t('main.tr2_body.import.json') }}</button>
        </div>
        <div v-show="importMode === 'json'" class="tr2-import-json">
          <div class="tr2-dropzone" :class="{ 'is-dragging': jsonDragging }" data-testid="tr2-json-dropzone"
            @dragover.prevent="jsonDragging = true" @dragleave.prevent="jsonDragging = false" @drop.prevent="dropBatch">
            <strong>{{ t('main.tr2_body.import.drop') }}</strong>
            <p class="tr2-note">{{ t('main.tr2_body.import.json_hint') }}</p>
            <input ref="batchInput" type="file" multiple hidden accept="application/json,.json" data-testid="tr2-upload-batch" :disabled="locked" @change="previewBatch" />
            <button type="button" class="btn btn-primary btn-sm" :disabled="locked" data-testid="tr2-choose-json" @click="batchInput?.click()">{{ t('main.tr2_body.batch.upload') }}</button>
          </div>
      <section v-if="batchRows.length || batchLoading" class="tr2-section tr2-batch" data-testid="tr2-batch-preview" :aria-busy="batchLoading">
        <h3>{{ t('main.tr2_body.batch.heading') }}</h3>
        <details class="tr2-note"><summary>{{ t('main.tr2_body.diag.technical') }}</summary><p>{{ t('main.tr2_body.batch.hint') }}</p></details>
        <p role="status" data-testid="tr2-batch-count">{{ t('main.tr2_body.batch.count', { total: batchRows.length, valid: batchRows.filter(row => row.valid).length, selected: batchSelected.length }) }}</p>
        <p v-if="batchLoading">{{ t('main.tr2_body.batch.loading') }}</p>
        <p v-if="batchRevision !== view.document.revision_no || staleWhileEditing || stale" class="tr2-error" data-testid="tr2-batch-stale">{{ t('main.tr2_body.batch.stale') }} <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-batch-reload" :disabled="saving" @click="reload">{{ t('main.tr2_body.actions.reload') }}</button></p>
        <ul class="tr2-batch-list">
          <li v-for="(row, index) in batchRows" :key="index" :data-testid="`tr2-batch-row-${index}`">
            <label>
              <input v-model="row.selected" type="checkbox" :disabled="!row.valid || locked || stale || staleWhileEditing" :data-testid="`tr2-batch-select-${index}`" />
              <strong>{{ row.name }}</strong> · {{ row.item?.id ?? '—' }} · {{ row.item?.file ?? t('main.tr2_body.files.no_file') }}
              <span v-if="row.item"> · {{ opLabel(itemOp(row.collection ?? 'edits', row.item)) }}</span>
            </label>
            <details v-if="row.item"><summary>{{ t('main.tr2_body.batch.details') }}</summary><pre>{{ JSON.stringify(row.item, null, 2) }}</pre></details>
            <p v-if="row.problem" class="tr2-error">{{ row.problem }}</p>
            <p v-if="row.valid && sharedBatchPath(row)" class="tr2-note">{{ t('main.tr2_body.import.shared_path') }}</p>
            <span v-else-if="row.valid">{{ t('main.tr2_body.batch.valid') }}</span>
          </li>
        </ul>
        <div class="tr2-actions">
          <button type="button" class="btn btn-primary btn-sm" data-testid="tr2-batch-import" :disabled="locked || batchLoading || !batchSelected.length || batchRevision !== view.document.revision_no || stale || staleWhileEditing" @click="importBatch">{{ t('main.tr2_body.batch.import', { count: batchSelected.length }) }}</button>
          <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-batch-cancel" :disabled="saving" @click="closeImport">{{ t('main.tr2_body.actions.cancel') }}</button>
        </div>
      </section>
          <p v-if="error" class="tr2-error" role="alert">{{ error }}</p>
        </div>
        <Tr2SourceImport v-show="importMode === 'source'" ref="sourceImport" :key="tab.id" :doc-id="tab.id" :revision="view.document.revision_no"
          :disabled="readOnly || !mutationAllowed || saving || batchLoading" :stale="stale || staleWhileEditing"
          @editing="sourceEditing = $event" @busy="sourceBusy = $event" @saving="sourceSaving = $event" @changed="onSourcesSaved" @cancelled="closeImport" />
      </Tr2ImportDialog>

      <section class="tr2-review-card" data-testid="tr2-review-card">
        <header class="tr2-review-head">
          <div><h3>{{ t('main.tr2_body.files.heading') }}</h3><p class="tr2-note">{{ t('main.tr2_body.workspace.selection_hint') }}</p></div>
          <div class="tr2-actions">
            <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-add-item" :disabled="locked" @click="openItemEditor(null)"><AppIcon name="plus" /> {{ t('main.tr2_body.actions.add_item') }}</button>

            <button type="button" class="btn btn-primary btn-sm" data-testid="tr2-open-import" :disabled="locked" @click="openImport"><AppIcon name="file-text" /> {{ t('main.tr2_body.import.heading') }}</button>
          </div>
        </header>
      <div class="tr2-split">
        <section class="tr2-section tr2-file-column">
          <p v-if="!items.length">{{ t('main.tr2_body.files.none') }}</p>
          <div v-if="items.length" class="tr2-file-controls">
            <label class="tr2-search"><AppIcon name="magnifying-glass" /><input v-model="fileQuery" type="search" data-testid="tr2-file-search" :aria-label="t('main.tr2_body.workspace.search')" :placeholder="t('main.tr2_body.workspace.search')" /></label>
            <div class="tr2-filters" role="group" :aria-label="t('main.tr2_body.workspace.filter')">
              <button v-for="op in filterOptions" :key="op" type="button" class="btn btn-outline btn-sm" :data-testid="`tr2-filter-${op}`" :aria-pressed="fileFilter === op" @click="fileFilter = op">{{ op === 'all' ? t('main.tr2_body.workspace.all') : t(`main.tr2_body.workspace.kind_${op}`) }}</button>
            </div>

          </div>
          <ul v-if="items.length" ref="fileList" class="tr2-files" data-testid="tr2-file-list" :aria-label="t('main.tr2_body.files.heading')">
            <li v-for="entry in visibleItems" :key="`${entry.collection}:${entry.item.id}`" :class="{ 'is-selected': selectedKey === `${entry.collection}:${entry.item.id}` }">
              <button type="button" :data-testid="`tr2-item-${entry.item.id}`" :aria-pressed="selectedKey === `${entry.collection}:${entry.item.id}`" @click="selectItem(entry)">
                <span class="tr2-file-symbol" aria-hidden="true"><AppIcon name="file-text" /></span>
                <span class="tr2-file-name" :title="entry.item.file"><span class="tr2-path">{{ fileName(entry.item.file) }}</span><small>{{ fileDirectory(entry.item.file) }}</small></span>
                <span class="tr2-op" :class="`tr2-op-${entry.op}`">{{ opLabel(entry.op) }}</span>
                <span class="tr2-file-arrow" aria-hidden="true">›</span>
                <span v-if="editStatus(entry) && editStatus(entry) !== 'applicable'" class="tr2-edit-status" :class="`is-${editStatus(entry)}`" :title="editStatus(entry) ?? ''">{{ editStatus(entry) === 'applicable' ? '✓' : '!' }}</span>
              </button>
            </li>
          </ul>
          <p v-if="items.length && !visibleItems.length" class="tr2-note" data-testid="tr2-no-results">{{ t('main.tr2_body.workspace.no_results') }}</p>
          <div v-if="items.length" class="tr2-note tr2-file-bottom"><span>{{ t('main.tr2_body.files.selection_safe') }}</span><span role="status" data-testid="tr2-visible-count">{{ t('main.tr2_body.workspace.visible', { total: items.length, count: visibleItems.length }) }}</span></div>
        </section>

        <section class="tr2-section tr2-detail" data-testid="tr2-detail">
          <Tr2ItemEditor v-if="itemEditor" :key="itemEditor.key" :initial-op="itemEditor.op" :item="itemEditor.item" :disabled="locked || saving" @save="saveItem" @cancel="itemEditor = null" />
          <template v-else-if="selected">
            <p v-if="selectedHidden" class="tr2-note" data-testid="tr2-selection-hidden">{{ t('main.tr2_body.workspace.hidden_selection') }} <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-clear-filters" @click="clearFilters">{{ t('main.tr2_body.workspace.clear') }}</button></p>
            <div class="tr2-actions">
              <h4><span>{{ selected.item.file ?? t('main.tr2_body.files.no_file') }}</span> <span class="tr2-op" :class="`tr2-op-${selected.op}`">{{ opLabel(selected.op) }}</span></h4>
              <div>
                <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-item-edit" :disabled="locked" @click="openItemEditor(selected)"><AppIcon name="pencil-simple" /> {{ t('main.tr2_body.actions.item_edit') }}</button>
                <button type="button" class="btn btn-outline btn-sm btn-danger-outline" data-testid="tr2-item-delete" :disabled="locked" :aria-label="t('main.tr2_body.actions.item_delete')" :title="t('main.tr2_body.actions.item_delete')" @click="ask({ kind: 'delete', id: selected.item.id })"><AppIcon name="trash" /></button>
              </div>
            </div>
            <div class="tr2-detail-content">
            <template v-if="selected.op === 'deferred'">
              <p><b>{{ t('main.tr2_body.files.deferred_heading') }}</b>: {{ deferredReasonLabel(selected.item.reason) }} ({{ selected.item.reason }})</p>
              <p>{{ selected.item.rationale }}</p>
              <p class="tr2-note">{{ t('main.tr2_body.files.deferred_no_diff') }}</p>
            </template>
            <template v-else>
              <p class="tr2-note">{{ t(selected.op === 'edit' ? 'main.tr2_body.files.diff_hint' : 'main.tr2_body.files.create_hint') }}</p>
              <p v-if="selectedStatusText" class="tr2-error" data-testid="tr2-item-status">{{ selectedStatusText }}</p>
              <div class="tr2-diff-container"><Tr2CodeDiff :before="selected.op === 'edit' ? selected.item.anchor_old : undefined" :after="selected.op === 'edit' ? selected.item.replacement_new : selected.item.content" /></div>
              <p v-if="fileDetail?.truncated" class="tr2-note">{{ t('main.tr2_body.files.truncated') }}</p>
              <details v-if="fileDetail?.before_text != null"><summary>{{ t('main.tr2_body.files.current_source') }}</summary><pre>{{ fileDetail.before_text }}</pre></details>
            </template>
            <p class="tr2-note tr2-item-meta">{{ t('main.tr2_body.files.item_meta', { id: selected.item.id, confidence: selected.item.confidence ?? '', rationale: selected.item.rationale ?? '' }) }}</p>
            </div>
          </template>
          <p v-else class="tr2-note">{{ t('main.tr2_body.files.select_hint') }}</p>
        </section>
      </div>
      </section>

      <section class="tr2-section tr2-checks">
        <div class="tr2-checks-head"><div><h3>{{ t('main.tr2_body.v4.gate_heading') }}</h3><p>{{ t('main.tr2_body.v4.gate_hint') }}</p></div><span class="tr2-gate-summary">{{ t('main.tr2_body.v4.gate_summary', { count: view.body.edit_spec.gate.commands.length, status: gate ? t(gate.commands.some(command => command.timed_out || command.exit_code !== 0) ? 'main.tr2_body.v4.gate_failed' : 'main.tr2_body.v4.gate_completed') : t('main.tr2_body.v4.gate_waiting') }) }}</span></div>
        <p v-if="view.gate_admission?.all_candidate" class="tr2-gate-warning" data-testid="tr2-all-candidate-warning">{{ t('main.tr2_body.gate.all_candidate') }}</p>
        <p v-if="hasShellComplexCandidate" class="tr2-gate-warning" data-testid="tr2-shell-complex-warning">{{ t('main.tr2_body.gate.shell_complex') }}</p>
        <p v-if="!view.body.edit_spec.gate.commands.length">{{ t('main.tr2_body.gate.none') }}</p>
        <ul v-else class="tr2-gate">
          <li v-for="(_command, index) in view.body.edit_spec.gate.commands" :key="index" :class="`is-${gateStatus(index)}`">
            <span class="tr2-check-icon"><AppIcon name="code" /></span>
            <div class="tr2-check-copy"><code>{{ admissionCommand(index) }}</code>
            <span class="tr2-admission" :class="`is-${admissionState(index)}`" :data-testid="`tr2-admission-${index}`">{{ t(`main.tr2_body.gate.admission_${admissionState(index)}`) }}</span></div>
            <span class="tr2-runtime">{{ t(`main.tr2_body.gate.${gateStatus(index)}`) }}</span>
          </li>
        </ul>
      </section>

    </template>
    <details class="tr2-advanced" data-testid="tr2-advanced" :open="advancedOpen" @toggle="advancedOpen = ($event.target as HTMLDetailsElement).open">
      <summary><span class="tr2-advanced-symbol"><AppIcon name="dots-three" /></span><span>{{ t('main.tr2_body.workspace.advanced') }}<small>{{ t('main.tr2_body.workspace.advanced_hint') }}</small></span><AppIcon name="caret-down" /></summary>
      <div class="tr2-advanced-content">
        <div class="tr2-advanced-tabs" role="tablist" :aria-label="t('main.tr2_body.workspace.advanced')">
          <button v-for="tabKey in (['history', 'diagnostics', 'manage'] as const)" :key="tabKey" type="button" role="tab"
            :id="`tr2-${tab.id}-${tabKey}-tab`" :aria-controls="`tr2-${tab.id}-${tabKey}-panel`" :aria-selected="advancedTab === tabKey"
            :tabindex="advancedTab === tabKey ? 0 : -1" :data-testid="`tr2-advanced-tab-${tabKey}`"
            @click="advancedTab = tabKey" @keydown="switchAdvancedTab($event, tabKey)">{{ t(`main.tr2_body.workspace.tab_${tabKey}`) }}</button>
        </div>
        <p v-show="advancedTab === 'diagnostics'" class="tr2-note tr2-document-id">{{ tab.id }}</p>
        <template v-if="view && state">
          <section v-show="advancedTab === 'diagnostics'" :id="`tr2-${tab.id}-diagnostics-panel`" data-testid="tr2-diagnostics-panel" role="tabpanel" :aria-labelledby="`tr2-${tab.id}-diagnostics-tab`" class="tr2-diagnostics-grid">
            <div class="tr2-history-box"><h3>{{ t('main.tr2_body.diag.heading') }}</h3>
            <p v-if="awaitingApproval && view.derived.live_precheck.drift" class="tr2-error">{{ t('main.tr2_body.diag.drift_banner') }}</p>
            <p data-testid="tr2-diagnostic">{{ diagnostic ? (diagnostic.ready ? t('main.tr2_body.diag.ready') : t('main.tr2_body.diag.review_required')) : t('main.tr2_body.diag.not_run') }}<span v-if="diagnostic?.code"> · {{ diagnosticCodeText }}</span></p>
            <p v-if="awaitingApproval" class="tr2-note" data-testid="tr2-diag-strip">{{ t('main.tr2_body.diag.strip', { revision: view.document.revision_no, match: view.derived.live_precheck.source_available === false ? t('main.tr2_body.diag.unavailable') : view.derived.live_precheck.drift ? t('main.tr2_body.diag.mismatch') : t('main.tr2_body.diag.match') }) }}</p>
            <p v-if="retry?.allowed" class="tr2-note">{{ t('main.tr2_body.retry.available') }}</p>
            <p v-if="latest?.error_code" class="tr2-code">{{ latest.error_code }}<em v-if="latest.retryable === true">{{ t('main.tr2_body.attempts.retryable') }}</em><em v-else-if="latest.retryable === false">{{ t('main.tr2_body.attempts.not_retryable') }}</em></p>
            <button type="button" class="btn btn-outline btn-sm" :disabled="loading || !view" @click="runDiagnostic"><AppIcon name="arrow-clockwise" /> {{ t('main.tr2_body.actions.diagnostic') }}</button>
            </div><div class="tr2-history-box"><h3>{{ t('main.tr2_body.diag.technical') }}</h3>
            <details class="tr2-tech" open>
              <summary>{{ t('main.tr2_body.diag.technical') }}</summary>
              <p>{{ t('main.tr2_body.diag.baseline') }}: <code>{{ view.derived.live_precheck.baseline_fingerprint }}</code></p>
              <p>{{ t('main.tr2_body.diag.live') }}: <code>{{ view.derived.live_precheck.live_fingerprint ?? t('main.tr2_body.diag.unavailable') }}</code></p>
            </details>
            </div>
          </section>

          <section v-show="advancedTab === 'history'" :id="`tr2-${tab.id}-history-panel`" data-testid="tr2-history-panel" role="tabpanel" :aria-labelledby="`tr2-${tab.id}-history-tab`" class="tr2-history-grid">
            <div class="tr2-history-box">
          <h3>{{ t('main.tr2_body.attempts.heading') }}</h3><span class="tr2-sr-only" data-testid="tr2-revision">{{ view.document.revision_no }}</span>
          <div class="tr2-summary tr2-history-summary">
            <div><span>{{ t('main.tr2_body.summary.attempt') }}</span><strong>{{ latest ? t('main.tr2_body.v4.latest_attempt', { round: latest.approval_round, state: te(`main.tr2_body.attempt_state.${latest.state}`) ? t(`main.tr2_body.attempt_state.${latest.state}`) : latest.state }) : t('main.tr2_body.summary.no_attempt') }}</strong></div>
            <div><span>{{ t('main.tr2_body.summary.history') }}</span><strong data-testid="tr2-history-state">{{ historyLabel }}</strong></div>
            <div><span>{{ t('main.tr2_body.v4.ledger') }}</span><strong>{{ t('main.tr2_body.v4.ledger_count', { count: view.history?.ledger?.length ?? 0 }) }}</strong></div>
          </div>
          <details v-if="view.approval.attempts.length" class="tr2-attempt-detail"><summary :aria-label="t('main.tr2_body.v4.attempt_details')" :title="t('main.tr2_body.v4.attempt_details')">···</summary>
            <ol class="tr2-attempts" data-testid="tr2-attempts">
              <li v-for="(attempt, index) in view.approval.attempts" :key="attempt.attempt_id ?? index" :class="`is-${attempt.state}`">
                <span>{{ attemptLabel(attempt) }}</span>
                <span>{{ attempt.finished_at ?? attempt.started_at ?? '' }}</span>
                <span v-if="attempt.error_code">{{ attempt.error_code }}</span>
                <span v-if="attempt.commit_sha">{{ t('main.tr2_body.attempts.commit', { sha: String(attempt.commit_sha).slice(0, 7) }) }}</span>
                <em v-if="attempt.retryable === true">{{ t('main.tr2_body.attempts.retryable') }}</em>
                <em v-else-if="attempt.retryable === false">{{ t('main.tr2_body.attempts.not_retryable') }}</em>
              </li>
            </ol>
            <p>{{ t('main.tr2_body.summary.review_status') }} · {{ reviewLabel }} · {{ t(`main.tr2_body.state.${state}`) }} · Revision {{ view.document.revision_no }}</p>
          </details>
            </div>
            <div class="tr2-history-box">
            <details data-testid="tr2-history-toggle" :open="historyOpen" @toggle="historyOpen = ($event.target as HTMLDetailsElement).open">
              <summary>{{ t('main.tr2_body.recovery.history_heading') }}</summary>
              <Tr2RevisionPanel v-if="advancedOpen && historyOpen" :key="`history-${view.document.revision_no}`" :doc-id="tab.id" mode="history" compact :read-only="readOnly || !mutationAllowed" @changed="onRecovered" />
            </details>
            </div>
          </section>

        </template>
        <section v-show="advancedTab === 'manage'" :id="`tr2-${tab.id}-manage-panel`" role="tabpanel" :aria-labelledby="`tr2-${tab.id}-manage-tab`" class="tr2-section" data-testid="tr2-management">
          <h3>{{ t('main.tr2_body.workspace.manage') }}</h3>
          <p class="tr2-note">{{ t('main.tr2_body.workspace.manage_hint') }}</p>
          <div class="tr2-actions">
            <button type="button" class="btn btn-outline btn-sm" :disabled="!view" data-testid="tr2-download" @click="downloadSpec">{{ t('main.tr2_body.actions.download') }}</button>
            <label class="btn btn-outline btn-sm" :class="{ 'is-disabled': locked }" :aria-disabled="locked"><input type="file" accept="application/json,.json" data-testid="tr2-upload" :disabled="locked" @change="uploadSpec">{{ t('main.tr2_body.actions.upload') }}</label>
            <button type="button" class="btn btn-outline btn-sm" :disabled="locked" data-testid="tr2-edit-whole" @click="openWholeEditor(false)">{{ t('main.tr2_body.actions.edit_whole') }}</button>
            <button type="button" class="btn btn-outline btn-sm" :disabled="locked" data-testid="tr2-new-whole" @click="openWholeEditor(true)">{{ t('main.tr2_body.actions.new_whole') }}</button>
            <button type="button" class="btn btn-outline btn-sm btn-danger-outline" :disabled="locked" data-testid="tr2-reset-whole" @click="ask({ kind: 'reset' })">{{ t('main.tr2_body.actions.reset_whole') }}</button>
          </div>
          <details class="tr2-json-tools"><summary>{{ t('main.tr2_body.v4.item_json') }}</summary>
            <div class="tr2-actions">
            <label class="btn btn-outline btn-sm" :class="{ 'is-disabled': locked }" :aria-disabled="locked"><input type="file" hidden accept="application/json,.json" data-testid="tr2-upload-new-item" :disabled="locked" @change="uploadItem($event, null)">{{ t('main.tr2_body.actions.item_upload') }}</label>              <template v-if="selected">
                <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-item-download" :aria-label="t('main.tr2_body.actions.item_download')" :title="t('main.tr2_body.actions.item_download')" @click="downloadItem(selected)"><AppIcon name="download-simple" /></button>
                <label class="btn btn-outline btn-sm tr2-icon-action" :class="{ 'is-disabled': locked }" :aria-disabled="locked" :aria-label="t('main.tr2_body.actions.item_upload')" :title="t('main.tr2_body.actions.item_upload')"><input type="file" accept="application/json,.json" data-testid="tr2-item-upload" :disabled="locked" @change="uploadItem($event, selected)"><AppIcon name="upload-simple" /></label>
              </template>
            </div>
          </details>
        </section>

        <section v-show="advancedTab === 'manage'" v-if="view && state && wholeEditorOpen" class="tr2-section" data-testid="tr2-whole-editor">
          <h3>{{ t('main.tr2_body.editor.whole_title') }}</h3>
          <p class="tr2-note">{{ t('main.tr2_body.editor.whole_hint') }}</p>
          <p class="tr2-note">{{ t('main.tr2_body.editor.legend_editable') }} / {{ t('main.tr2_body.editor.legend_locked') }}</p>
          <textarea v-model="editorText" rows="18" :aria-label="t('main.tr2_body.editor.whole_title')" :disabled="locked || saving" />
          <div class="tr2-actions">
            <button type="button" class="btn btn-primary btn-sm" data-testid="tr2-save-whole" :disabled="locked || saving" @click="saveWhole">{{ t('main.tr2_body.actions.save') }}</button>
            <button type="button" class="btn btn-outline btn-sm" @click="wholeEditorOpen = false">{{ t('main.tr2_body.actions.cancel') }}</button>
          </div>
        </section>
      </div>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { deleteRequest, getRequest, postRequest, putRequest } from '@shared/api'
import { resolveGitError } from '@shared/gitErrors'
import type { Tab } from '../../stores/tabs'
import Tr2SourceImport from './Tr2SourceImport.vue'
import Tr2ImportDialog from './Tr2ImportDialog.vue'
import Tr2CodeDiff from './Tr2CodeDiff.vue'
import Tr2ItemEditor from './Tr2ItemEditor.vue'
import Tr2RevisionPanel from './Tr2RevisionPanel.vue'
import {
  describeTr2Error, gateResults, itemOp, readinessText, tr2State, tr2Tone,
  type ItemCollection, type ItemOp, type Row, type Tr2View,
} from './tr2State'

interface Entry { collection: ItemCollection; op: ItemOp; item: Row }
type Pending = { kind: 'reset' } | { kind: 'retry' } | { kind: 'delete'; id: string }

interface BatchRow { name: string; collection: ItemCollection | null; item: Row | null; valid: boolean; selected: boolean; problem: string }

const props = defineProps<{ tab: Tab; readOnly: boolean }>()
const { t, te } = useI18n()

const view = ref<Tr2View | null>(null)
const broken = ref<{ code: string } | null>(null)
const recoveryKey = ref(0)
const diagnostic = ref<Row | null>(null)
const fileDetail = ref<Row | null>(null)
const selectedKey = ref<string | null>(null)
const itemEditor = ref<{ key: number; op: ItemOp; item: Row | null } | null>(null)
const wholeEditorOpen = ref(false)
const historyOpen = ref(true)
const advancedTab = ref<'history' | 'diagnostics' | 'manage'>('history')
const advancedOpen = ref(false)
const fileQuery = ref('')
const fileFilter = ref<ItemOp | 'all'>('all')
const filterOptions: Array<ItemOp | 'all'> = ['all', 'edit', 'create_file', 'deferred']
const fileList = ref<HTMLUListElement | null>(null)
const editorText = ref('')
const error = ref('')
const notice = ref('')
const stale = ref(false)
const staleWhileEditing = ref(false)
const loading = ref(false)
const saving = ref(false)
const retrying = ref(false)
const confirming = ref<Pending | null>(null)
const sourceEditing = ref(false)
const sourceBusy = ref(false)
const batchRows = ref<BatchRow[]>([])
const batchRevision = ref<number | null>(null)
const batchLoading = ref(false)
const batchSelected = computed(() => batchRows.value.filter(row => row.valid && row.selected))
let batchGeneration = 0
const importOpen = ref(false)
const importMode = ref<'source' | 'json'>('source')
const jsonDragging = ref(false)
const batchInput = ref<HTMLInputElement | null>(null)
const sourceSaving = ref(false)
const sourceImport = ref<InstanceType<typeof Tr2SourceImport> | null>(null)
function openImport() { if (!locked.value) { importMode.value = 'source'; importOpen.value = true } }
function closeImport() {
  if (saving.value || sourceSaving.value) return
  closeBatch()
  sourceImport.value?.reset()
  importOpen.value = false
  jsonDragging.value = false
}
async function onSourcesSaved(value: { revision: number; message: string }) { closeBatch(); importOpen.value = false; await onRecovered(value) }
function sharedBatchPath(row: BatchRow): boolean {
  const normalize = (file: unknown) => String(file ?? '').replace(/\\/g, '/').split('/').filter(p => p && p !== '.').join('/').toLocaleLowerCase()
  const file = normalize(row.item?.file)
  return Boolean(file) && [...items.value.map(entry => entry.item), ...batchRows.value.filter(r => r.valid).map(r => r.item)].filter(item => normalize(item?.file) === file).length > 1
}
function dropBatch(event: DragEvent) {
  jsonDragging.value = false
  if (event.dataTransfer) void readBatchFiles(Array.from(event.dataTransfer.files))
}
let generation = 0
let editorSeq = 0
let fileGeneration = 0

const base = computed(() => `/api/v1/documents/${encodeURIComponent(props.tab.id)}/tr2`)
// The tree still embeds a legacy English "[Type]: " prefix in tab titles (see TabBar).
const title = computed(() => (view.value?.document as Row | undefined)?.title ?? props.tab.title.replace(/^\[.*?\]:\s*/, ''))
const state = computed(() => (view.value ? tr2State(view.value) : null))
const tone = computed(() => (state.value ? tr2Tone(state.value) : 'live'))
const latest = computed(() => view.value?.approval.latest_attempt ?? null)
const retry = computed(() => view.value?.approval.retry ?? null)
const mutationAllowed = computed(() => view.value?.mutation?.allowed ?? Boolean(view.value?.document.editable))
const locked = computed(() => props.readOnly || !view.value || !mutationAllowed.value || saving.value || batchLoading.value || sourceBusy.value)
const gate = computed(() => (view.value ? gateResults(view.value) : null))
const gateAdmission = computed(() => view.value?.gate_admission ?? null)
const commandAdmissionFingerprint = computed(() => gateAdmission.value?.fingerprint ?? null)
const hasShellComplexCandidate = computed(() => gateAdmission.value?.commands.some((item) => item.state === 'candidate' && item.shell_complex) === true)
const editing = computed(() => sourceEditing.value || sourceBusy.value || wholeEditorOpen.value || itemEditor.value !== null || batchLoading.value || batchRows.value.length > 0)
// Basis-vs-source only matters before approval: once applied, the source differs by design.
const awaitingApproval = computed(() => view.value?.document.doc_review_status === 'pending_review')
const notReadyReason = computed(() => (state.value === 'not_ready' ? readinessText(view.value?.readiness, t, te) : ''))

const items = computed<Entry[]>(() => {
  const spec = view.value?.body.edit_spec
  if (!spec) return []
  return [
    ...spec.edits.map((item) => ({ collection: 'edits' as const, op: itemOp('edits', item), item })),
    ...spec.deferred.map((item) => ({ collection: 'deferred' as const, op: 'deferred' as const, item })),
  ]
})
const selected = computed(() => items.value.find((entry) => `${entry.collection}:${entry.item.id}` === selectedKey.value) ?? null)
const visibleItems = computed(() => {
  const query = fileQuery.value.trim().toLocaleLowerCase()
  return items.value.filter((entry) => (fileFilter.value === 'all' || entry.op === fileFilter.value)
    && `${entry.item.file ?? ''} ${entry.item.id}`.toLocaleLowerCase().includes(query))
})
const selectedHidden = computed(() => selected.value !== null && !visibleItems.value.some((entry) => `${entry.collection}:${entry.item.id}` === selectedKey.value))
function clearFilters() { fileQuery.value = ''; fileFilter.value = 'all' }
watch([fileQuery, fileFilter], () => { if (fileList.value) fileList.value.scrollTop = 0 })
const counts = computed(() => ({
  edit: items.value.filter((entry) => entry.op === 'edit').length,
  create: items.value.filter((entry) => entry.op === 'create_file').length,
  deferred: items.value.filter((entry) => entry.op === 'deferred').length,
}))

const reviewLabel = computed(() => {
  const status = view.value?.document.doc_review_status ?? ''
  const key = `main.doc_header.status_${status}`
  return status && te(key) ? t(key) : status
})
const historyLabel = computed(() => {
  const value = view.value?.history?.source_history_state ?? 'unknown'
  const key = `main.tr2_body.history_state.${value}`
  return te(key) ? t(key) : value
})
const lockKey = computed(() => {
  if (!view.value) return null
  if (props.readOnly) return 'main.tr2_body.lock.read_only'
  const reason = view.value.mutation?.reason ?? (view.value.document.editable ? null : 'not_editable')
  return reason && te(`main.tr2_body.lock.${reason}`) ? `main.tr2_body.lock.${reason}` : null
})
// 0660 T0004 §3 (RC3): while a Time Machine commit cancel is pending the whole group is
// held (no rewind, no next step, no approval) and the cancel retry is the one way out. The
// rewind dialog's [다시 시도] is gone once that dialog is closed or the page reloads, so the
// proposal itself offers the same retry (`/return-point/cancel-commits`), from any tab,
// session or browser, decided by the server state it reads.
const cancelRetrying = ref(false)
const revertPending = computed(() => !props.readOnly && view.value?.mutation?.reason === 'revert_pending')
function cancelBlockedReason(reason: unknown): string {
  const key = `main.time_machine.reason_${typeof reason === 'string' && reason ? reason : 'not_attempted'}`
  return t(te(key) ? key : 'main.time_machine.reason_not_attempted')
}
async function retryCommitCancel() {
  if (cancelRetrying.value) return
  cancelRetrying.value = true
  notice.value = ''
  let outcome: { notice: string } | { error: string }
  try {
    const response = await postRequest<Row>(
      `/api/v1/documents/workflow/${encodeURIComponent(props.tab.id)}/return-point/cancel-commits`, {})
    const result = (response.data?.tr_commit_cancel ?? {}) as Row
    const canceled = Array.isArray(result.canceled)
      && result.canceled.some((row: Row) => row?.doc_id === props.tab.id)
    outcome = canceled
      ? { notice: t('main.tr2_body.revert_retry.done') }
      : { error: t('main.tr2_body.revert_retry.blocked', { reason: cancelBlockedReason(result.blocked_reason) }) }
  } catch (exc) {
    outcome = { error: resolveGitError(exc, t) }
  } finally {
    cancelRetrying.value = false
  }
  // Re-read first: refresh() clears `error` on a successful read, and the outcome of the
  // retry is what the reviewer must still see afterwards.
  await refresh()
  if ('notice' in outcome) { notice.value = outcome.notice; error.value = '' } else { error.value = outcome.error }
  window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: props.tab.projectId ?? null, doc_id: props.tab.id, source: 'tr2_body' } }))
}
const retryBlockedKey = computed(() => {
  const reason = retry.value?.reason
  if (!reason || !['recovery_required', 'not_retryable', 'revision_changed'].includes(reason)) return null
  return `main.tr2_body.retry.blocked_${reason}`
})
const confirmText = computed(() => {
  const pending = confirming.value
  if (!pending) return ''
  if (pending.kind === 'delete') return t('main.tr2_body.confirm.delete_item', { id: pending.id })
  if (pending.kind === 'retry') return t('main.tr2_body.confirm.retry', { revision: view.value?.document.revision_no ?? '' })
  return t('main.tr2_body.confirm.reset')
})
const diagnosticCodeText = computed(() => {
  const code = diagnostic.value?.code
  if (!code) return ''
  const key = `main.tr2_body.readiness.code.${code}`
  return te(key) ? t(key) : code
})
/** Per-edit applicability from the server readiness, keyed by edit id. */
const editStatuses = computed(() => new Map<string, string>(
  (view.value?.readiness?.edits ?? []).map((edit: Row) => [String(edit.id), String(edit.status)]),
))
function editStatus(entry: Entry): string | null {
  return entry.op === 'deferred' ? null : (editStatuses.value.get(String(entry.item.id)) ?? null)
}
const selectedStatusText = computed(() => {
  if (!selected.value) return ''
  const status = editStatus(selected.value)
  if (!status || status === 'applicable') return ''
  const key = `main.tr2_body.readiness.reason.${status}`
  return te(key) ? t(key, { id: selected.value.item.id }) : ''
})

function phaseLabel(phase?: string | null): string {
  const key = `main.tr2_body.phase.${phase ?? ''}`
  return phase && te(key) ? t(key) : (phase ?? '')
}
function attemptLabel(attempt: Row): string {
  const stateKey = `main.tr2_body.attempt_state.${attempt.state}`
  return t('main.tr2_body.summary.attempt_label', {
    round: attempt.approval_round ?? '', state: te(stateKey) ? t(stateKey) : attempt.state, phase: phaseLabel(attempt.phase),
  })
}
function fileName(file?: string): string { return file?.replace(/\\/g, '/').split('/').at(-1) || t('main.tr2_body.files.no_file') }
function fileDirectory(file?: string): string { return file?.replace(/\\/g, '/').split('/').slice(0, -1).join('/') || '' }
function opLabel(op: ItemOp): string {
  return t(op === 'edit' ? 'main.tr2_body.files.op_edit' : op === 'create_file' ? 'main.tr2_body.files.op_create' : 'main.tr2_body.files.op_deferred')
}
function deferredReasonLabel(reason: string): string {
  const key = `main.tr2_body.deferred_reason.${reason}`
  return te(key) ? t(key) : reason
}
function gateStatus(index: number): 'passed' | 'failed' | 'waiting' {
  const result = gate.value?.commands[index]
  if (!result) return 'waiting'
  return !result.timed_out && result.exit_code === 0 ? 'passed' : 'failed'
}
function admissionState(index: number): 'registered' | 'candidate' | 'suppressed' {
  return gateAdmission.value?.commands[index]?.state ?? 'registered'
}
function admissionCommand(index: number): string {
  return gateAdmission.value?.commands[index]?.command ?? view.value?.body.edit_spec.gate.commands[index] ?? ''
}

function describe(exc: any): string {
  const info = describeTr2Error(exc, t, te)
  if (info.stale) stale.value = true
  return info.text
}

async function refresh() {
  const current = ++generation
  loading.value = true
  try {
    const response = await getRequest<Tr2View>(base.value)
    if (current !== generation) return
    view.value = response.data
    broken.value = null
    error.value = ''
    stale.value = false
    staleWhileEditing.value = false
    if (selectedKey.value && !selected.value) { selectedKey.value = null; fileDetail.value = null; fileGeneration++ }
    if (!selectedKey.value && items.value.length) selectedKey.value = `${items.value[0].collection}:${items.value[0].item.id}`
    if (selected.value) await loadFileDetail(selected.value)
  } catch (exc) {
    if (current !== generation) return
    const info = describeTr2Error(exc, t, te)
    if (info.recovery) {
      // The stored proposal is unreadable: show what can be recovered instead of a dead end.
      view.value = null
      broken.value = { code: info.code ?? '' }
      recoveryKey.value++
      error.value = ''
    } else {
      error.value = t('main.tr2_body.errors.load_failed', { detail: info.text })
    }
  } finally {
    if (current === generation) loading.value = false
  }
}
async function reload() {
  closeBatch()
  wholeEditorOpen.value = false
  itemEditor.value = null
  await refresh()
}
async function runDiagnostic() {
  advancedOpen.value = true
  advancedTab.value = 'diagnostics'
  try { diagnostic.value = (await postRequest<Row>(`${base.value}/precheck`, {})).data; error.value = '' }
  catch (exc) { diagnostic.value = null; error.value = describe(exc) }
}
async function loadFileDetail(entry: Entry | null) {
  const current = ++fileGeneration
  fileDetail.value = null
  if (!entry || entry.op === 'deferred' || !entry.item.file) return
  try {
    const response = await getRequest<Row>(`${base.value}/files/${String(entry.item.file).split('/').map(encodeURIComponent).join('/')}`)
    if (current === fileGeneration) fileDetail.value = response.data
  } catch (exc) { if (current === fileGeneration) error.value = describe(exc) }
}
async function selectItem(entry: Entry) {
  selectedKey.value = `${entry.collection}:${entry.item.id}`
  itemEditor.value = null
  await loadFileDetail(entry)
}

function announce(changed: boolean) {
  if (!changed) return
  window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
    detail: { doc_id: props.tab.id, project: props.tab.projectId, revision_no: view.value?.document.revision_no ?? null, source: 'tr2_body' },
  }))
}

/** Every mutation: server-side merge + validation + revision CAS; then re-read the model. */
async function mutate(call: (revision: number) => Promise<{ data: any }>, expectedRevision?: number): Promise<boolean> {
  if (!view.value || locked.value) return false
  const docId = props.tab.id
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const response = await call(expectedRevision ?? view.value.document.revision_no)
    if (docId !== props.tab.id) return false
    diagnostic.value = null
    await refresh()
    notice.value = t('main.tr2_body.saved', { revision: response.data?.new_revision ?? view.value?.document.revision_no ?? '' })
    announce(true)
    return true
  } catch (exc) {
    if (docId === props.tab.id) error.value = describe(exc)
    return false
  } finally {
    saving.value = false
  }
}
async function onRecovered(value: { revision: number; message: string }) {
  diagnostic.value = null
  selectedKey.value = null
  fileDetail.value = null
  await refresh()
  notice.value = value.message
  announce(true)
}

function specPayload(spec: Row) {
  return { tr2_version: view.value!.body.tr2_version, source_t2_doc_id: view.value!.body.source_t2_doc_id, edit_spec: spec }
}
function parseObject(text: string): Row {
  const parsed = JSON.parse(text)
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(t('main.tr2_body.errors.object_required'))
  return parsed
}
function jsonError(exc: any): string {
  return t('main.tr2_body.errors.invalid_json', { reason: exc?.message ?? String(exc) })
}

function openWholeEditor(fresh: boolean) {
  if (!view.value || locked.value) return
  closeBatch()
  const spec = fresh
    ? { termination: 'needs_more_work', edits: [], deferred: [], gate: { commands: [], apply: false } }
    : view.value.body.edit_spec
  editorText.value = JSON.stringify(spec, null, 2)
  itemEditor.value = null
  wholeEditorOpen.value = true
}
async function saveWhole() {
  let spec: Row
  try { spec = parseObject(editorText.value) } catch (exc) { error.value = jsonError(exc); return }
  if (await mutate((revision) => putRequest(base.value, { expected_revision: revision, body: specPayload(spec) }))) wholeEditorOpen.value = false
}
async function uploadSpec(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || locked.value) return
  let spec: Row
  try {
    const parsed = parseObject(await file.text())
    spec = parseObject(JSON.stringify(parsed.edit_spec ?? parsed))
  } catch (exc) { error.value = jsonError(exc); return }
  // Only edit_spec travels: fingerprints, attempts and history in the file are ignored.
  await mutate((revision) => putRequest(base.value, { expected_revision: revision, body: specPayload(spec) }))
}
function closeBatch() {
  batchGeneration++
  batchRows.value = []
  batchRevision.value = null
  batchLoading.value = false
}
async function previewBatch(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  await readBatchFiles(files)
}
async function readBatchFiles(files: File[]) {
  if (!files.length || locked.value || !view.value) return
  closeBatch()
  importMode.value = 'json'
  importOpen.value = true
  if (files.length > 200 || files.reduce((sum, file) => sum + (file.size || 0), 0) > 10 * 1024 * 1024) { error.value = t('main.tr2_body.import.json_limit'); return }
  wholeEditorOpen.value = false
  itemEditor.value = null
  const current = batchGeneration
  const url = `${base.value}/items/batch/preview`
  const revision = view.value.document.revision_no
  batchRevision.value = revision
  batchLoading.value = true
  error.value = ''
  notice.value = ''
  try {
    const rows = await Promise.all(files.map(async (file): Promise<BatchRow> => {
      const row: BatchRow = { name: file.name, collection: null, item: null, valid: false, selected: false, problem: '' }
      try {
        if (file.size > 1024 * 1024) throw new Error(t('main.tr2_body.import.json_limit'))
        const text = await file.text()
        if (new TextEncoder().encode(text).length > 1024 * 1024) throw new Error(t('main.tr2_body.import.json_limit'))
        const parsed = parseObject(text)
        row.item = parseObject(JSON.stringify(parsed.item ?? parsed))
        const declared = parsed.item ? parsed.collection : undefined
        if (declared !== undefined && declared !== 'edits' && declared !== 'deferred') throw new Error(t('main.tr2_body.batch.collection'))
        row.collection = declared ?? (row.item.reason && !row.item.kind ? 'deferred' : 'edits')
      } catch (exc) { row.problem = jsonError(exc) }
      return row
    }))
    if (current !== batchGeneration) return
    batchRows.value = rows
    const candidates = rows.filter(row => row.item && row.collection && !row.problem)
    if (!candidates.length) return
    const response = await postRequest<{ items: Array<{ index: number; valid: boolean; error: Row | null }> }>(url, {
      expected_revision: revision, items: candidates.map(row => ({ collection: row.collection, item: row.item })),
    })
    if (current !== batchGeneration) return
    for (const result of response.data.items) {
      const row = candidates[result.index]
      if (!row) continue
      row.valid = result.valid
      row.selected = result.valid
      if (result.error) row.problem = describeTr2Error({ response: { status: 422, data: result.error } }, t, te).text
    }
    // rows are also held by the asynchronous local array; replace it to notify Vue.
    batchRows.value = rows.map(row => ({ ...row }))
  } catch (exc) {
    if (current === batchGeneration) error.value = describe(exc)
  } finally {
    if (current === batchGeneration) batchLoading.value = false
  }
}
async function importBatch() {
  if (locked.value || !batchSelected.value.length || batchRevision.value === null
    || batchRevision.value !== view.value?.document.revision_no || stale.value || staleWhileEditing.value) return
  const current = batchGeneration
  const revision = batchRevision.value
  const url = `${base.value}/items/batch`
  const entries = batchSelected.value.map(row => ({ collection: row.collection, item: row.item }))
  if (await mutate(() => postRequest(url, { expected_revision: revision, items: entries }), revision)) {
    if (current === batchGeneration) { closeBatch(); sourceImport.value?.reset(); importOpen.value = false }
  }
}

function downloadJson(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}
function downloadSpec() {
  if (view.value) downloadJson(`${props.tab.id}-edit-spec.json`, specPayload(view.value.body.edit_spec))
}
function downloadItem(entry: Entry) {
  downloadJson(`${props.tab.id}-${entry.item.id}.json`, { collection: entry.collection, item: entry.item })
}

function openItemEditor(entry: Entry | null) {
  if (locked.value) return
  closeBatch()
  wholeEditorOpen.value = false
  itemEditor.value = { key: ++editorSeq, op: entry?.op ?? 'edit', item: entry ? { ...entry.item } : null }
}
function itemUrl(id: string) {
  return `${base.value}/items/${encodeURIComponent(id)}`
}
async function saveItem(value: { collection: ItemCollection; item: Row; originalId: string | null }) {
  const ok = await mutate((revision) => (value.originalId
    ? putRequest(itemUrl(value.originalId), { expected_revision: revision, collection: value.collection, item: value.item })
    : postRequest(`${base.value}/items`, { expected_revision: revision, collection: value.collection, item: value.item })))
  if (ok) {
    itemEditor.value = null
    selectedKey.value = `${value.collection}:${value.item.id}`
    // The current-source preview belongs to the file the item now names.
    await loadFileDetail(selected.value)
  }
}
async function uploadItem(event: Event, target: Entry | null) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || locked.value) return
  let item: Row
  let collection: ItemCollection
  try {
    const parsed = parseObject(await file.text())
    item = parseObject(JSON.stringify(parsed.item ?? parsed))
    const declared = parsed.item ? parsed.collection : undefined
    collection = declared === 'deferred' || declared === 'edits' ? declared : (target?.collection ?? (item.reason && !item.kind ? 'deferred' : 'edits'))
  } catch (exc) { error.value = jsonError(exc); return }
  const replaceId = target?.item.id ?? (items.value.some((entry) => entry.item.id === item.id) ? String(item.id) : null)
  await saveItem({ collection, item, originalId: replaceId })
}

function ask(pending: Pending) { confirming.value = pending }
async function confirmYes() {
  const pending = confirming.value
  confirming.value = null
  if (!pending) return
  if (pending.kind === 'reset') {
    await mutate((revision) => deleteRequest(`${base.value}/spec?expected_revision=${revision}`))
  } else if (pending.kind === 'delete') {
    if (await mutate((revision) => deleteRequest(`${itemUrl(pending.id)}?expected_revision=${revision}`))) { selectedKey.value = null; fileDetail.value = null }
  } else {
    await runRetry()
  }
}
async function runRetry() {
  const current = view.value
  const plan = current?.approval.retry
  if (!current || !plan?.allowed || !plan.request_key || retrying.value) return
  retrying.value = true
  error.value = ''
  try {
    // The server named this request key after the attempt it retries: a double click or a
    // network retry replays that one attempt instead of starting another round.
    await postRequest('/api/v1/documents/review_transitions/approve', {
      doc_id: props.tab.id, comment: null, expected_revision: current.document.revision_no, request_key: plan.request_key,
    })
  } catch (exc) {
    error.value = describe(exc)
  } finally {
    retrying.value = false
    await refresh()
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: props.tab.projectId ?? null, doc_id: props.tab.id } }))
  }
}

function onServerChange(detail: any) {
  if (!detail || detail.source === 'tr2_body') return
  if (detail.doc_id && detail.doc_id !== props.tab.id) return
  if (detail.project && props.tab.projectId && detail.project !== props.tab.projectId) return
  const revision = detail.revision_no
  if (typeof revision === 'number' && view.value && revision <= view.value.document.revision_no) return
  if (editing.value || saving.value) { staleWhileEditing.value = true; return }
  void refresh()
}
const onContentChanged = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail?.doc_id === props.tab.id) onServerChange(detail) }
const onReviewChanged = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail?.doc_id === props.tab.id) onServerChange({ doc_id: detail.doc_id }) }
const onOpenDocsRefresh = (event: Event) => onServerChange({ ...((event as CustomEvent).detail ?? {}), revision_no: undefined })

const reviewSummary = computed(() => ({
  title: state.value === 'ready' ? t('main.tr2_body.v4.approval_ready') : state.value ? t(`main.tr2_body.state.${state.value}`) : t('main.tr2_body.loading'),
  description: state.value === 'ready'
    ? t('main.tr2_body.v4.approval_summary', { count: counts.value.edit + counts.value.create, excluded: counts.value.deferred })
    : state.value ? t(`main.tr2_body.state_desc.${state.value}`, { phase: phaseLabel(latest.value?.phase), code: '', reason: notReadyReason.value }) : '',
  ready: state.value === 'ready' && !props.readOnly && !locked.value && !editing.value && !stale.value && !staleWhileEditing.value,
}))
function switchAdvancedTab(event: KeyboardEvent, current: 'history' | 'diagnostics' | 'manage') {
  const keys = ['history', 'diagnostics', 'manage'] as const
  const index = keys.indexOf(current)
  const next = event.key === 'ArrowRight' ? keys[(index + 1) % keys.length]
    : event.key === 'ArrowLeft' ? keys[(index + keys.length - 1) % keys.length]
    : event.key === 'Home' ? keys[0] : event.key === 'End' ? keys[2] : null
  if (!next) return
  event.preventDefault()
  advancedTab.value = next
  document.getElementById(`tr2-${props.tab.id}-${next}-tab`)?.focus()
}
defineExpose({ commandAdmissionFingerprint, reviewSummary, refresh })

onMounted(() => {
  window.addEventListener('fg:document_content_changed', onContentChanged)
  window.addEventListener('fg:doc_review_status_changed', onReviewChanged)
  window.addEventListener('fg:open_docs_refresh', onOpenDocsRefresh)
})
onBeforeUnmount(() => {
  closeBatch()
  generation++
  fileGeneration++
  window.removeEventListener('fg:document_content_changed', onContentChanged)
  window.removeEventListener('fg:doc_review_status_changed', onReviewChanged)
  window.removeEventListener('fg:open_docs_refresh', onOpenDocsRefresh)
})
watch(() => props.tab.id, () => {
  importOpen.value = false
  importMode.value = 'source'
  closeBatch()
  fileGeneration++
  fileDetail.value = null
  clearFilters()
  advancedOpen.value = false
  advancedTab.value = 'history'
  diagnostic.value = null
  selectedKey.value = null
  itemEditor.value = null
  wholeEditorOpen.value = false
  historyOpen.value = true
  broken.value = null
  void refresh()
}, { immediate: true })
</script>

<style scoped>
.tr2 { padding: 16px; display: grid; gap: 16px; }
.tr2-header, .tr2-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.tr2-header small { display: block; color: var(--text-m); font-family: monospace; }
.tr2-title { display: grid; gap: 4px; }
.tr2-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tr2-title-row strong { font-size: 1.05rem; }
.tr2-chip { color: #fff; padding: 2px 8px; border-radius: 999px; font-size: .72rem; font-weight: 700; }
.tr2-apply-notice { margin: 0; font-size: .8rem; color: var(--primary-h, #1d4ed8); }
.tr2-actions input[type=file], .tr2-section input[type=file] { display: none; }
.tr2 label.btn.is-disabled { opacity: .5; pointer-events: none; }
.tr2-pill { padding: 2px 9px; border-radius: 999px; font-size: .72rem; font-weight: 700; }
.tr2-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
.tr2-summary div { border: 1px solid var(--border, #d9e1e8); border-radius: 8px; padding: 10px; }
.tr2-summary span, .tr2-summary strong { display: block; }
.tr2-summary span, .tr2-note { color: var(--text-m); font-size: .8rem; }
.tr2-diag-strip { margin: 0; padding: 7px 14px; font-size: .74rem; border-radius: 6px; background: var(--primary-l, #eff6ff); color: var(--primary-h, #1d4ed8); }
.tr2-strip { display: grid; gap: 4px; border-radius: 8px; padding: 10px 12px; border: 1px solid transparent; }
.tr2-tone-safe { background: #ecfdf5; border-color: #a7f3d0; color: #065f46; }
.tr2-tone-live { background: #eff6ff; border-color: #bfdbfe; color: #1e40af; }
.tr2-tone-warn { background: #fffbeb; border-color: #fde68a; color: #92400e; }
.tr2-tone-danger { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
.tr2-tone-blocked { background: #ede9fe; border-color: #c4b5fd; color: #5b21b6; }
.tr2-tone-done { background: #f0fdf4; border-color: #bbf7d0; color: #166534; }
.tr2-tone-past { background: #f3f4f6; border-color: #d1d5db; color: #374151; }
.tr2-code em, .tr2-attempts em { margin-left: 6px; font-style: normal; font-size: .75rem; }
.tr2-retry, .tr2-confirm, .tr2-error { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tr2-confirm { border: 1px solid var(--border, #d9e1e8); border-radius: 8px; padding: 8px 12px; }
.tr2-error { color: var(--danger, #b91c1c); }
.tr2-notice { color: var(--success, #15803d); }
.tr2-batch-list { max-height: 360px; overflow-y: auto; list-style: none; padding: 0; }
.tr2-batch-list li { padding: 8px 0; border-bottom: 1px solid var(--border, #d9e1e8); overflow-wrap: anywhere; }
.tr2-batch-list pre { white-space: pre-wrap; overflow-wrap: anywhere; }
.tr2-batch-list input[type=checkbox] { margin-right: 8px; }
.tr2-split { display: grid; grid-template-columns: minmax(220px, 1fr) 2fr; gap: 16px; }
.tr2-section { border-top: 1px solid var(--border, #d9e1e8); padding-top: 12px; min-width: 0; }
.tr2-section h3 { margin: 0 0 8px; font-size: 1rem; }
.tr2-section h3 small { font-weight: normal; color: var(--text-m); }
.tr2-section summary { cursor: pointer; font-weight: 600; }
.tr2-files, .tr2-gate, .tr2-attempts { list-style: none; padding: 0; margin: 0 0 8px; }
.tr2-review-summary strong { font-size: 1.3rem; }
.tr2-file-controls { display: grid; gap: 8px; margin-bottom: 8px; }
.tr2-file-controls input { box-sizing: border-box; width: 100%; min-width: 0; padding: 8px 10px; border: 1px solid var(--border, #d9e1e8); border-radius: 6px; background: var(--bg, #f6f8fa); color: inherit; }
.tr2-file-controls p { margin: 0; }
.tr2-filters { display: flex; gap: 4px; flex-wrap: wrap; }
.tr2-filters button[aria-pressed=true] { background: var(--primary-l, #eff6ff); border-color: var(--primary, #2563eb); }
.tr2-files { height: 420px; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
.tr2-files button:focus-visible, .tr2-filters button:focus-visible, .tr2-file-controls input:focus-visible, .tr2-advanced > summary:focus-visible { outline: 2px solid var(--primary, #2563eb); outline-offset: 2px; }
.tr2-advanced { border: 1px solid var(--border, #d9e1e8); border-radius: 8px; }
.tr2-advanced > summary { padding: 12px; cursor: pointer; font-weight: 600; }
.tr2-advanced > summary small { display: block; margin-top: 4px; color: var(--text-m); font-weight: normal; }
.tr2-advanced-content { padding: 0 12px 12px; display: grid; gap: 16px; }
.tr2-document-id { margin: 0; font-family: monospace; overflow-wrap: anywhere; }
.tr2-files li button { display: flex; gap: 8px; align-items: center; width: 100%; border: 1px solid transparent; border-radius: 6px; background: none; padding: 6px 4px; cursor: pointer; text-align: left; }
.tr2-files li.is-selected button { background: var(--primary-l, #eff6ff); border-color: var(--primary, #2563eb); }
.tr2-path { overflow-wrap: anywhere; font-family: monospace; font-size: .8rem; flex: 1; }
.tr2-op { font-size: .7rem; font-weight: 700; padding: 1px 6px; border-radius: 4px; }
.tr2-op-edit { background: #fef3c7; color: #92400e; }
.tr2-op-create_file { background: #d1fae5; color: #047857; }
.tr2-op-deferred { background: #f3f4f6; color: #4b5563; border: 1px dashed #9ca3af; }
.tr2-edit-status { font-weight: 700; }
.tr2-edit-status.is-applicable { color: #15803d; }
.tr2-edit-status:not(.is-applicable) { color: #b91c1c; }
.tr2-diff { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tr2-diff--new { grid-template-columns: 1fr; }
.tr2-gate li, .tr2-attempts li { display: flex; gap: 8px; flex-wrap: wrap; padding: 4px 0; }
.tr2-gate code { flex: 1 1 100%; min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
.tr2-admission { font-size: .72rem; font-weight: 700; padding: 1px 6px; border-radius: 999px; }
.tr2-admission.is-registered { background: #e0f2fe; color: #0369a1; }
.tr2-admission.is-candidate { background: #fef3c7; color: #92400e; }
.tr2-admission.is-suppressed { background: #fee2e2; color: #991b1b; }
.tr2-runtime { font-size: .76rem; }
.tr2-gate li.is-passed .tr2-runtime { color: #15803d; }
.tr2-gate li.is-failed .tr2-runtime { color: #b91c1c; }
.tr2-gate-warning { margin: 6px 0; padding: 7px 9px; border-radius: 6px; background: #fffbeb; color: #92400e; font-size: .78rem; }
.tr2-tech { font-size: .78rem; }
.tr2 pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg, #f6f8fa); padding: 10px; border-radius: 6px; max-height: 320px; overflow: auto; }
.tr2 textarea { box-sizing: border-box; width: 100%; font-family: monospace; }
@media (max-width: 900px) { .tr2-split, .tr2-diff { grid-template-columns: 1fr; } }

/* 0640 reference: review card, compact metadata, 34% file rail and stacked diff. */
.tr2 { background: var(--bg, #f5f7fb); border: 0; border-radius: 0; padding: 20px; gap: 18px; color: var(--text, #202b3a); }
.tr2 .btn { box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center; gap: 6px; border: 1px solid var(--border, #e3e9f1); border-radius: 8px; padding: 8px 12px; background: var(--surface, #fff); color: var(--text, #48586c); font: inherit; font-size: 12px; font-weight: 650; cursor: pointer; }
.tr2 .btn-sm { padding: 6px 10px; font-size: 11px; }
.tr2 .btn-primary { background: var(--primary, #356ae6); color: #fff; border-color: var(--primary, #356ae6); }
.tr2 .btn-danger, .tr2 .btn-danger-outline { color: var(--danger, #b83b45); border-color: #f0c9ce; }
.tr2 .btn-danger { background: var(--danger, #b83b45); color: #fff; }
.tr2 .btn:hover:not(:disabled) { filter: brightness(.97); }
.tr2 .btn:disabled { opacity: .45; cursor: not-allowed; }
.tr2-header { align-items: flex-start; }
.tr2-title-row strong { font-size: 21px; line-height: 1.35; letter-spacing: -.035em; }
.tr2-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; padding: 16px 20px; border-radius: 10px; }
.tr2-state-symbol { flex: none; width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; background: #fff8; font-weight: 800; }
.tr2-state-copy { flex: 1 1 240px; min-width: 0; }
.tr2-strip .tr2-retry { flex: 1 1 100%; padding-left: 50px; }
@media (max-width: 560px) { .tr2-strip .tr2-retry { padding-left: 0; } }
.tr2-state-copy strong { font-size: 14px; }
.tr2-state-copy p { margin: 3px 0 0; font-size: 12px; line-height: 1.6; }
.tr2-revision-badge { flex: none; background: #fff8; border: 1px solid currentColor; border-radius: 7px; padding: 6px 10px; font-size: 11px; }
.tr2-review-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.tr2-review-summary div { padding: 15px 17px; background: var(--surface, #fff); border-radius: 12px; display: grid; grid-template-columns: 34px 1fr; grid-template-areas: "icon number" "icon label"; gap: 2px 12px; align-items: center; }
.tr2-review-summary div::before { content: "▤"; grid-area: icon; height: 34px; background: var(--bg, #f3f6fc); color: var(--text-m, #8493ae); border-radius: 9px; display: grid; place-items: center; }
.tr2-review-summary span { grid-area: label; font-size: 11px; }
.tr2-review-summary strong { grid-area: number; line-height: 1.1; }
.tr2-review-summary strong { font-size: 20px; }
.tr2-review-card, .tr2-checks, .tr2-advanced { background: var(--surface, #fff); border: 1px solid var(--border, #e3e9f1); border-radius: 12px; box-shadow: 0 7px 25px #1c2f4c0b; min-width: 0; }
.tr2-review-card { overflow: hidden; }
.tr2-review-head { padding: 18px 20px 15px; border-bottom: 1px solid var(--border, #e3e9f1); display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.tr2-review-head h3 { margin: 0; font-size: 15px; }
.tr2-review-head p { margin: 3px 0 0; }
.tr2-split { grid-template-columns: minmax(278px, 34%) minmax(0, 1fr); gap: 0; align-items: start; }
.tr2-split > .tr2-section { border-top: 0; padding: 0; }
.tr2-file-column { border-right: 1px solid var(--border, #e3e9f1); }
.tr2-file-controls { padding: 12px 13px; margin: 0; border-bottom: 1px solid var(--border, #e3e9f1); }
.tr2-file-controls input { background: var(--bg, #fafcff); font-size: 12px; border-radius: 8px; }
.tr2-filters button { border-color: transparent !important; background: transparent !important; color: var(--text-m, #8592a5); }
.tr2-filters button[aria-pressed=true] { background: var(--primary-l, #edf3ff) !important; color: var(--primary, #345fdb); }
.tr2-files { height: auto; max-height: 420px; margin: 0; padding: 9px; overflow-x: hidden; }
.tr2-files li button { padding: 12px; gap: 10px; box-sizing: border-box; margin-bottom: 4px; border-radius: 8px; color: inherit; }
.tr2-files li button:hover { background: var(--bg, #f6f8fc); }
.tr2-file-symbol { width: 31px; height: 34px; background: var(--bg, #f1f4f8); border-radius: 7px; color: var(--text-m, #8291a7); display: grid; place-items: center; flex: none; }
.tr2-file-name { min-width: 0; flex: 1; display: grid; gap: 3px; }
.tr2-path { font-family: inherit; font-size: 12px; line-height: 1.5; font-weight: 650; overflow-wrap: anywhere; }
.tr2-file-name small { font-size: 10.5px; color: var(--text-m, #96a2b2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tr2-file-bottom, .tr2-file-column > p { margin: 0; padding: 13px 15px; border-top: 1px solid var(--border, #eef1f5); }
.tr2-detail { min-width: 0; }
.tr2-detail > .tr2-actions { padding: 16px 19px; border-bottom: 1px solid var(--border, #e3e9f1); gap: 10px; }
.tr2-detail h4 { margin: 0; font-size: 12.5px; overflow-wrap: anywhere; }
.tr2-detail > p, .tr2-detail > details { margin: 12px 19px; font-size: 12px; }
.tr2-detail > .tr2-actions > div { display: flex; gap: 5px; flex-wrap: wrap; }
.tr2-diff { display: block; margin: 16px 19px; }
.tr2-checks { padding: 18px 20px; }
.tr2-gate { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.tr2-gate li { border: 1px solid var(--border, #e7ecf3); border-radius: 9px; padding: 11px 13px; }
.tr2-gate code { white-space: pre; overflow: auto; word-break: normal; overflow-wrap: normal; font-size: 11.5px; }
.tr2 pre { white-space: pre; word-break: normal; overflow-wrap: normal; max-width: 100%; font: 12px/1.6 ui-monospace, Consolas, monospace; }
.tr2-import-tabs { display: flex; gap: 6px; margin-bottom: 15px; border-bottom: 1px solid var(--border, #e3e9f1); padding-bottom: 10px; }
.tr2-import-tabs button { border: 0; background: transparent; color: var(--text-m, #8190a2); padding: 8px 12px; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 650; }
.tr2-import-tabs button[aria-selected=true] { color: var(--primary, #356ae6); background: var(--primary-l, #eef4ff); }
.tr2-dropzone { padding: 24px 18px; text-align: center; border: 1.5px dashed var(--border, #b8cbe8); border-radius: 11px; background: var(--bg, #f8fbff); }
.tr2-dropzone.is-dragging { border-color: var(--primary, #356ae6); background: var(--primary-l, #eef4ff); }
.tr2-dropzone input[type=file] { display: none; }
.tr2-batch-list { display: grid; gap: 8px; }
.tr2-batch-list li { padding: 12px; border: 1px solid var(--border, #e3e9f1); border-radius: 9px; }
.tr2-batch-list pre { white-space: pre; overflow-wrap: normal; }
.tr2-advanced > summary { padding: 18px 20px; }
@media (max-width: 780px) { .tr2 { padding: 12px; gap: 12px; } .tr2-split { grid-template-columns: minmax(0, 1fr); } .tr2-file-column { border-right: 0; border-bottom: 1px solid var(--border, #e3e9f1); } .tr2-gate { grid-template-columns: minmax(0, 1fr); } .tr2-revision-badge { display: none; } }
.tr2-advanced-tabs { display: flex; gap: 5px; border-bottom: 1px solid var(--border, #e3e9f1); padding: 12px 0 9px; }
.tr2-advanced-tabs button { border: 0; background: transparent; border-radius: 7px; padding: 7px 11px; color: var(--text-m); cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; }
.tr2-advanced-tabs button[aria-selected="true"] { background: #eef3ff; color: #3d65d4; }
.tr2-advanced-tabs button:focus-visible { outline: 2px solid var(--primary, #356ae6); outline-offset: 2px; }

/* v4: local palette and spacing, with existing actions and approval contracts. */
.tr2 { --bg: #f5f7fb; --surface: #fff; --border: #e3e9f1; --text: #202b3a; --text-m: #718096; --primary: #356ae6; --primary-l: #eef4ff; --danger: #b83b45; font: 14px/1.55 system-ui, -apple-system, 'Segoe UI', 'Malgun Gothic', sans-serif; }
.tr2-title-row .tr2-chip { flex-basis: 100%; background: transparent; color: #78869b; padding: 0; border-radius: 0; font-size: 11px; font-weight: 500; }
.tr2-title-row { gap: 5px 10px; }
.tr2-title-row strong { font-size: 23px; }
.tr2-apply-notice { color: #8995a5; font-size: 12px; }
.tr2-header > .tr2-actions { padding-top: 12px; }
.tr2-pill { border-radius: 6px; }
.tr2-tone-safe, .tr2-tone-done { background: #f2fbf6; border-color: #d4ebdd; color: #107d53; }
.tr2-tone-safe .tr2-state-symbol, .tr2-tone-done .tr2-state-symbol { background: #d9f3e5; }
.tr2-tone-safe .tr2-state-copy p, .tr2-tone-done .tr2-state-copy p { color: #5f7b6b; }
.tr2-state-copy strong { font-size: 13.5px; }
.tr2-review-summary strong { font-size: 18px; }
.tr2-review-summary div::before { background: #f3f6fc; }
.tr2-op-edit { background: #eef4ff; color: #356ae6; }
.tr2-op-create_file { background: #e9f8ef; color: #107d53; }
.tr2-op-deferred { background: #f0f3f7; color: #657185; border: 0; }
.tr2-files li.is-selected button { background: #edf3ff; border-color: #c8d8ff; }
.tr2-file-controls input { height: 34px; background: #fafcff; }
.tr2-files { max-height: 475px; display: grid; gap: 4px; align-content: start; }
.tr2-files li { margin: 0; padding: 0; display: contents; }
.tr2-files li button { margin: 0; padding: 9px 12px; }
.tr2-detail { display: flex; flex-direction: column; align-self: stretch; }
.tr2-detail > .tr2-actions { align-items: flex-start; }
.tr2-detail > .tr2-actions h4 { flex: 1 1 220px; }
.tr2-diff-container { margin: 16px 19px; min-width: 0; }
.tr2-detail > .tr2-item-meta { margin-top: auto; padding-top: 15px; }
.tr2-advanced-content { padding: 0 20px 22px; gap: 15px; }
.tr2-history-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.tr2-history-box { border: 1px solid var(--border); border-radius: 9px; padding: 15px; min-width: 0; }
.tr2-history-summary { display: grid; grid-template-columns: 1fr; gap: 10px; margin-bottom: 15px; }
.tr2-history-summary div { border: 0; border-radius: 0; padding: 0; display: flex; justify-content: space-between; gap: 10px; }
.tr2-history-summary strong { font-size: 12px; text-align: right; }
.tr2-history-summary span { font-size: 12px; }
.tr2-attempts li { border-top: 1px solid var(--border); padding: 10px 0; font-size: 11.5px; overflow-wrap: anywhere; }
@media (max-width: 780px) { .tr2-history-grid { grid-template-columns: minmax(0, 1fr); } }

.tr2-title { gap: 8px; }
.tr2-files li button { padding: 8px 10px; min-height: 52px; }
.tr2-file-symbol { height: 27px; width: 27px; }
.tr2-path { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tr2-file-bottom { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; font-size: 11px; }
.tr2-file-bottom [data-testid="tr2-visible-count"] { white-space: nowrap; color: #4b66a4; font-weight: 700; }
.tr2-detail [data-testid="tr2-item-download"], .tr2-detail [data-testid="tr2-item-delete"], .tr2-icon-action { width: 32px; height: 32px; padding: 6px; font-size: 16px; line-height: 1; }
.tr2-history-box h3 { font-size: 12px; margin: 0 0 10px; }
.tr2-history-box p { font-size: 11.5px; color: var(--text-m); }

/* Match the confirmed v4's component metrics without changing server admission. */
.tr2-title-row strong { letter-spacing: -.055em; }
.tr2-heading-id { font: 12px/1.55 ui-monospace, 'Cascadia Code', Consolas, monospace; }
.tr2 .app-icon { font-size: 16px; }
.tr2-header .btn, .tr2-review-head .btn { padding: 9px 12px; font-size: 12px; }
.tr2-title-row .tr2-pill { padding: 3px 9px; font-size: 11px; font-weight: 700; }
.tr2-title-row .tr2-tone-safe, .tr2-title-row .tr2-tone-done { background: #e9f8ef; }
.tr2-strip { flex-wrap: nowrap; }
.tr2-state-copy { flex: 1; }
.tr2-state-copy strong { color: #202b3a; letter-spacing: -.015em; }
.tr2-state-copy p { margin-top: 2px; line-height: 1.55; }
.tr2-tone-danger, .tr2-tone-blocked { background: #fff5f6; border-color: #f8dcdf; color: #8b3039; }
.tr2-tone-danger .tr2-state-symbol, .tr2-tone-blocked .tr2-state-symbol { background: #ffe2e6; color: #ae3440; }
.tr2-tone-danger .tr2-state-copy strong, .tr2-tone-blocked .tr2-state-copy strong { color: #8b3039; }
.tr2-tone-danger .tr2-state-copy p, .tr2-tone-blocked .tr2-state-copy p { color: #a16e72; }
.tr2-tone-warn { background: #fffaf0; border-color: #fae8c6; }
.tr2-tone-warn .tr2-state-symbol { background: #fff0ce; color: #a9710f; }
.tr2-tone-warn .tr2-state-copy strong { color: #805708; }
.tr2-tone-warn .tr2-state-copy p { color: #9a7b4b; }
.tr2-revision-badge { border-color: #cce8d9; background: #fff; color: #638773; }
.tr2-strip:not(.tr2-tone-safe):not(.tr2-tone-done) .tr2-revision-badge { border-color: var(--border); color: #718096; }
.tr2-strip .tr2-retry { flex: none; padding: 0; }
.tr2-retry-copy { display: none; }
.tr2-strip .tr2-note { font-size: 11px; max-width: 220px; }
.tr2-review-summary div { column-gap: 12px; row-gap: 0; }
.tr2-review-summary div::before { content: none; }
.tr2-review-summary .tr2-stat-icon { grid-area: icon; width: 34px; height: 34px; border-radius: 9px; background: #f3f6fc; color: #8493ae; display: grid; place-items: center; }
.tr2-review-summary strong { letter-spacing: -.035em; font-weight: 750; }
.tr2-review-summary span:not(.tr2-stat-icon) { margin-top: 2px; color: #8693a6; }
.tr2-review-head h3 { letter-spacing: -.025em; }
.tr2-review-head p { font-size: 11px; color: #8b96a6; }
.tr2-split { align-items: stretch; }
.tr2-file-controls { gap: 0; }
.tr2-search { height: 34px; display: flex; align-items: center; gap: 7px; padding: 0 10px; border: 1px solid #e3e9f1; border-radius: 8px; background: #fafcff; color: #9cabbc; }
.tr2-file-controls input { height: auto; padding: 0; border: 0; background: transparent; color: #314157; outline: 0; }
.tr2-filters { gap: 5px; margin-top: 10px; }
.tr2-filters .btn { padding: 5px 9px; border-radius: 7px; font-size: 11px; color: #8592a5; }
.tr2-filters button[aria-pressed=true] { background: #edf3ff !important; color: #345fdb; }
.tr2-files { height: 475px; max-height: 475px; min-height: 260px; }
.tr2-path { line-height: 1.55; color: #344255; }
.tr2-file-name { gap: 3px; }
.tr2-file-name small { color: #96a2b2; line-height: 1.55; }
.tr2-file-symbol { color: #8291a7; background: #f1f4f8; }
.tr2-files li.is-selected .tr2-file-symbol { background: #dde9ff; color: #5376d2; }
.tr2-file-arrow { color: #afbac8; font-size: 16px; margin-left: -3px; }
.tr2-files li.is-selected .tr2-file-arrow { color: #6383cb; }
.tr2-op { padding: 2px 7px; font-size: 10px; border-radius: 6px; white-space: nowrap; }
.tr2-file-bottom { color: #93a0ae; border-color: #eef1f5; }
.tr2-detail > .tr2-actions { align-items: center; gap: 12px; flex-wrap: nowrap; }
.tr2-detail > .tr2-actions h4 { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; flex: 1; min-width: 0; }
.tr2-detail > .tr2-actions h4 > span:first-child { overflow-wrap: anywhere; font-size: 12.5px; }
.tr2-detail > .tr2-actions > div { flex: none; gap: 5px; }
.tr2-detail-content { padding: 19px; flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 15px; }
.tr2-detail-content > p, .tr2-detail-content > details { margin: 0; font-size: 12px; }
.tr2-detail-content > .tr2-note { color: #718199; }
.tr2-diff-container { margin: 0; }
.tr2-detail-content > details { font-size: 11px; color: #8390a2; }
.tr2-detail-content > .tr2-item-meta { margin-top: auto; padding-top: 0; font-size: 11px; color: #8390a2; }
.tr2-checks-head { margin-bottom: 13px; }
.tr2-checks-head h3 { margin: 0; font-size: 14px; }
.tr2-checks-head p { margin: 3px 0 0; color: #94a0ae; font-size: 11px; }
.tr2-gate { margin: 0; }
.tr2-gate li { flex-wrap: nowrap; align-items: center; gap: 10px; border-color: #e7ecf3; }
.tr2-check-icon { width: 28px; height: 28px; flex: none; display: grid; place-items: center; color: #7994bf; background: #f0f5fc; border-radius: 7px; }
.tr2-check-copy { flex: 1; min-width: 0; }
.tr2-check-copy code { display: block; }
.tr2-admission { display: block; border-radius: 0; font-size: 10px; font-weight: 400; padding: 0; color: #9ca7b5; }
.tr2-admission.is-registered { color: #9ca7b5; background: transparent; }
.tr2-runtime { flex: none; background: #f0f3f7; color: #657185; font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 6px; }
.tr2-gate li.is-passed .tr2-runtime { color: #107d53; background: #e9f8ef; }
.tr2-gate li.is-failed .tr2-runtime { color: #b83b45; background: #fff0f1; }
.tr2-advanced > summary { list-style: none; display: flex; align-items: center; gap: 12px; }
.tr2-advanced > summary::-webkit-details-marker { display: none; }
.tr2-advanced > summary > .app-icon { margin-left: auto; color: #97a4b6; }
.tr2-advanced[open] > summary > .app-icon { transform: rotate(180deg); }
.tr2-advanced > summary small { margin-top: 3px; font-size: 11px; color: #94a0ae; }
.tr2-advanced-content { border-top: 1px solid var(--border); padding-top: 17px; }
.tr2-advanced-tabs { padding-top: 0; }
.tr2-history-summary { margin-bottom: 0; }
.tr2-history-summary strong { font-weight: 700; }
.tr2-history-box > details > summary { font-size: 12px; margin-bottom: 10px; }
.tr2-history-box > .tr2-attempt-detail > summary { margin: 10px 0 0; font-weight: 400; font-size: 11px; color: #8390a2; }
.tr2-management, [data-testid=tr2-management] { border: 0; padding: 0; }
[data-testid=tr2-management] h3 { display: none; }
[data-testid=tr2-management] p { margin: 0 0 15px; font-size: 12px; }
[data-testid=tr2-management] .tr2-actions { justify-content: flex-start; }
@media(max-width:900px) { .tr2-files { height: 370px; max-height: 370px; } }
@media(max-width:780px) { .tr2-detail > .tr2-actions { flex-wrap: wrap; } }
@media(max-width:560px) { .tr2-files { height: 290px; max-height: 290px; } .tr2-strip { flex-wrap: wrap; } .tr2-strip .tr2-retry { margin-left: 50px; } .tr2-detail-content { padding: 13px; } }

.tr2-title { gap: 7px; }
.tr2-state-copy strong { display: block; }
.tr2-check-copy code { display: inline; }
.tr2-checks-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.tr2-gate-summary { color: #356ae6; background: #eef4ff; border-radius: 6px; padding: 3px 9px; font-size: 11px; font-weight: 700; white-space: nowrap; }
.tr2-json-tools { margin-top: 15px; font-size: 11px; color: #8390a2; }
.tr2-json-tools .tr2-actions { margin-top: 10px; }
.tr2-history-box > details[data-testid=tr2-history-toggle] > summary { list-style: none; font-size: 12px; font-weight: 700; }
.tr2-history-box > details[data-testid=tr2-history-toggle] > summary::-webkit-details-marker { display: none; }

.tr2-sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.tr2-diagnostics-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.tr2-diagnostics-grid .tr2-history-box > p { margin: 0 0 12px; font-size: 11.5px; }
.tr2-diagnostics-grid code { display: inline-block; max-width: 100%; white-space: nowrap; overflow-x: auto; vertical-align: middle; font-size: 11px; }
@media(max-width:780px) { .tr2-diagnostics-grid { grid-template-columns: 1fr; } }

.tr2-advanced-symbol { width: 31px; height: 31px; flex: none; display: grid; place-items: center; border-radius: 8px; background: #eef3fc; color: #5479bd; }
.tr2-history-box { position: relative; }
.tr2-history-box > .tr2-attempt-detail { margin: 0; }
.tr2-history-box > .tr2-attempt-detail > summary { position: absolute; top: 12px; right: 15px; margin: 0; list-style: none; cursor: pointer; font-weight: 700; }
.tr2-history-box > .tr2-attempt-detail > summary::-webkit-details-marker { display: none; }
</style>
