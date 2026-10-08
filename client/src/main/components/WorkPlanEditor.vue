<template>
  <div ref="rootRef" class="card md-preview-card wp-editor" :class="`wp-layout-${layoutTier}`">
    <div class="card-hd">
      <span class="card-title">
        <span class="doc-tag c-WP" style="font-size:.68rem; padding:2px 5px; margin-right:4px;">WP</span>
        {{ t('main.work_plan.title') }}
        <span v-if="docReviewStatus" class="wp-status-pill" :class="`wp-status-${docReviewStatus}`">
          {{ statusLabel }}
        </span>
      </span>
      <!-- Mockup xc32frrg screen 1: the card header carries only [view raw] / [save].
           [Load AI Suggestion] sits in the table toolbar and [Fill Into Continuous Task] in the
           document action bar. -->
      <div class="card-actions">
        <button
          class="btn btn-secondary btn-sm"
          type="button"
          :disabled="loading || dirty || downloading || hasPendingCapabilityWarning || (!plan && !unreadable?.raw) || downloadBlockedByUnreadable"
          :title="dirty ? t('main.work_plan.upload_needs_save') : undefined"
          @click="downloadWorkPlan"
        >
          <AppIcon name="download-simple" /> {{ t('main.work_plan.download') }}
        </button>
        <button
          class="btn btn-secondary btn-sm"
          type="button"
          :disabled="loading || !!unreadable || saving || uploading || isLocked || dirty || hasPendingCapabilityWarning"
          :title="dirty ? t('main.work_plan.upload_needs_save') : (isLocked ? lockedHint : undefined)"
          @click="triggerUpload"
        >
          <AppIcon name="upload-simple" /> {{ uploading ? t('main.work_plan.uploading') : t('main.work_plan.upload') }}
        </button>
        <input
          ref="workPlanFileInput"
          type="file"
          accept=".json,application/json"
          hidden
          @change="onWorkPlanFileSelected"
        />
        <button class="btn btn-secondary btn-sm" type="button" :disabled="loading || (!plan && !unreadable?.raw)" @click="rawViewOpen = true">
          <AppIcon name="code" /> {{ t('main.work_plan.raw_view') }}
        </button>
        <button
          class="btn btn-primary btn-sm"
          type="button"
          :disabled="loading || !!unreadable || saving || isLocked || hasPendingCapabilityWarning"
          @click="save"
        >
          <AppIcon name="floppy-disk" /> {{ saving ? t('main.work_plan.saving') : t('main.work_plan.save') }}
        </button>
      </div>
    </div>

    <div class="card-bd wp-body">
      <div v-if="loading" class="wp-loading">{{ t('common.loading') }}</div>

      <!-- P0009 §4.5: an unreadable canonical file never gets forced into a table. -->
      <div v-else-if="unreadable" class="wp-unreadable">
        <AppIcon name="warning-circle" class="wp-unreadable-icon" />
        <p class="wp-unreadable-title">{{ t('main.work_plan.unreadable_title') }}</p>
        <p class="wp-unreadable-desc">{{ unreadable.message }}</p>
        <p class="wp-unreadable-detail">{{ unreadable.detail }}</p>
        <div v-if="unreadable.revisions?.length" class="wp-unreadable-revisions">
          <p class="wp-unreadable-revisions-title">{{ t('main.work_plan.revisions_title') }}</p>
          <ul>
            <li v-for="rev in unreadable.revisions" :key="rev.revision_no" class="wp-revision-row">
              <span>r{{ rev.revision_no }} — {{ rev.created_by }} · {{ rev.created_at }}</span>
              <!-- HEAD (main) had this button as one line, kept verbatim for provenance —
                   <button class="btn btn-outline btn-sm" type="button" :disabled="!rev.restorable || restoringRevision !== null || isLocked" :title="rev.restorable ? undefined : t(`main.work_plan.restore_unavailable_${rev.restore_unavailable_reason || 'unknown'}`)" @click="restoreRevision(rev.revision_no)">
                   — the same class/:disabled/:title/@click below, split across lines with wp-restore-btn, :class, :aria-label and a restoring-in-progress label added. -->
              <button
                type="button"
                class="btn btn-outline btn-sm wp-restore-btn"
                :class="{ 'wp-restore-unavailable': !rev.restorable }"
                :disabled="!rev.restorable || restoringRevision !== null || isLocked"
                :title="rev.restorable ? undefined : t(`main.work_plan.restore_unavailable_${rev.restore_unavailable_reason || 'unknown'}`)"
                :aria-label="rev.restorable ? t('main.work_plan.restore_revision') : undefined"
                @click="restoreRevision(rev.revision_no)"
              >
                {{ restoringRevision === rev.revision_no
                    ? t('main.work_plan.restoring')
                    : (rev.restorable ? t('main.work_plan.restore_revision') : t('main.work_plan.restore_unavailable')) }}
              </button>
            </li>
          </ul>
        </div>
        <p v-else class="wp-unreadable-no-baseline">
          {{ t('main.work_plan.legacy_no_baseline') }}
        </p>
        <p v-if="restoreError" class="wp-restore-error" role="alert">{{ restoreError }}</p>
        <pre v-if="unreadable.raw !== null" class="wp-unreadable-raw">{{ unreadable.raw }}</pre>
      </div>

      <template v-else-if="plan">
        <div class="wp-toolbar">
          <span class="wp-toolbar-spacer"></span>
          <button
            class="btn btn-outline btn-sm"
            type="button"
            :disabled="loading || !!unreadable || aiSuggesting || dirty || isLocked"
            :title="aiRunLocked ? lockedHint : (dirty ? t('main.work_plan.ai_needs_save') : undefined)"
            @click="aiScopeOpen = true"
          >
            <AppIcon name="robot" /> {{ aiSuggesting ? t('main.work_plan.ai_filling') : t('main.work_plan.ai_suggest') }}
          </button>
        </div>

        <p v-if="isLocked" class="wp-locked-hint">
          <AppIcon name="lock" /> {{ lockedHint }}
        </p>
        <p v-else-if="docReviewStatus === 'pending_review'" class="wp-review-hint">
          {{ t('main.work_plan.review_pending_hint') }}
        </p>

        <!-- 0403 NR0004 F5 — makes sure unsaved edits are not silently erased by the AI fill.
             The AI fill reads the server's canonical copy and, when done, swaps the whole
             screen for that copy. So quantities, assignments, and notes just entered by hand
             disappeared with no warning. Forcing a save here first keeps the plan the AI
             reads and the plan on screen identical (same reason as F6). -->
        <div v-if="dirty" class="wp-dirty-banner">
          <AppIcon name="warning-circle" />
          <span>{{ t('main.work_plan.unsaved_changes') }}</span>

        </div>

        <div v-if="conflict" class="wp-conflict-banner">
          <AppIcon name="warning-circle" />
          <span>{{ t('main.work_plan.save_conflict_message', { who: conflict.updatedBy ?? '?', when: conflict.updatedAt ?? '' }) }}</span>
          <button type="button" class="btn btn-outline btn-sm" @click="reload">{{ t('main.work_plan.reload') }}</button>
        </div>
        <!-- NR0003 rev2 — the upload PUT already committed a new revision server-side; this
             screen just failed to confirm it. Editing/saving stays blocked (isLocked) until an
             explicit reload succeeds, so a stale-looking save can never overwrite it. -->
        <div v-if="staleAfterUpload" class="wp-conflict-banner">
          <AppIcon name="warning-circle" />
          <span>{{ t('main.work_plan.upload_sync_failed') }}</span>
          <button type="button" class="btn btn-outline btn-sm" @click="reload">{{ t('main.work_plan.reload') }}</button>
        </div>
        <!-- flowgate.default.0576 TR0005 rev4 — PUT /work-plan refuses to apply a body at all
             (422 provider_capability_confirmation_required) when a T/TR step's provider cannot
             modify source or run tests, or is unknown to this project. The body stays unapplied
             until the person reviews the findings and explicitly confirms; [취소] drops it. -->
        <div v-if="capabilityWarnings.length" class="wp-conflict-banner wp-capability-banner" data-test="capability-warning-banner">
          <div class="wp-capability-banner-head">
            <AppIcon name="warning-circle" />
            <span>{{ t('main.work_plan.capability_warning_title') }}</span>
          </div>
          <ul class="wp-capability-list">
            <li v-for="finding in capabilityWarnings" :key="finding.step_key">
              <strong>{{ finding.step_key }} · {{ finding.step_type }}</strong>
              — {{ finding.provider_name || finding.provider_id || '?' }}:
              {{ (finding.missing_capabilities || []).join(', ') }}
            </li>
          </ul>
          <div class="wp-capability-banner-actions">
            <button type="button" class="btn btn-warning btn-sm" :disabled="confirmingCapability" @click="confirmCapabilityWarning">
              {{ t('main.work_plan.capability_warning_confirm') }}
            </button>
            <button type="button" class="btn btn-outline btn-sm" :disabled="confirmingCapability" @click="cancelCapabilityWarning">
              {{ t('main.work_plan.capability_warning_cancel') }}
            </button>
          </div>
        </div>
        <div v-if="topLevelErrors.length" class="wp-error-banner">
          <AppIcon name="warning-circle" />
          <span>{{ topLevelErrors.join(' ') }}</span>
        </div>

        <!-- ① Quantities -->
        <section class="wp-section">
          <div class="wp-section-hd">
            <span class="wp-step-no-badge">1</span>
            <AppIcon name="hash" class="wp-section-ico" />
            <span class="wp-section-title">{{ t('main.work_plan.section_quantities_title') }}</span>
            <span class="wp-section-totals">{{ t('main.work_plan.totals_line', { design: totals.design_sheets, work: totals.work_sets }) }}</span>
          </div>
          <div class="wp-qty-grid">
            <div
              v-for="code in renderedCountedTypes"
              :key="code"
              class="wp-qty-card"
              :class="{ zero: (plan.quantities[code]?.count ?? 0) === 0 }"
            >
              <span class="wp-qty-tags">
                <span class="doc-tag" :class="`c-${code}`" style="font-size:.62rem; padding:1px 5px;">{{ code }}</span>
                <span v-if="pairOf(code)" class="doc-tag" :class="`c-${pairOf(code)}`" style="font-size:.62rem; padding:1px 5px;">{{ pairOf(code) }}</span>
              </span>
              <span class="wp-qty-body">
                <span class="wp-qty-name">{{ qtyCardName(code) }}</span>
                <span class="wp-qty-unit">{{ unitLabel(code) }}</span>
              </span>
              <span class="wp-qty-stepper">
                <button type="button" class="wp-stepper-btn" :disabled="isLocked" @click="setQuantity(code, (plan.quantities[code]?.count ?? 0) - 1)">−</button>
                <span class="wp-qty-value" :class="{ zero: (plan.quantities[code]?.count ?? 0) === 0 }">{{ plan.quantities[code]?.count ?? 0 }}</span>
                <button type="button" class="wp-stepper-btn" :disabled="isLocked" @click="setQuantity(code, (plan.quantities[code]?.count ?? 0) + 1)">+</button>
              </span>
            </div>
          </div>
        </section>

        <!-- ② per-step provider · one-line note -->
        <section class="wp-section">
          <div class="wp-section-hd">
            <span class="wp-step-no-badge">2</span>
            <AppIcon name="users" class="wp-section-ico" />
            <span class="wp-section-title">{{ t('main.work_plan.section_steps_title') }}</span>
            <span v-if="unassignedStepCount > 0" class="wp-section-missing">
              {{ t('main.work_plan.summary_unassigned', { n: unassignedStepCount }) }}
            </span>
            <span class="wp-section-totals">{{ t('main.work_plan.steps_total', { n: plan.steps.length }) }}</span>
          </div>

          <div class="wp-defaults-row">
            <span class="wp-defaults-label">{{ t('main.work_plan.defaults_label') }}</span>
            <AiProviderSelect :providers="providerOptionsWithUnassigned" :model-value="plan.defaults.provider_id ?? ''" :disabled="isLocked" hide-label hide-icon compact @update:model-value="(v) => setDefaultProvider(v || null)" />
            <span class="wp-note-field">
              <input :value="plan.defaults.note" type="text" class="wp-defaults-note" :class="{ 'is-over-limit': plan.defaults.note.length > noteMaxChars }" :placeholder="t('main.work_plan.defaults_note_placeholder')" :disabled="isLocked" @input="(e) => setDefaultNote((e.target as HTMLInputElement).value)" />
              <small class="wp-note-count" :class="{ 'is-over-limit': plan.defaults.note.length > noteMaxChars }">
                {{ plan.defaults.note.length > noteMaxChars
                  ? t('main.work_plan.note_char_over', { current: plan.defaults.note.length, max: noteMaxChars })
                  : t('main.work_plan.note_char_count', { current: plan.defaults.note.length, max: noteMaxChars }) }}
              </small>
            </span>
            <button type="button" class="btn btn-outline btn-sm" :disabled="isLocked" @click="applyDefaults">{{ t('main.work_plan.apply_to_all') }}</button>
          </div>

          <div class="wp-step-head">
            <span>{{ t('main.work_plan.col_step') }}</span><span>{{ t('main.work_plan.col_type') }}</span>
            <span>{{ t('main.work_plan.col_document') }}</span><span>{{ t('main.work_plan.col_provider') }}</span>
            <span>{{ t('main.work_plan.col_review') }}</span><span>{{ t('main.work_plan.col_note') }}</span>
            <span class="wp-col-instr-head">{{ t('main.work_plan.col_instruction') }}</span>
          </div>
          <div class="wp-step-list" :class="{ 'is-card-dragging': draggedCardId !== null }">
            <div v-if="plan.steps.length === 0" class="step-empty">{{ t('main.work_plan.empty_all_zero') }}</div>
            <!-- flowgate.default.0649 T#3 (NR0003 §5.3 / O7) — the list is drawn by CARD: a single
                 step, or a set's instruction + result pair. Cards move as a whole (drag the grip,
                 or the ▲▼ buttons), and keys/ordinals are renumbered by order afterwards; card_id
                 and every value (provider, note, review, pre-instruction) travel with the card.
                 A card that already started in the workflow is pinned: no grip, no ▲▼, and no
                 other card can be put in front of it. -->
            <div
              v-for="(card, cardIdx) in cards"
              v-else
              :key="card.id"
              class="wp-card"
              :class="{
                'is-started': card.started,
                'is-dragging': draggedCardId === card.id,
                'drag-over': draggedCardId !== null && dragOverCardId === card.id && draggedCardId !== card.id,
              }"
              data-test="wp-card"
              :data-card-type="card.type"
              @dragover.prevent="onCardDragOver(card)"
              @drop.prevent="onCardDrop(cardIdx)"
            >
              <div class="wp-card-grip">
                <span
                  class="wp-card-handle"
                  :class="{ 'is-disabled': !canMoveCard(card) }"
                  :draggable="canMoveCard(card)"
                  :title="card.started ? t('main.work_plan.card_started_locked') : t('main.work_plan.card_drag_hint')"
                  :data-test="card.started ? 'card-started-lock' : 'card-drag-handle'"
                  @dragstart="onCardDragStart($event, card)"
                  @dragend="onCardDragEnd"
                >
                  <AppIcon :name="card.started ? 'lock' : 'dots-six-vertical'" />
                </span>
                <button
                  type="button"
                  class="wp-card-move"
                  :disabled="!canMoveCardBy(cardIdx, -1)"
                  :title="t('main.work_plan.card_move_up')"
                  :aria-label="t('main.work_plan.card_move_up')"
                  data-test="card-move-up"
                  @click="moveCardBy(cardIdx, -1)"
                >
                  <AppIcon name="caret-up" />
                </button>
                <button
                  type="button"
                  class="wp-card-move"
                  :disabled="!canMoveCardBy(cardIdx, 1)"
                  :title="t('main.work_plan.card_move_down')"
                  :aria-label="t('main.work_plan.card_move_down')"
                  data-test="card-move-down"
                  @click="moveCardBy(cardIdx, 1)"
                >
                  <AppIcon name="caret-down" />
                </button>
              </div>
              <div class="wp-card-rows">
                <template v-for="step in card.steps" :key="rowKeyOf(card, step)">
                  <div class="wp-step-row" :class="{ 'is-card-result': step.pair_role === 'result', 'is-locked': step.locked, 'wp-row-error': stepErrors[rowKeyOf(card, step)] }">
                    <span v-if="step.locked" class="wp-step-no">{{ t('main.work_plan.step_no', { n: rowNo(step) }) }}</span>
                    <button
                      v-else
                      type="button"
                      class="wp-step-no wp-step-no-btn"
                      :title="t('main.work_plan.drawer_open_hint')"
                      data-test="step-name-toggle"
                      @click="toggleDrawer(rowKeyOf(card, step), step)"
                    >
                      {{ t('main.work_plan.step_no', { n: rowNo(step) }) }}
                      <AppIcon name="caret-right" class="wp-step-caret" :class="{ open: openDrawerRow === rowKeyOf(card, step) }" />
                    </button>
                    <span class="doc-tag" :class="`c-${step.type}`">{{ step.type }}</span>
                    <span class="wp-step-label">{{ stepDocName(step) }} <small>{{ stepDocQuantity(step) }}</small></span>
                    <select v-if="step.locked" class="prov-select" disabled><option>{{ t('main.work_plan.locked_note') }}</option></select>
                    <AiProviderSelect v-else :providers="providerOptionsWithUnassigned" :model-value="step.provider_id ?? ''" :disabled="isLocked" hide-label hide-icon compact @update:model-value="(v) => setStepProvider(step.key, v || null)" />
                    <button
                      type="button"
                      class="wp-review-pill"
                      :class="{ 'has-review': !step.locked && (step.review_count ?? 0) !== 0, 'is-locked': step.locked }"
                      :disabled="step.locked"
                      :title="t('main.work_plan.drawer_open_hint')"
                      data-test="review-pill-toggle"
                      @click="toggleDrawer(rowKeyOf(card, step), step)"
                    >
                      <span class="wp-review-pill-text">{{ step.locked ? '—' : reviewSummaryText(step) }}</span>
                    </button>
                    <span class="wp-note-field">
                      <input class="wp-step-msg" :class="{ 'is-ai': step.origin === 'ai_suggested', 'is-over-limit': (step.note ?? '').length > noteMaxChars }" type="text" :placeholder="t('main.work_plan.note_placeholder')" :value="step.locked ? '' : (step.note ?? '')" :disabled="step.locked || isLocked" @input="(e) => setStepNote(step.key, (e.target as HTMLInputElement).value)" />
                      <small v-if="!step.locked" class="wp-note-count" :class="{ 'is-over-limit': (step.note ?? '').length > noteMaxChars }">
                        {{ (step.note ?? '').length > noteMaxChars
                          ? t('main.work_plan.note_char_over', { current: (step.note ?? '').length, max: noteMaxChars })
                          : t('main.work_plan.note_char_count', { current: (step.note ?? '').length, max: noteMaxChars }) }}
                      </small>
                    </span>
                    <button
                      type="button"
                      class="wp-instr-icon"
                      :class="{ 'has-value': !step.locked && instrEligible(step) && stepHasInstructionValue(step), 'is-locked': step.locked || !instrEligible(step) }"
                      :disabled="step.locked"
                      :title="step.locked ? t('main.work_plan.instr_not_eligible', { reason: t('main.work_plan.instr_excluded_server_assembled') }) : (instrEligible(step) ? t('main.work_plan.drawer_open_hint') : t('main.work_plan.instr_not_eligible', { reason: t('main.work_plan.instr_excluded_report') }))"
                      data-test="instr-icon-toggle"
                      @click="toggleDrawer(rowKeyOf(card, step), step)"
                    >
                      <AppIcon name="pencil-simple" />
                    </button>
                    <div v-if="stepErrors[rowKeyOf(card, step)]?.length" class="wp-step-errors" role="alert">
                      <span v-for="(msg, i) in stepErrors[rowKeyOf(card, step)]" :key="i" class="wp-step-error-msg">{{ msg }}</span>
                    </div>
                  </div>
                  <div v-if="!step.locked && openDrawerRow === rowKeyOf(card, step)" class="wp-step-drawer" data-test="step-drawer">
                    <div class="wp-drawer-block">
                      <div class="wp-drawer-block-hd">
                        <AppIcon name="magnifying-glass" />
                        <span class="wp-drawer-block-title">{{ t('main.work_plan.drawer_review_title', { n: rowNo(step) }) }}</span>
                      </div>
                      <div class="wp-drawer-review-row">
                        <select
                          class="wp-drawer-select wp-drawer-review-count"
                          :class="{ 'is-active': (step.review_count ?? 0) !== 0 }"
                          :disabled="isLocked"
                          :value="step.review_count"
                          data-test="drawer-review-count"
                          @change="(e) => setStepReviewCount(step.key, Number((e.target as HTMLSelectElement).value))"
                        >
                          <option v-for="choice in reviewCountChoices" :key="choice" :value="choice">{{ reviewCountLabel(choice) }}</option>
                        </select>
                        <AiProviderSelect
                          :providers="reviewerOptionsWithDefault"
                          :model-value="step.reviewer_provider_id ?? ''"
                          :disabled="isLocked || (step.review_count ?? 0) === 0"
                          hide-label hide-icon compact
                          @update:model-value="(v) => setStepReviewer(step.key, v || null)"
                        />
                      </div>
                    </div>
                    <div class="wp-drawer-divider"></div>
                    <div v-if="instrEligible(step)" class="wp-drawer-block">
                      <div class="wp-drawer-block-hd">
                        <AppIcon name="file-text" />
                        <span class="wp-drawer-block-title">{{ t('main.work_plan.drawer_instruction_title', { n: rowNo(step) }) }}</span>
                      </div>
                      <div class="wp-drawer-instr-block">
                        <span class="wp-drawer-mode-label"><AppIcon name="pencil-simple" /> {{ t('main.work_plan.instr_manual_label') }}</span>
                        <div class="wp-drawer-textarea-wrap">
                          <textarea
                            class="wp-drawer-textarea"
                            :value="step.pre_instruction_text ?? ''"
                            :disabled="isLocked"
                            :placeholder="t('main.work_plan.instr_placeholder')"
                            data-test="drawer-instr-text"
                            @input="(e) => setStepInstruction(step.key, (e.target as HTMLTextAreaElement).value)"
                          ></textarea>
                          <span class="wp-drawer-count" :class="{ 'is-over-limit': (step.pre_instruction_text ?? '').length > preInstructionMaxChars }">
                            {{ t('main.work_plan.instr_char_count', { current: (step.pre_instruction_text ?? '').length, max: preInstructionMaxChars }) }}
                          </span>
                        </div>
                      </div>
                      <div class="wp-drawer-instr-block wp-drawer-file-row">
                        <span class="wp-drawer-mode-label"><AppIcon name="paperclip" /> {{ t('main.work_plan.instr_attach_label') }}</span>
                        <label class="btn btn-outline btn-sm wp-drawer-file-btn" :class="{ 'is-disabled': isLocked || instrUploadingKey === rowKeyOf(card, step) }">
                          <AppIcon name="upload-simple" /> {{ instrUploadingKey === rowKeyOf(card, step) ? t('main.work_plan.instr_attach_uploading') : t('main.work_plan.instr_attach_choose') }}
                          <input type="file" class="wp-drawer-file-input" hidden :disabled="isLocked || instrUploadingKey === rowKeyOf(card, step)" data-test="drawer-instr-file-input" @change="(e) => onInstrFileSelected(step, rowKeyOf(card, step), e)" />
                        </label>
                        <span class="wp-drawer-file-name" :class="{ 'has-file': !!step.pre_instruction_attachment }" data-test="drawer-instr-file-name">
                          {{ step.pre_instruction_attachment ? step.pre_instruction_attachment.original_filename : t('main.work_plan.instr_attach_none') }}
                        </span>
                        <button
                          v-if="step.pre_instruction_attachment"
                          type="button" class="wp-drawer-file-remove" :disabled="isLocked"
                          :title="t('main.work_plan.instr_attach_remove')"
                          data-test="drawer-instr-file-remove"
                          @click="removeStepInstructionAttachment(step.key)"
                        >
                          <AppIcon name="x" />
                        </button>
                      </div>
                    </div>
                    <p v-else class="wp-drawer-excluded">
                      <AppIcon name="info" /> {{ t('main.work_plan.instr_not_eligible', { reason: t('main.work_plan.instr_excluded_report') }) }}
                    </p>
                    <div class="wp-drawer-actions">
                      <button type="button" class="btn btn-outline btn-sm" data-test="drawer-close" @click="closeDrawer">{{ t('main.work_plan.drawer_close') }}</button>
                      <button type="button" class="btn btn-primary btn-sm" :disabled="saving || isLocked || hasPendingCapabilityWarning" data-test="drawer-save" @click="saveFromDrawer(rowKeyOf(card, step), step)">
                        {{ saving ? t('main.work_plan.saving') : t('main.work_plan.drawer_save') }}
                      </button>
                    </div>
                  </div>
                </template>
              </div>
            </div>
            <div
              v-if="draggedCardId !== null"
              class="wp-card-drop-end"
              :class="{ 'drag-over': dragOverCardId === CARD_DROP_END }"
              data-test="card-drop-end"
              @dragover.prevent="dragOverCardId = CARD_DROP_END"
              @drop.prevent="onCardDrop(cards.length)"
            >
              {{ t('main.work_plan.card_drop_end') }}
            </div>
          </div>

        </section>

        <!-- Mockup xc32frrg screen 1 — 3 quantity cards -->
        <div class="wp-sum-cards">
          <div class="wp-sum-card">
            <div class="wp-sum-label"><AppIcon name="compass-tool" /> {{ t('main.work_plan.sum_design_label') }}</div>
            <div class="wp-sum-value">{{ totals.design_sheets }}<small>{{ t('main.work_plan.unit_sheet_short') }}</small></div>
            <div class="wp-sum-desc">{{ t('main.work_plan.sum_design_desc') }}</div>
          </div>
          <div class="wp-sum-card">
            <div class="wp-sum-label"><AppIcon name="stack" /> {{ t('main.work_plan.sum_work_label') }}</div>
            <div class="wp-sum-value">{{ totals.work_sets }}<small>{{ t('main.work_plan.unit_set_short') }}</small></div>
            <div class="wp-sum-desc">{{ t('main.work_plan.sum_work_desc') }}</div>
          </div>
          <div class="wp-sum-card">
            <div class="wp-sum-label"><AppIcon name="file-text" /> {{ t('main.work_plan.sum_steps_label') }}</div>
            <div class="wp-sum-value">{{ totals.steps }}<small>{{ t('main.work_plan.unit_step_short') }}</small></div>
            <div class="wp-sum-desc">{{ t('main.work_plan.sum_steps_desc') }}</div>
          </div>
        </div>
      </template>
    </div>

    <!-- Raw view overlay (read-only) — P0009 §3.4 / D0007 §6.5.
         flowgate.default.0560 T0018 (4순위, NR0011 원장 ID 45) on the common dialog layer;
         D0008 maps it to `readonly`. It has no footer and gets none (T0018 §2.2-2): its two
         controls always sat in the title bar, so [복사] stays there through DialogHeader's
         `actions` slot and the separate [닫기] button becomes the header X that D0008 §2
         assigns to DialogHeader — the close control keeps its place, it is the common one
         now. `size="lg"` is the same 720px track `.wp-raw-box` had.
         T0018 §2.3-7: like the AI-scope dialog above, this was a non-Teleport
         `position: absolute; inset: 0; z-index: 50` overlay confined to the editor panel and
         is now viewport-wide. See the note in `WorkPlanAiScopeDialog.vue` for why that is
         accepted, and `tests/browser/dialog-local-overlay-geometry.0560.mjs` for the
         before/after coordinates.
         `:close-on-backdrop="false"` stays explicit (T0018 §2.2-3): NR0011 records
         BD=X. `readonly` defaults to `false` too since 0560 T0035, so this now restates
         rather than overrides the table. The `@keydown.escape` binding is gone with the
         overlay div — it never fired (nothing inside was focused, NR0011 §9-5); the common
         stack's single document listener is what closes this now. -->
    <DialogShell
      :open="rawViewOpen"
      variant="readonly"
      size="lg"
      surface="sheet"
      surface-class="work-plan-raw-dialog"
      :close-on-backdrop="false"
      @request-close="rawViewOpen = false"
    >
      <template #header>
        <DialogHeader :title="t('main.work_plan.raw_view_title')" @close="rawViewOpen = false">
          <template #actions>
            <button type="button" class="btn btn-outline btn-sm" @click="copyRaw">
              <AppIcon name="copy" /> {{ t('main.work_plan.copy') }}
            </button>
          </template>
        </DialogHeader>
      </template>
      <template #default>
        <pre class="wp-raw-content">{{ rawJson }}</pre>
      </template>
    </DialogShell>

    <WorkPlanAiScopeDialog
      :visible="aiScopeOpen"
      :busy="aiSuggesting"
      :countable-types="docTypeStore.countableTypes.map((item) => ({ code: item.code, label: item.label }))"
      :steps="scopeSteps"
      :candidates="scopeProviderOptions"
      @close="aiScopeOpen = false"
      @project-map="fetchSuggestion"
      @ai="startAiFill"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRaw, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postFormRequest, postRequest, putRequest } from '@shared/api'
import { renderWpFieldError, type WpFieldError } from '@shared/workPlanErrors'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import WorkPlanAiScopeDialog from './WorkPlanAiScopeDialog.vue'
import type { WorkPlanScope } from './WorkPlanAiScopeDialog.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import { confirm } from '../composables/useDialogStack'
import { useContentLayoutTier } from '../composables/useContentLayoutTier'
import { useToast } from './common/useToast'
import { useDocTypeStore } from '../stores/docTypeStore'
import { useAiProviderStore } from '../stores/aiProvider'
import { useTabsStore } from '../stores/tabs'
import { groupIdFromDocId, useAiInvokeRunsStore } from '../stores/aiInvokeRuns'
import { copyToClipboard } from '../utils/clipboard'

// ── Canonical shape (mirrors flowgate.default.0395 P0009 §2 / L0010 §1-2) ────
interface WPCandidate { provider_id: string; display_name: string | null; group_label: string | null }
interface WPRegisteredProvider { id: string; name: string | null; group_label: string | null }
interface WPProviderStatus { provider_id: string; registered: boolean; current_name: string | null; snapshot_name: string | null; name_changed: boolean }
interface WPAssignmentSummary { provider_id: string; display_name: string; step_count: number }
interface WPQuantity { unit: 'sheet' | 'set'; count: number }
interface WPCapabilityFinding {
  step_key: string
  step_type: string
  provider_id: string | null
  provider_name: string | null
  missing_capabilities: string[]
}
interface WPPreInstructionAttachment {
  doc_id: string
  filename: string
  original_filename: string
  content_sha256: string
}
interface WPStep {
  key: string
  // flowgate.default.0649 T#1/T#3 (NR0003 §5.1) — the card's identity. It never changes when a
  // card moves or is renumbered; a card added here has none until the server assigns one.
  card_id?: string | null
  type: string
  ordinal: number
  pair_key: string | null
  pair_role: 'instruction' | 'result' | 'single'
  provider_id: string | null
  provider_display_name: string | null
  note: string | null
  review_count: number
  reviewer_provider_id: string | null
  reviewer_provider_display_name: string | null
  pre_instruction_text: string | null
  pre_instruction_attachment: WPPreInstructionAttachment | null
  locked: boolean
  locked_reason: string | null
  origin: 'human' | 'ai_suggested' | 'system'
}
interface WPBody {
  wp_version: number
  binding: string
  counted_types: string[]
  quantities: Record<string, WPQuantity>
  provider_candidates: WPCandidate[]
  defaults: { provider_id: string | null; note: string }
  steps: WPStep[]
}

const LOCKED_TYPES = new Set(['TSR'])
const COUNT_MIN = 0
const COUNT_MAX = 20
// The server response limit is canonical; 1000 is compatibility for old mocks.
const noteMaxChars = ref(1000)
// T0010 §5.4 / D0007 §5.1 — same SSOT pattern as noteMaxChars: the server publishes the
// current selectable review counts and the pre-instruction character cap in the WP GET
// response, so a system-setting change never leaves this screen showing a stale list.
const reviewCountChoices = ref<number[]>([0])
const preInstructionMaxChars = ref(20000)
const preInstructionAttachmentMaxBytes = ref(0)
// D0007 §6.1-§6.2 — the three entry points (step name / review pill / instruction icon) open
// the SAME drawer for that step; opening one closes any other step's drawer.
// 0649 T#3 (NR0003 §5.3): keyed by the ROW identity (card + role), never by step.key — a key is
// renumbered whenever a card moves, and a key-keyed drawer would jump to another card.
const openDrawerRow = ref<string | null>(null)
const instrUploadingKey = ref<string | null>(null)

const props = defineProps<{
  docId: string
  projectId: string | null
  /** 0424 TR0005 rev2 — the document panel's AI-run lock, passed down exactly like
   *  DocHeader / AttachmentCard / MdViewer already receive it. Until now this editor
   *  was the one card in the column that took no lock at all, so every control on the
   *  work plan (save · quantity stepper · provider · note · apply-to-all · AI suggest) stayed clickable
   *  through an AI run and only answered with a 423 toast. */
  readOnly?: boolean
}>()
const { t, locale } = useI18n()
const { showToast } = useToast()
const docTypeStore = useDocTypeStore()
const aiProviderStore = useAiProviderStore()
const tabsStore = useTabsStore()

const rootRef = ref<HTMLElement | null>(null)
const loading = ref(true)
const saving = ref(false)
const aiSuggesting = ref(false)
const aiScopeOpen = ref(false)
const aiRunId = ref<string | null>(null)
const rawViewOpen = ref(false)
const downloading = ref(false)
const uploading = ref(false)
const restoringRevision = ref<number | null>(null)
const workPlanFileInput = ref<HTMLInputElement | null>(null)

const plan = ref<WPBody | null>(null)
const serverRegisteredProviders = ref<WPRegisteredProvider[]>([])
const serverRegisteredProvidersKnown = ref(false)
const providerStatuses = ref<WPProviderStatus[]>([])
const assignmentSummary = ref<WPAssignmentSummary[]>([])
const unassignedStepCount = ref(0)
const revisionNo = ref(0)
const docReviewStatus = ref<string | null>(null)
// 0403 NR0004 F5 — whether there is an unsaved edit. Because the AI fill swaps the screen
// for the server's canonical copy, the AI cannot be handed control while this is true.
const dirty = ref(false)
// 0403 NR0004 F7 — the server judges editability and carries it in the response. If the
// screen instead locks based only on approval status, you get a plan the server allows but
// the screen locks (and the reverse).
const editable = ref(true)
const editLockedReason = ref<string | null>(null)
const totals = ref({ design_sheets: 0, work_sets: 0, steps: 0 })
const conflict = ref<{ updatedBy: string | null; updatedAt: string | null } | null>(null)
// Set when an upload's PUT committed a new revision but the immediate canonical refetch failed
// (network blip, 5xx). The screen still shows the pre-upload plan under the now-stale
// revisionNo, so editing/saving must stay blocked (via isLocked) until reload() confirms it —
// otherwise a save from this screen would carry the new revision number over stale content and
// silently overwrite the just-uploaded plan without a conflict.
const staleAfterUpload = ref(false)
// PUT /work-plan can refuse to apply a body at all (422 provider_capability_confirmation_required)
// when a T/TR step's provider cannot modify source or run tests, or is unknown to the project.
// The body is held here, unapplied, until the user explicitly confirms — a manual save and a
// JSON upload both go through persistPlanBody, so both land here the same way.
const capabilityWarnings = ref<WPCapabilityFinding[]>([])
const pendingCapabilityBody = ref<WPBody | null>(null)
const pendingCapabilitySource = ref<'save' | 'upload' | null>(null)
const confirmingCapability = ref(false)
// T0007 §1.2 — the raw {code, params, loc, key, msg} the server sends is the SSOT; the
// displayed strings below are computed from it so a locale switch re-renders them without a
// new request, instead of freezing whatever `msg` text the save-time locale produced.
const topLevelErrorRecords = ref<WpFieldError[]>([])
// Keyed by row identity (rowKeyOf), not by the step key the server named: a card moved after a
// failed save keeps its own errors (0649 T#3, NR0003 §10-4).
const stepErrorRecords = ref<Record<string, WpFieldError[]>>({})
const topLevelErrors = computed(() => topLevelErrorRecords.value.map((err) => renderWpFieldError(err, t)))
const stepErrors = computed(() => {
  const rendered: Record<string, string[]> = {}
  for (const [key, errs] of Object.entries(stepErrorRecords.value)) {
    rendered[key] = errs.map((err) => renderWpFieldError(err, t))
  }
  return rendered
})
// HEAD (main) had this as one inline type, kept verbatim for provenance —
// const unreadable = ref<{ message: string; detail: string; raw: string | null; revision_no: number; revisions: { revision_no: number; created_by: string; created_at: string; restorable: boolean; restore_unavailable_reason: string | null }[] } | null>(null)
// — every one of those fields is still here, just split into named interfaces below
// (created_by/created_at widened to `string | null`) plus the new restoreError.
interface WPRevisionCandidate {
  revision_no: number
  created_by: string | null
  created_at: string | null
  restorable: boolean
  restore_unavailable_reason: string | null
}
interface WPUnreadable {
  message: string
  detail: string
  raw: string | null
  // The document's own current revision — distinct from each recovery candidate's
  // `revision_no` in WPRevisionCandidate above, but same field name (matches the
  // server's wire format); the two are only ever reached via different object paths
  // (`unreadable.value.revision_no` vs `unreadable.value.revisions[i].revision_no`).
  revision_no: number
  revisions: WPRevisionCandidate[]
}

const unreadable = ref<WPUnreadable | null>(null)
const restoreError = ref<string | null>(null)


// D0007 §3.2 decision 4: a value-bearing step that a lower quantity would drop stays
// recoverable until the plan is actually saved.
// 0649 T#3 (NR0003 §5.3): what a quantity decrease removes is a whole CARD (its steps, its
// card_id and its values), stacked per card type. Raising the quantity again in the same edit
// session brings the most recently removed card back with its original card_id — it is a card
// of the last saved body, so it is the same card. Cleared on save/reload: a card deleted by a
// save is gone, and re-adding it later makes a new card (§3.8).
const restoreBuffer = new Map<string, Array<{ steps: WPStep[]; index: number }>>()

// 0649 T#3 (NR0003 O7): cards that already started in the workflow, from the read view's
// step_execution_status. They are pinned in place and cannot be removed by a quantity decrease.
const startedCardIds = ref<Set<string>>(new Set())

// 0424 B0001 / TR0005 rev2 — "AI실행중에 버튼들이 안눌리게 하던가 없애야지 토스트 띄우면
// 다인가?". This group's own [Load AI Suggestion] starts a run against this very WP document
// (action_scope 'work_plan_fill'), and every mutating work-plan route is behind the group's
// AI lease, so from that moment the server answers 423 GROUP_AI_RUN_LOCKED. The editor has
// to already read as locked instead of letting the click through to a toast. Same predicate
// the explorers use (GroupTreeNode / FileExplorer): the run registry OR a live lease.
const aiInvokeRunsStore = useAiInvokeRunsStore()
const wpGroupId = computed(() => groupIdFromDocId(props.docId))
const groupBusy = computed(() => {
  const groupId = wpGroupId.value
  return !!groupId && (
    aiInvokeRunsStore.isGroupRunning(groupId)
    || aiInvokeRunsStore.isGroupInlineVisible(groupId)
  )
})
// A lease can outlive this tab's own view of the run (0401 NR0003 SS3), so ask the lease
// endpoint too. Single-flight + generation-guarded inside the store, and it only ever adds
// a lock, so it cannot make a control flicker back to enabled. This tab is long-lived and
// never remounts around a run, so the phase is watched as well: without it a lease read as
// live at mount would keep the editor locked after the run had already ended.
watch(
  () => [wpGroupId.value, aiInvokeRunsStore.runsByGroup[wpGroupId.value ?? '']?.phase] as const,
  ([groupId]) => {
    if (groupId) void aiInvokeRunsStore.refreshGroupLease(groupId)
  },
  { immediate: true },
)

const aiRunLocked = computed(() => props.readOnly === true || groupBusy.value)
const isLocked = computed(() => !editable.value || aiRunLocked.value || staleAfterUpload.value)
const hasPendingCapabilityWarning = computed(() => capabilityWarnings.value.length > 0)

// [Download] is blocked when there is nothing to download. main's original guard —
// `!plan.value && !unreadable.value?.raw` — covers the ordinary case; 0599 widened it
// with `!!unreadable.value && unreadable.value.raw === null` for the case a stale
// `plan.value` from before the document went unreadable hides main's check. Kept as two
// named conditions rather than collapsed into one boolean so both authors' checks read
// independently.
const downloadBlockedByUnreadable = computed(() => {
  const mainGuard = !plan.value && !unreadable.value?.raw
  const wp0599Guard = !!unreadable.value && unreadable.value.raw === null
  return mainGuard || wp0599Guard
})

const lockedHint = computed(() =>
  aiRunLocked.value
    ? t('main.review_action_bar.ai_running_hint')
    : staleAfterUpload.value
      ? t('main.work_plan.upload_sync_failed')
      : editLockedReason.value === 'final_approved'
        ? t('main.work_plan.locked_after_final_approval')
        : t('main.work_plan.locked_by_status'),
)

// The scope dialog is a write surface too — a run that starts while it is open must not
// leave [Fill from Project Map] / [Fill with AI] sitting there ready to fire.
watch(aiRunLocked, (locked) => {
  if (locked) aiScopeOpen.value = false
})

function markDirty() {
  dirty.value = true
}

const statusLabel = computed(() => {
  const key = `main.work_plan.status_${docReviewStatus.value}`
  const label = t(key)
  return label === key ? (docReviewStatus.value ?? '') : label
})

function unitLabel(code: string): string {
  const unit = plan.value?.quantities[code]?.unit
  return unit === 'set' ? t('main.work_plan.unit_set') : t('main.work_plan.unit_sheet')
}

/**
 * A set row is named after the pair, not after its instruction document —
 * survey / work / test, the way mockup xc32frrg screen 1 labels the quantity cards.
 */
function qtyCardName(code: string): string {
  if (plan.value?.quantities[code]?.unit !== 'set') return docTypeStore.getLabel(code)
  return docTypeStore.getSetName(code)
}

// L0010 §2.9: editor and apply preview share the same measured-width classifier.
const { layoutTier } = useContentLayoutTier(rootRef)
onMounted(() => { void fetchPlan() })

// ── Type ordering / expansion (mirrors L0010 §2.1 / §2.2) ────────────────────

function typeOrder(codes: string[]): string[] {
  const registryOrder = docTypeStore.countableTypes.map((item) => item.code)
  const wanted = new Set(codes)
  const ordered = registryOrder.filter((code) => wanted.has(code))
  const remainder = codes.filter((code) => !ordered.includes(code)).sort()
  return [...ordered, ...remainder]
}

const renderedCountedTypes = computed(() => {
  if (!plan.value) return []
  return typeOrder(Array.from(new Set([
    ...plan.value.counted_types,
    ...docTypeStore.countableTypes.map((item) => item.code),
  ])))
})

function pairOf(code: string): string | undefined {
  return docTypeStore.items.find((item) => item.code === code)?.pair_code
}

function makeKey(type: string, ordinal: number): string {
  return `${type}#${ordinal}`
}

function makeStep(type: string, ordinal: number, pairKey: string | null, pairRole: WPStep['pair_role']): WPStep {
  const locked = LOCKED_TYPES.has(type)
  return {
    key: makeKey(type, ordinal),
    type,
    ordinal,
    pair_key: pairKey,
    pair_role: pairRole,
    provider_id: null,
    provider_display_name: null,
    note: null,
    review_count: 0,
    reviewer_provider_id: null,
    reviewer_provider_display_name: null,
    pre_instruction_text: null,
    pre_instruction_attachment: null,
    locked,
    locked_reason: locked ? 'server_assembled' : null,
    origin: locked ? 'system' : 'human',
  }
}

function hasValue(step: WPStep): boolean {
  if (step.locked) return false
  if (step.provider_id) return true
  if ((step.note ?? '').trim() !== '') return true
  // D0007 §5.5 — a step carrying only a new field (review/reviewer/pre-instruction) is not
  // an empty step; the quantity-lowering removal warning must fire for it too.
  if ((step.review_count ?? 0) !== 0) return true
  if (step.reviewer_provider_id) return true
  if ((step.pre_instruction_text ?? '').trim() !== '') return true
  if (step.pre_instruction_attachment) return true
  return false
}

// ── Cards (flowgate.default.0649 T#3 / NR0003 §5.3) ─────────────────────────
// A card is one single step, or a set's instruction immediately followed by its result. The
// steps array order IS the plan order; the editor never re-expands it into the fixed per-type
// blocks again. Keys and ordinals are derived from that order (renumberSteps).

interface WPCard {
  /** card_id, or a client-only temporary id for a card the server has not named yet. */
  id: string
  type: string
  steps: WPStep[]
  started: boolean
}

// A card added here has no card_id until the save response names it. Its temporary identity
// lives beside the step (never in the saved body) so drawer/error state can follow it.
const tempCardIds = new WeakMap<object, string>()
let tempCardSeq = 0
function cardIdentityOf(step: WPStep): string {
  if (step.card_id) return step.card_id
  const raw = toRaw(step)
  let id = tempCardIds.get(raw)
  if (!id) {
    tempCardSeq += 1
    id = `tmp:${tempCardSeq}`
    tempCardIds.set(raw, id)
  }
  return id
}

function groupCards(steps: WPStep[]): WPStep[][] {
  const out: WPStep[][] = []
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i]
    const next = steps[i + 1]
    if (step.pair_role === 'instruction' && next && next.pair_role === 'result') {
      out.push([step, next])
      i += 1
    } else {
      out.push([step])
    }
  }
  return out
}

const cards = computed<WPCard[]>(() => groupCards(plan.value?.steps ?? []).map((steps) => ({
  id: cardIdentityOf(steps[0]),
  type: steps[0].type,
  steps,
  started: !!steps[0].card_id && startedCardIds.value.has(steps[0].card_id),
})))

function rowKeyOf(card: WPCard, step: WPStep): string {
  return `${card.id}|${step.pair_role === 'result' ? 'result' : 'main'}`
}

/** The row identity of a step in the current plan (drawer / error bookkeeping). */
function rowKeyOfStep(step: WPStep): string | null {
  for (const card of cards.value) {
    if (card.steps.some((s) => toRaw(s) === toRaw(step))) return rowKeyOf(card, step)
  }
  return null
}

function rowNo(step: WPStep): number {
  const steps = plan.value?.steps ?? []
  return steps.findIndex((s) => toRaw(s) === toRaw(step)) + 1
}

/** Ordinals restart per type and follow the array order; pair keys follow (NR0003 §5.1 b). */
function renumberSteps(steps: WPStep[]): WPStep[] {
  const seen = new Map<string, number>()
  for (const card of groupCards(steps)) {
    const head = card[0]
    const n = (seen.get(head.type) ?? 0) + 1
    seen.set(head.type, n)
    head.ordinal = n
    head.key = makeKey(head.type, n)
    if (card.length === 2) {
      const result = card[1]
      result.ordinal = n
      result.key = makeKey(result.type, n)
      head.pair_key = result.key
      result.pair_key = head.key
    }
  }
  return steps
}

/** A card added by a quantity increase: no card_id — the server assigns one on save. */
function newCardSteps(code: string): WPStep[] | null {
  const unit = plan.value?.quantities[code]?.unit ?? 'sheet'
  if (unit === 'sheet') return [makeStep(code, 0, null, 'single')]
  const pair = pairOf(code)
  if (!pair) return null
  return [makeStep(code, 0, null, 'instruction'), makeStep(pair, 0, null, 'result')]
}

function isCardStarted(card: WPStep[]): boolean {
  const id = card[0].card_id
  return !!id && startedCardIds.value.has(id)
}

/** Index of the last started card; nothing may be placed at or before it (O7, G6). */
function lastStartedIndex(cardList: WPStep[][]): number {
  for (let i = cardList.length - 1; i >= 0; i--) {
    if (isCardStarted(cardList[i])) return i
  }
  return -1
}

// ── Fetch / recovery ──────────────────────────────────────────────────────

function applyReadView(data: any) {
  if (typeof data.title === 'string' && data.title) {
    tabsStore.setTabTitle(props.docId, data.title)
  }
  serverRegisteredProvidersKnown.value = Array.isArray(data.registered_providers)
  serverRegisteredProviders.value = serverRegisteredProvidersKnown.value
    ? data.registered_providers
    : []
  plan.value = data.body as WPBody
  const allCodes = typeOrder(Array.from(new Set([
    ...plan.value.counted_types,
    ...docTypeStore.countableTypes.map((item) => item.code),
  ])))
  const quantities = { ...plan.value.quantities }
  for (const code of allCodes) {
    if (quantities[code]) continue
    const unit = docTypeStore.countableTypes.find((item) => item.code === code)?.unit ?? 'sheet'
    quantities[code] = { unit: unit === 'set' ? 'set' : 'sheet', count: 0 }
  }
  plan.value.counted_types = allCodes
  plan.value.quantities = quantities
  providerStatuses.value = data.provider_status ?? []
  assignmentSummary.value = data.assignment_summary ?? []
  unassignedStepCount.value = data.unassigned_step_count ?? 0
  // 0649 T#3 (NR0003 O7): the server says which cards already started (by card identity).
  startedCardIds.value = new Set(
    (Array.isArray(data.step_execution_status) ? data.step_execution_status : [])
      .filter((entry: any) => entry?.started === true && typeof entry?.card_id === 'string')
      .map((entry: any) => entry.card_id as string),
  )
  revisionNo.value = data.revision_no
  noteMaxChars.value = Number(data.limits?.note_max_chars) || 1000
  preInstructionMaxChars.value = Number(data.limits?.pre_instruction_text_max_chars) || 20000
  preInstructionAttachmentMaxBytes.value = Number(data.limits?.pre_instruction_attachment_max_bytes) || 0
  reviewCountChoices.value = Array.isArray(data.review_count_choices) && data.review_count_choices.length
    ? data.review_count_choices
    : [0]
  docReviewStatus.value = data.doc_review_status
  // 0403 NR0004 F7: use the value the server judged, as-is. If it's absent from the
  // response (old response · mock), leave it open — only the server knows whether to
  // lock, and the bug was the screen guessing and locking on its own.
  editable.value = data.editable === undefined ? true : !!data.editable
  editLockedReason.value = data.edit_locked_reason ?? null
  dirty.value = false
  totals.value = data.totals ?? { design_sheets: 0, work_sets: 0, steps: plan.value.steps.length }
  staleAfterUpload.value = false
  unreadable.value = null
  restoreError.value = null
}

async function fetchPlan(): Promise<boolean> {
  loading.value = true
  unreadable.value = null
  restoreError.value = null
  conflict.value = null
  capabilityWarnings.value = []
  pendingCapabilityBody.value = null
  pendingCapabilitySource.value = null
  topLevelErrorRecords.value = []
  stepErrorRecords.value = {}
  restoreBuffer.clear()
  openDrawerRow.value = null
  serverRegisteredProviders.value = []
  serverRegisteredProvidersKnown.value = false
  try {
    const providerLoad = props.projectId
      ? aiProviderStore.ensureLoaded(props.projectId)
      : Promise.resolve()
    if (!docTypeStore.loaded) await Promise.all([docTypeStore.loadLabels(locale.value), providerLoad])
    else await providerLoad
    const res = await getRequest<any>(`/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan`)
    applyReadView(res.data)
    return true
  } catch (e: any) {
    const status = e?.response?.status
    const data = e?.response?.data
    if (status === 409 && data?.code === 'wp_unreadable') {
      unreadable.value = {
        message: data.message,
        detail: data.detail ?? '',
        raw: data.raw ?? null,
        revision_no: Number(data.revision_no) || 0,
        revisions: data.revisions ?? [],
      }
    } else {
      showToast(data?.message || data?.detail || String(e), 'danger')
    }
    return false
  } finally {
    loading.value = false
  }
}

async function reload() {
  await fetchPlan()
}

async function restoreRevision(sourceRevisionNo: number) {
  const state = unreadable.value
  if (!state || restoringRevision.value !== null || aiRunLocked.value) return
  const candidate = state.revisions.find((item) =>
    item.revision_no === sourceRevisionNo && item.restorable,
  )
  if (!candidate) return
  restoringRevision.value = sourceRevisionNo
  restoreError.value = null
  try {
    const res = await postRequest<any>(
      `/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan/revisions/${sourceRevisionNo}/restore`,
      { base_revision_no: state.revision_no },
    )
    applyReadView(res.data)
    showToast(t('main.work_plan.restore_success'), 'success')
  } catch (e: any) {
    const data = e?.response?.data
    const message = data?.message || data?.detail || t('main.work_plan.restore_failed')
    restoreError.value = message
    showToast(message, 'danger')
  } finally {
    restoringRevision.value = null
  }
}

// flowgate.default.0576 had its own restore implementation here (full refetch via
// fetchPlan(), then a toast). Superseded by `restoreRevision` above, which applies the
// server's read view directly and tracks `restoreError` inline instead of a second
// round trip — a strict improvement, not an independent behavior, so it is not kept
// alongside it.

// Pouring/applying this plan into the workflow sequence happens in the sibling
// DocWorkflow strip, not here. That save never touched this component, so
// `lastApplication` (the [마지막 적용] line) and the rest of this view stayed on
// whatever was loaded at mount/tab-switch until a manual reload. Exposed so
// MainPanel can pull a refresh in on the same 'sequence-updated' signal it
// already forwards to the doc header. Skipped while the table has unsaved
// edits so a pour elsewhere cannot silently discard them.
async function refreshAfterSequenceChange() {
  if (dirty.value) return
  await fetchPlan()
}

defineExpose({ fetchPlan: refreshAfterSequenceChange, ensureSaved })

// ── Quantity editing ─────────────────────────────────────────────────────

function updateDerivedSummary() {
  if (!plan.value) return
  let designSheets = 0
  let workSets = 0
  for (const quantity of Object.values(plan.value.quantities)) {
    if (quantity.unit === 'set') workSets += quantity.count
    else designSheets += quantity.count
  }
  totals.value = { design_sheets: designSheets, work_sets: workSets, steps: plan.value.steps.length }
  unassignedStepCount.value = plan.value.steps.filter((step) => !step.locked && !step.provider_id).length
  const counts = new Map<string, number>()
  for (const step of plan.value.steps) {
    if (step.provider_id) counts.set(step.provider_id, (counts.get(step.provider_id) ?? 0) + 1)
  }
  assignmentSummary.value = Array.from(counts.entries()).map(([providerId, stepCount]) => ({
    provider_id: providerId,
    display_name: providerDisplayName(providerId) ?? providerId,
    step_count: stepCount,
  }))
}

// 0649 T#3 (NR0003 §5.3, F5): a quantity change edits the cards of that one type in place and
// never re-expands the plan, so a person's card order survives the stepper and the AI
// suggestion (fetchSuggestion → setQuantity) alike. Growing appends new cards at the end of
// the current order (a card removed earlier in this session comes back first, with its id and
// values); shrinking removes the last cards of the type — never a started card (O7).
async function setQuantity(code: string, next: number) {
  if (!plan.value || isLocked.value) return
  const clamped = Math.max(COUNT_MIN, Math.min(COUNT_MAX, next))
  const unit = plan.value.quantities[code]?.unit ?? 'sheet'
  const quantities = { ...plan.value.quantities, [code]: { unit, count: clamped } }
  const ofType = groupCards([...plan.value.steps]).filter((card) => card[0].type === code)
  const removed: WPStep[][] = []
  if (clamped < ofType.length) {
    const removable = ofType.filter((card) => !isCardStarted(card))
    const need = ofType.length - clamped
    if (removable.length < need) {
      showToast(t('main.work_plan.quantity_decrease_blocked_started', { type: code }), 'warning')
      return
    }
    removed.push(...removable.slice(removable.length - need))
  }
  const removalCandidates = removed.flat().filter(hasValue)
  // D0007 §5.5 — a quantity decrease that would drop a step still carrying provider/note/
  // review/pre-instruction values needs an explicit confirmation, not a silent removal.
  if (removalCandidates.length > 0) {
    const ok = await confirm({
      title: t('main.work_plan.quantity_removal_warning_title', { n: removalCandidates.length }),
      confirmLabel: t('main.work_plan.quantity_removal_warning_confirm'),
      cancelLabel: t('main.work_plan.quantity_removal_warning_cancel'),
      danger: true,
    })
    if (!ok) return
  }
  if (!plan.value || isLocked.value) return
  let nextCards = groupCards([...plan.value.steps])
  if (removed.length) {
    const stack = restoreBuffer.get(code) ?? []
    // Removed last-first, so popping the stack brings them back in their original order and
    // at their original positions.
    for (const card of [...removed].reverse()) {
      const index = nextCards.findIndex((c) => toRaw(c[0]) === toRaw(card[0]))
      stack.push({ steps: card, index })
      nextCards.splice(index, 1)
    }
    restoreBuffer.set(code, stack)
  }
  const currentCount = nextCards.filter((card) => card[0].type === code).length
  for (let n = currentCount; n < clamped; n++) {
    const restored = restoreBuffer.get(code)?.pop()
    if (restored) {
      // never in front of a started card (O7)
      const at = Math.max(lastStartedIndex(nextCards) + 1, Math.min(restored.index, nextCards.length))
      nextCards.splice(at, 0, restored.steps)
      continue
    }
    const card = newCardSteps(code)
    if (!card) break
    // NR0003 §5.3: a new card is appended at the end of the current order, so it never lands
    // in front of an existing card — a started one included (O7).
    nextCards.push(card)
  }
  plan.value.quantities = quantities
  plan.value.steps = renumberSteps(nextCards.flat())
  markDirty()
  updateDerivedSummary()
}

// ── Card moves (0649 T#3, NR0003 §5.3 / O7) ─────────────────────────────────

const CARD_DROP_END = '__end__'
const draggedCardId = ref<string | null>(null)
const dragOverCardId = ref<string | null>(null)

function canMoveCard(card: WPCard): boolean {
  return !isLocked.value && !hasPendingCapabilityWarning.value && !card.started
}

/** Move the card at `from` so it lands at index `to` of the card list without it. */
function moveCard(from: number, to: number): boolean {
  if (!plan.value || isLocked.value || hasPendingCapabilityWarning.value) return false
  const cardList = groupCards([...plan.value.steps])
  const moving = cardList[from]
  if (!moving || isCardStarted(moving)) return false
  const rest = cardList.filter((_, i) => i !== from)
  const target = Math.max(0, Math.min(rest.length, to))
  // O7 / G6: a card that has not started can never be placed in front of a started card.
  if (target <= lastStartedIndex(rest)) {
    showToast(t('main.work_plan.card_move_before_started'), 'warning')
    return false
  }
  if (target === from) return false
  rest.splice(target, 0, moving)
  plan.value.steps = renumberSteps(rest.flat())
  markDirty()
  updateDerivedSummary()
  return true
}

function canMoveCardBy(index: number, delta: number): boolean {
  const list = cards.value
  const card = list[index]
  if (!card || !canMoveCard(card)) return false
  const to = index + delta
  if (to < 0 || to >= list.length) return false
  const rest = list.filter((_, i) => i !== index)
  for (let i = rest.length - 1; i >= 0; i--) {
    if (rest[i].started) return to > i
  }
  return true
}

function moveCardBy(index: number, delta: number) {
  if (!canMoveCardBy(index, delta)) return
  moveCard(index, index + delta)
}

function onCardDragStart(event: DragEvent, card: WPCard) {
  if (!canMoveCard(card)) {
    event.preventDefault()
    return
  }
  draggedCardId.value = card.id
  dragOverCardId.value = null
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', card.id)
  }
}

function onCardDragOver(card: WPCard) {
  if (draggedCardId.value === null) return
  dragOverCardId.value = card.id
}

/** Drop = put the dragged card in front of the card at `targetIndex` (cards.length = the end). */
function onCardDrop(targetIndex: number) {
  const draggedId = draggedCardId.value
  draggedCardId.value = null
  dragOverCardId.value = null
  if (draggedId === null) return
  const from = cards.value.findIndex((card) => card.id === draggedId)
  if (from < 0) return
  moveCard(from, targetIndex > from ? targetIndex - 1 : targetIndex)
}

function onCardDragEnd() {
  draggedCardId.value = null
  dragOverCardId.value = null
}

function setDefaultProvider(providerId: string | null) {
  if (!plan.value || isLocked.value) return
  plan.value.defaults.provider_id = providerId
  markDirty()
}

function setDefaultNote(note: string) {
  if (!plan.value || isLocked.value) return
  plan.value.defaults.note = note
  markDirty()
}

// ── Step editing ──────────────────────────────────────────────────────────

/**
 * 0411 T0004: the server response is the atomic source for the registered set.
 * The project store covers old responses and project transitions; frozen candidates are only
 * a final compatibility fallback for old tests/servers that expose neither source.
 */
const liveProviderRowsKnown = computed(() =>
  serverRegisteredProvidersKnown.value
  || (!!props.projectId
    && aiProviderStore.loadedProjectId === props.projectId
    && !aiProviderStore.error),
)

const liveProviderRows = computed<WPRegisteredProvider[]>(() => {
  if (serverRegisteredProvidersKnown.value) return serverRegisteredProviders.value
  if (liveProviderRowsKnown.value) {
    return aiProviderStore.providers.map((provider) => ({
      id: provider.id,
      name: provider.name,
      group_label: null,
    }))
  }
  return (plan.value?.provider_candidates ?? []).map((candidate) => ({
    id: candidate.provider_id,
    name: candidate.display_name,
    group_label: candidate.group_label,
  }))
})

const liveProviderById = computed(() =>
  new Map(liveProviderRows.value.map((provider) => [provider.id, provider])),
)

const scopeProviderOptions = computed<WPCandidate[]>(() =>
  liveProviderRows.value.map((provider) => ({
    provider_id: provider.id,
    display_name: provider.name,
    group_label: provider.group_label,
  })),
)

function candidateStillRegistered(providerId: string): boolean {
  if (serverRegisteredProvidersKnown.value) return liveProviderById.value.has(providerId)
  // An old server may omit registered_providers but still return an authoritative status row.
  const status = providerStatuses.value.find((item) => item.provider_id === providerId)
  if (status) return status.registered
  if (liveProviderRowsKnown.value) return liveProviderById.value.has(providerId)
  // Older/mocked responses may omit both registered_providers and provider_status.
  return !!plan.value?.provider_candidates.some((candidate) => candidate.provider_id === providerId)
}

function providerDisplayName(providerId: string | null): string | null {
  if (!providerId || !plan.value) return null
  return liveProviderById.value.get(providerId)?.name
    ?? plan.value.provider_candidates.find((candidate) => candidate.provider_id === providerId)?.display_name
    ?? providerId
}

function setStepProvider(key: string, providerId: string | null) {
  if (isLocked.value) return
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked) return
  step.provider_id = providerId
  step.provider_display_name = providerDisplayName(providerId)
  step.origin = 'human'
  markDirty()
  updateDerivedSummary()
}

function setStepNote(key: string, note: string) {
  if (isLocked.value) return
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked) return
  step.note = note
  step.origin = 'human'
  markDirty()
}

function buildProviderOptions(unassignedLabel: string, extraIds: (string | null)[]): { id: string; name: string }[] {
  const options: { id: string; name: string }[] = []
  const seen = new Set<string>()
  const append = (providerId: string) => {
    if (!providerId || seen.has(providerId)) return
    seen.add(providerId)
    const name = providerDisplayName(providerId) ?? providerId
    options.push({
      id: providerId,
      name: candidateStillRegistered(providerId)
        ? name
        : `${name} (${t('main.work_plan.unavailable_provider')})`,
    })
  }
  // Manual assignment is registered-all, independent from the frozen AI candidate scope.
  liveProviderRows.value.forEach((provider) => append(provider.id))
  // Keep deleted providers visible only when the body actually uses them.
  extraIds.forEach((id) => append(id ?? ''))
  return [{ id: '', name: unassignedLabel }, ...options]
}

const providerOptionsWithUnassigned = computed(() => buildProviderOptions(
  t('main.work_plan.unassigned'),
  [plan.value?.defaults.provider_id ?? null, ...(plan.value?.steps ?? []).map((step) => step.provider_id)],
))

// D0007 §3.3/§3.4 — reviewer is a second, independent provider slot per step. An empty
// value means "project default reviewer at execution time", not "unassigned" like the
// provider column, so it gets its own label instead of reusing providerOptionsWithUnassigned.
const reviewerOptionsWithDefault = computed(() => buildProviderOptions(
  t('main.work_plan.reviewer_project_default'),
  (plan.value?.steps ?? []).map((step) => step.reviewer_provider_id),
))

// ── Review count / reviewer / pre-instruction editing (T0010 §4-§7, D0007 §3.2-§3.4) ──────

function reviewCountLabel(count: number): string {
  if (count === 0) return t('main.work_plan.review_none')
  if (count === -1) return t('main.work_plan.review_unlimited')
  return t('main.work_plan.review_n_times', { n: count })
}

function reviewSummaryText(step: WPStep): string {
  const count = step.review_count ?? 0
  if (count === 0) return reviewCountLabel(0)
  const reviewerLabel = step.reviewer_provider_id
    ? (providerDisplayName(step.reviewer_provider_id) ?? step.reviewer_provider_id)
    : t('main.work_plan.reviewer_project_default')
  return t('main.work_plan.review_summary_active', { count: reviewCountLabel(count), reviewer: reviewerLabel })
}

// D0007 §3.2 — a result-role step (NR/TR) is a report: pre-instruction has no meaning there.
// A locked (TSR) step never reaches this — its drawer never opens at all.
function instrEligible(step: WPStep): boolean {
  return step.pair_role !== 'result'
}

function stepHasInstructionValue(step: WPStep): boolean {
  return (step.pre_instruction_text ?? '').trim() !== '' || !!step.pre_instruction_attachment
}

function toggleDrawer(rowKey: string, step: WPStep) {
  if (step.locked) return
  openDrawerRow.value = openDrawerRow.value === rowKey ? null : rowKey
}

function closeDrawer() {
  openDrawerRow.value = null
}

function setStepReviewCount(key: string, count: number) {
  if (isLocked.value) return
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked) return
  step.review_count = count
  // D0007 §3.3 — "검수 안 함을 고른 단계에서는 검수 담당 칸이 꺼진다": a stale reviewer left
  // behind a switch back to 0 would otherwise fail save with reviewer_not_allowed.
  if (count === 0) {
    step.reviewer_provider_id = null
    step.reviewer_provider_display_name = null
  }
  markDirty()
}

function setStepReviewer(key: string, providerId: string | null) {
  if (isLocked.value) return
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked || (step.review_count ?? 0) === 0) return
  step.reviewer_provider_id = providerId
  step.reviewer_provider_display_name = providerId ? providerDisplayName(providerId) : null
  markDirty()
}

function setStepInstruction(key: string, text: string) {
  if (isLocked.value) return
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked || !instrEligible(step)) return
  step.pre_instruction_text = text
  step.origin = 'human'
  markDirty()
}

function removeStepInstructionAttachment(key: string) {
  if (isLocked.value) return
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked || !instrEligible(step)) return
  step.pre_instruction_attachment = null
  markDirty()
}

// D0007 §3.7 — the file goes straight to this document's own attachment store on selection;
// the step only ever holds a reference. One file per step: a replace re-uses the same field, it
// never adds a second reference, and the previous reserved file becomes cleanup-eligible only
// after the next successful save (T#1 lifecycle), never here.
async function onInstrFileSelected(step: WPStep, rowKey: string, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  input.value = ''
  if (!file || isLocked.value || step.locked || !instrEligible(step)) return
  instrUploadingKey.value = rowKey
  try {
    const form = new FormData()
    form.append('file', file)
    // The step's key at upload time; it only names the reserved file (the reference check is
    // doc/filename/sha), so a later renumber does not invalidate the attachment.
    form.append('step_key', step.key)
    const res = await postFormRequest<any>(
      `/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan/pre-instruction-attachments`,
      form,
    )
    step.pre_instruction_attachment = res.data.reference
    step.origin = 'human'
    markDirty()
  } catch (e: any) {
    const data = e?.response?.data
    showToast(data?.message || data?.detail || String(e), 'danger')
  } finally {
    instrUploadingKey.value = null
  }
}

async function saveFromDrawer(rowKey: string, step: WPStep) {
  const stepNo = rowNo(step)
  const result = await ensureSaved()
  if (result === 'saved' || result === 'clean') {
    // A saved new card trades its temporary id for the server's; persistPlanBody carried the
    // open drawer over by position, so the row now at the same position counts as this one.
    const after = plan.value?.steps[stepNo - 1]
    const nowKey = after ? rowKeyOfStep(after) : null
    if (openDrawerRow.value === rowKey || (nowKey !== null && openDrawerRow.value === nowKey)) {
      openDrawerRow.value = null
    }
    showToast(t('main.work_plan.drawer_save_success', { n: stepNo }), 'success')
  }
  // 'failed' / 'capability_warning': the drawer and its inputs stay exactly as-is (D0007 §6.2)
  // — the existing save-failure / capability-warning banners already show what went wrong.
}

/**
 * Mockup xc32frrg screen 1 names the document column by type, not by logical key:
 * "설계지시 1장", "작업 레포트 1세트". A report row has no quantity entry of its own,
 * so it reads its pair's.
 */
function stepDocParts(step: WPStep): { name: string; quantity: string } {
  const name = docTypeStore.getLabel(step.type)
  const own = plan.value?.quantities[step.type]
  const q = own ?? (step.pair_key ? plan.value?.quantities[step.pair_key.split('#')[0]] : undefined)
  if (!q) return { name: name || step.key, quantity: '' }
  const quantity = q.unit === 'sheet' && own
    ? (q.count <= 1
        ? `${step.ordinal}${t('main.work_plan.unit_sheet_short')}`
        : t('main.work_plan.doc_label_sheet', { n: step.ordinal, total: q.count }))
    : t('main.work_plan.doc_label_set', { n: step.ordinal })
  return { name, quantity }
}

function stepDocName(step: WPStep): string {
  return stepDocParts(step).name
}

function stepDocQuantity(step: WPStep): string {
  return stepDocParts(step).quantity
}

function applyDefaults() {
  if (!plan.value || isLocked.value) return
  for (const step of plan.value.steps) {
    if (step.locked) continue
    step.provider_id = plan.value.defaults.provider_id
    step.provider_display_name = providerDisplayName(plan.value.defaults.provider_id)
    step.note = plan.value.defaults.note || null
    step.origin = 'human'
  }
  markDirty()
  updateDerivedSummary()
  showToast(t('main.work_plan.apply_to_all_done'), 'success')
}

// ── Scoped project-map suggestion and real AI invocation ──────────────────

const scopeSteps = computed(() => (plan.value?.steps ?? []).map((step) => ({
  key: step.key,
  type: step.type,
  label: [stepDocName(step), stepDocQuantity(step)].filter(Boolean).join(' '),
  provider_id: step.provider_id,
  locked: step.locked,
})))

async function fetchSuggestion(scope: WorkPlanScope) {
  if (!plan.value || isLocked.value) return
  aiSuggesting.value = true
  try {
    const res = await postRequest<any>(
      `/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan/suggest`,
      { base_revision_no: revisionNo.value, scope },
    )
    const quantities = res.data?.suggested?.quantities ?? {}
    for (const [code, count] of Object.entries(quantities)) await setQuantity(code, Number(count))
    const steps = res.data?.suggested?.steps ?? []
    for (const suggested of steps) {
      const target = plan.value.steps.find((item) => item.key === suggested.key)
      if (!target || target.locked) continue
      target.provider_id = suggested.provider_id ?? target.provider_id
      target.provider_display_name = suggested.provider_display_name ?? target.provider_display_name
      if (suggested.note !== undefined) target.note = suggested.note
      target.origin = 'ai_suggested'
    }
    // The result of receiving a suggestion is also an edit that hasn't been saved yet.
    markDirty()
    updateDerivedSummary()
    aiScopeOpen.value = false
    showToast(t('main.work_plan.ai_scope_success', { quantities: Object.keys(quantities).length, steps: steps.length }), 'success')
  } catch (e: any) {
    const data = e?.response?.data
    // 0403 NR0004 F6: the suggestion's basis diverged from the screen. It arrives with the
    // same code as the save path, so the same [Reload] strip is shown — a toast alone would
    // leave no indication of what to do.
    if (e?.response?.status === 409 && data?.code === 'wp_revision_conflict') {
      conflict.value = { updatedBy: data.updated_by ?? null, updatedAt: data.updated_at ?? null }
      aiScopeOpen.value = false
    }
    showToast(data?.message || String(e), 'danger')
  } finally {
    aiSuggesting.value = false
  }
}

async function startAiFill(scope: WorkPlanScope) {
  if (!plan.value || !props.projectId || isLocked.value) return
  aiSuggesting.value = true
  try {
    const parts = props.docId.split('.')
    const res = await postRequest<any>('/api/v1/ai-invoke/start', {
      project: props.projectId,
      module: parts[1] ?? null,
      group: parts[2] ?? '',
      doc_ref: props.docId,
      action_scope: 'work_plan_fill',
      mode: 'single',
      work_plan_scope: scope,
    })
    aiRunId.value = res.data?.run_id ?? null
    aiScopeOpen.value = false
  } catch (e: any) {
    aiSuggesting.value = false
    showToast(e?.response?.data?.error_message || e?.response?.data?.message || String(e), 'danger')
  }
}

function onAiInvoke(event: Event) {
  const detail = (event as CustomEvent).detail
  const payload = detail?.payload ?? {}
  if (detail?.kind !== 'finished' || !aiRunId.value || payload.run_id !== aiRunId.value) return
  aiRunId.value = null
  aiSuggesting.value = false
  void fetchPlan()
}

onMounted(() => window.addEventListener('fg:ai_invoke', onAiInvoke))
onBeforeUnmount(() => window.removeEventListener('fg:ai_invoke', onAiInvoke))

// 0434 B0001("F5를 누르지 않으면 적용되지 않음") — 위 onAiInvoke 는 *이 화면에서 시작한*
// AI 채우기(startAiFill)의 run_id 만 받는다. 그런데 실제로 작업계획을 다시 쓰는 것은 반려
// 뒤 검토 막대에서 시작하는 그룹 워커이고, 그 run_id 는 aiRunId 에 들어오지 않는다. 그래서
// 워커가 새 리비전을 등록해도 이 화면은 열 때 읽은 옛 계획(수량·단계·멘트)을 계속 그렸고,
// 바뀐 계획은 F5 뒤에야 보였다 — 사람이 보기엔 "수정했다는데 적용이 안 된" 화면이다.
// 다른 문서 타입의 본문은 이미 같은 이벤트로 다시 그린다(MdViewer, ConversationView).
// 작업계획 편집기만 듣지 않고 있었다. 서버는 그대로다 — 인박스 edit 등록이 이미
// document_explorer_refresh(operation='updated') 를 보내고 useFlowGateSse 가 그것을
// fg:document_content_changed 로 바꿔 넣는다. 저장하지 않은 표 편집이 있으면 다시 읽지
// 않는다: 남의 저장이 내가 치고 있던 값을 조용히 덮어쓰면 안 되고, 그 경우는 [저장] 때
// 서버가 409 로 알려 주는 기존 충돌 띠가 처리한다.
function onDocumentContentChanged(event: Event) {
  const detail = (event as CustomEvent).detail as { doc_id?: string } | undefined
  if (detail?.doc_id && detail.doc_id !== props.docId) return
  if (dirty.value || saving.value) return
  void fetchPlan()
}

onMounted(() => window.addEventListener('fg:document_content_changed', onDocumentContentChanged))
onBeforeUnmount(() => window.removeEventListener('fg:document_content_changed', onDocumentContentChanged))

// ── Save (P0009 §4.6 ~ §4.8) ─────────────────────────────────────────────

function canonicalBody(): WPBody {
  const p = plan.value!
  const body: WPBody = {
    wp_version: p.wp_version,
    binding: p.binding,
    counted_types: [...p.counted_types],
    quantities: { ...p.quantities },
    provider_candidates: p.provider_candidates.map((c) => ({ ...c })),
    defaults: { ...p.defaults },
    steps: p.steps.map((s) => ({ ...s })),
  }
  // T0007 §3 — GET can hand back top-level x_* extension fields the server preserves but this
  // editor has no UI for; carry them through untouched so a manual save/copy never drops them.
  const raw = p as unknown as Record<string, unknown>
  for (const key of Object.keys(raw)) {
    if (key.startsWith('x_')) (body as unknown as Record<string, unknown>)[key] = raw[key]
  }
  return body
}

const rawJson = computed(() => unreadable.value?.raw ?? (plan.value ? JSON.stringify(canonicalBody(), null, 2) : ''))

async function copyRaw() {
  const ok = await copyToClipboard(rawJson.value)
  showToast(ok ? t('main.work_plan.copy_done') : t('main.work_plan.copy_failed'), ok ? 'success' : 'danger')
}

let saveInFlight: Promise<'saved' | 'failed' | 'capability_warning'> | null = null

async function ensureSaved(): Promise<'clean' | 'saved' | 'failed' | 'capability_warning'> {
  if (!dirty.value) return 'clean'
  if (saveInFlight) return saveInFlight
  if (!plan.value || isLocked.value) return 'failed'

  saveInFlight = saveDirtyPlan()
  try {
    return await saveInFlight
  } finally {
    saveInFlight = null
  }
}

async function save() {
  if (!plan.value || saving.value || isLocked.value || hasPendingCapabilityWarning.value) return
  if (dirty.value) {
    await ensureSaved()
    return
  }
  await saveDirtyPlan()
}

// NR0003 §5 — the PUT / validation-error / conflict handling that a manual save and a JSON
// upload share. Letting them diverge would mean a save and an upload disagree on what a 422
// or a 409 means for the screen.
//
// flowgate.default.0576 TR0005 rev4 — the same PUT can also refuse to apply the body at all
// (422 provider_capability_confirmation_required) when a T/TR step's provider cannot modify
// source or run tests, or is unassigned/unknown to this project. That gate is server-authoritative
// (work_plan_service.capability_warning_findings) and applies to whatever body is sent, so a save
// and an upload must honor it identically instead of only the manual-edit path checking it.
type PersistResult =
  | { status: 'saved' }
  | { status: 'failed' }
  | { status: 'capability_warning'; findings: WPCapabilityFinding[] }

async function persistPlanBody(body: WPBody, capabilityWarningAcks: string[] = []): Promise<PersistResult> {
  conflict.value = null
  topLevelErrorRecords.value = []
  stepErrorRecords.value = {}
  // 0649 T#3: the server answers with the same steps in the same order, but a new card's
  // temporary id becomes a real card_id — carry the open drawer over by row position.
  const openIndex = openDrawerRow.value !== null && plan.value
    ? plan.value.steps.findIndex((step) => rowKeyOfStep(step) === openDrawerRow.value)
    : -1
  try {
    const payload: { base_revision_no: number; body: WPBody; capability_warning_acks?: string[] } = {
      base_revision_no: revisionNo.value,
      body,
    }
    if (capabilityWarningAcks.length) payload.capability_warning_acks = capabilityWarningAcks
    const res = await putRequest<any>(`/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan`, payload)
    // Apply the canonical response as one state for both manual save and JSON upload.
    plan.value = (res.data.body ?? body) as WPBody
    revisionNo.value = res.data.revision_no
    totals.value = res.data.totals
    assignmentSummary.value = res.data.assignment_summary ?? []
    unassignedStepCount.value = res.data.unassigned_step_count ?? 0
    if (typeof res.data.title === 'string' && res.data.title) {
      tabsStore.setTabTitle(props.docId, res.data.title)
      window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', {
        detail: { project: props.projectId, doc_id: props.docId },
      }))
    }
    restoreBuffer.clear()
    if (openIndex >= 0) {
      const reopened = plan.value?.steps[openIndex]
      openDrawerRow.value = reopened ? rowKeyOfStep(reopened) : null
    }
    dirty.value = false
    return { status: 'saved' }
  } catch (e: any) {
    const status = e?.response?.status
    const data = e?.response?.data
    if (status === 422 && data?.code === 'provider_capability_confirmation_required') {
      return { status: 'capability_warning', findings: Array.isArray(data.findings) ? data.findings : [] }
    }
    if (status === 422 && Array.isArray(data?.errors)) {
      const byRow: Record<string, WpFieldError[]> = {}
      const top: WpFieldError[] = []
      for (const err of data.errors as WpFieldError[]) {
        // The server names the step by the key it had in the body just sent; pin the error to
        // that row's identity so a later card move does not hand it to another card.
        const step = err.key ? plan.value?.steps.find((s) => s.key === err.key) : undefined
        const rowKey = step ? rowKeyOfStep(step) : null
        if (rowKey) {
          byRow[rowKey] = byRow[rowKey] ?? []
          byRow[rowKey].push(err)
        } else {
          // no key, or a key this screen does not hold (an uploaded file's step): top banner
          top.push(err)
        }
      }
      stepErrorRecords.value = byRow
      // No top-level field error: fall back to the headline `message` (unknown code renders
      // as its own msg, i.e. this fixed text) rather than leaving the banner empty.
      topLevelErrorRecords.value = top.length ? top : [{ loc: '', key: null, code: '', params: {}, msg: data.message }]
      showToast(data.message, 'danger', 5000)
    } else if (status === 409 && data?.code === 'wp_revision_conflict') {
      conflict.value = { updatedBy: data.updated_by ?? null, updatedAt: data.updated_at ?? null }
    } else {
      showToast(data?.message || data?.detail || String(e), 'danger')
    }
    return { status: 'failed' }
  }
}

// Fires the save-success toasts. Shared by a first save attempt and by the retry that follows
// an explicit capability-warning confirmation, so the two never disagree on what "saved" means.
function announceSaveSuccess() {
  showToast(t('main.work_plan.save_success'), 'success')
  if (unassignedStepCount.value > 0) {
    showToast(t('main.work_plan.unassigned_warning', { n: unassignedStepCount.value }), 'warning', 5000)
  }
}

async function saveDirtyPlan(): Promise<'saved' | 'failed' | 'capability_warning'> {
  saving.value = true
  try {
    const body = canonicalBody()
    const result = await persistPlanBody(body)
    if (result.status === 'capability_warning') {
      capabilityWarnings.value = result.findings
      pendingCapabilityBody.value = body
      pendingCapabilitySource.value = 'save'
    } else if (result.status === 'saved') {
      announceSaveSuccess()
    }
    return result.status
  } finally {
    saving.value = false
  }
}

// ── Download / upload (flowgate.default.0576 T0004 / NR0003) ────────────────

function fallbackWorkPlanFilename(docId: string): string {
  return docId ? `${docId}.work-plan.json` : 'work-plan.json'
}

// NR0003 §3.1 — the download source is a fresh GET of the canonical body, never `rawJson`.
// `canonicalBody()` only lists the fixed fields the editor knows, so a top-level `x_*`
// extension the server preserves would silently vanish from the downloaded file.
function downloadBlob(content: string, type: string, filename: string) {
  const blob = new Blob([content], { type })
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(href)
}

async function downloadWorkPlan() {
  // flowgate.default.0576 kept [Download] enabled for an unreadable canonical body by
  // reading `plan.value`/`unreadable.value?.raw` inline and reusing the ordinary
  // `.json` `fallbackWorkPlanFilename`. Here the raw text is not necessarily valid
  // JSON at all (that is the whole reason it is unreadable), so it downloads through
  // the shared `downloadBlob` helper as plain text under its own `.unreadable.raw.txt`
  // name instead — same [Download]-always-available behavior, honester content type.
  if (loading.value || dirty.value || downloading.value || (!plan.value && !unreadable.value?.raw)) return
  const unreadableRaw = unreadable.value?.raw
  if (!!unreadable.value && unreadableRaw === null) return
  downloading.value = true
  try {
    if (unreadableRaw !== undefined) {
      downloadBlob(
        unreadableRaw ?? '',
        'text/plain;charset=utf-8',
        `${props.docId}.work-plan.unreadable.raw.txt`,
      )
      return
    }
    // flowgate.default.0576's original combined expression, verbatim.
    const res = unreadable.value ? null : await getRequest<any>(`/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan`)
    const json = unreadable.value?.raw ?? `${JSON.stringify(res!.data.body, null, 2)}\n`
    const blob = new Blob([json], { type: 'application/json;charset=utf-8' })
    const href = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = fallbackWorkPlanFilename(props.docId)
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(href)
  } catch (e: any) {
    showToast(e?.response?.data?.message || e?.response?.data?.detail || t('main.work_plan.download_failed'), 'danger')
  } finally {
    downloading.value = false
  }
}

function triggerUpload() {
  if (loading.value || !!unreadable.value || saving.value || uploading.value || isLocked.value || dirty.value || hasPendingCapabilityWarning.value) return
  workPlanFileInput.value?.click()
}

// Blob.text() is unavailable in the jsdom test environment; FileReader has been supported
// everywhere since long before that, so this reads the same way in real browsers and in tests.
function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('file read error'))
    reader.readAsText(file)
  })
}

// NR0003 §4.2 — a parsed upload is never written into `plan.value` before the PUT succeeds.
// Reflecting it on screen first would show an "applied" plan the server actually rejected.
// The PUT already succeeded server-side by this point. A failed refetch here is not an
// upload failure — it only means this screen could not confirm it, so fetchPlan's own error
// toast / unreadable banner stands in for the failure signal instead of a second, contradictory
// "success" toast. But persistPlanBody already moved revisionNo to the new revision and cleared
// dirty, while plan.value is still the pre-upload body — saving from that mismatched state would
// carry the new revision number over stale content and overwrite the just-uploaded plan without
// a conflict. staleAfterUpload keeps the screen locked (isLocked) until reload() actually lands
// the canonical body. Shared by a first upload and by the retry after an explicit
// capability-warning confirmation.
async function finishUploadedSave() {
  const refetched = await fetchPlan()
  if (refetched) {
    showToast(t('main.work_plan.upload_success'), 'success')
  } else {
    staleAfterUpload.value = true
  }
}

async function onWorkPlanFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  input.value = ''
  if (!file) return
  if (loading.value || !!unreadable.value || saving.value || uploading.value || isLocked.value || dirty.value || hasPendingCapabilityWarning.value) return

  uploading.value = true
  try {
    let text: string
    try {
      text = await readFileAsText(file)
    } catch {
      showToast(t('main.work_plan.upload_parse_error'), 'danger')
      return
    }
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1)
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      showToast(t('main.work_plan.upload_parse_error'), 'danger')
      return
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      showToast(t('main.work_plan.upload_parse_error'), 'danger')
      return
    }
    const body = parsed as WPBody
    const result = await persistPlanBody(body)
    if (result.status === 'capability_warning') {
      capabilityWarnings.value = result.findings
      pendingCapabilityBody.value = body
      pendingCapabilitySource.value = 'upload'
    } else if (result.status === 'saved') {
      await finishUploadedSave()
    }
  } finally {
    uploading.value = false
  }
}

// flowgate.default.0576 TR0005 rev4 — the explicit confirmation the capability gate requires
// (P0009 / provider_capability_service): re-send the same held body with every finding's
// step_key acknowledged. Never auto-derived from a click elsewhere — only this button counts.
async function confirmCapabilityWarning() {
  const body = pendingCapabilityBody.value
  const source = pendingCapabilitySource.value
  if (!body || !source) return
  const acks = capabilityWarnings.value.map((finding) => finding.step_key)
  const busy = source === 'save' ? saving : uploading
  confirmingCapability.value = true
  busy.value = true
  try {
    const result = await persistPlanBody(body, acks)
    if (result.status === 'capability_warning') {
      // The findings changed underneath (providers/steps moved) — show the current set instead
      // of silently retrying with acks that no longer match.
      capabilityWarnings.value = result.findings
      pendingCapabilityBody.value = body
      return
    }
    capabilityWarnings.value = []
    pendingCapabilityBody.value = null
    pendingCapabilitySource.value = null
    if (result.status !== 'saved') return
    if (source === 'save') announceSaveSuccess()
    else await finishUploadedSave()
  } finally {
    confirmingCapability.value = false
    busy.value = false
  }
}

function cancelCapabilityWarning() {
  capabilityWarnings.value = []
  pendingCapabilityBody.value = null
  pendingCapabilitySource.value = null
}

watch(() => props.docId, () => { void fetchPlan() })
</script>

<style scoped>
.wp-status-pill {
  font-size: .64rem; font-weight: 600; padding: 1px 7px; border-radius: 999px;
  margin-left: 8px; background: var(--surface-h, #f1f5f9); color: var(--text-m);
}
.wp-status-approved { background: var(--success-l, #dcfce7); color: var(--success, #16a34a); }
.wp-status-rejected { background: var(--danger-l, #fee2e2); color: var(--danger, #dc2626); }
/* rej_01M2VY4Z8FG9M2S0 — card-hd's .card-actions has no gap rule in this component (unlike
   the duplicated copies in AttachmentCard/GenericDocumentBody/ConversationDocumentView), so
   the header buttons sat only 4px apart (plain inline whitespace) instead of a real gap. */
.card-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.wp-body { display: flex; flex-direction: column; gap: 14px; padding: 16px; }
.wp-loading { padding: 24px; text-align: center; color: var(--text-m); }
.wp-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.wp-toolbar-spacer { flex: 1; }
.wp-review-hint, .wp-locked-hint {
  display: flex; align-items: flex-start; gap: 8px; font-size: .78rem; line-height: 1.55; color: var(--text-m);
  background: var(--surface-h, #f8fafc); border-radius: var(--r, 6px); padding: 9px 12px;
}
.wp-conflict-banner, .wp-error-banner {
  display: flex; align-items: center; gap: 8px; font-size: .8rem; padding: 8px 12px; border-radius: var(--r, 6px);
}
.wp-conflict-banner { background: var(--warning-l, #fef3c7); color: var(--warning, #b45309); }
.wp-capability-banner { flex-direction: column; align-items: stretch; gap: 6px; }
.wp-capability-banner-head { display: flex; align-items: center; gap: 8px; }
.wp-capability-list { margin: 0; padding-left: 20px; list-style: disc; }
.wp-capability-list li { margin: 2px 0; }
.wp-capability-banner-actions { display: flex; gap: 8px; }
/* 0403 NR0004 F5 — unsaved-edit strip. Same spot, same shape as the save-conflict strip. */
.wp-dirty-banner {
  display: flex; align-items: center; gap: 8px; font-size: .8rem; padding: 8px 12px;
  border-radius: var(--r, 6px); background: var(--warning-l, #fef3c7); color: var(--warning, #b45309);
}
.wp-error-banner { background: var(--danger-l, #fee2e2); color: var(--danger, #dc2626); }
.wp-section { display: flex; flex-direction: column; gap: 10px; }
.wp-section-hd { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wp-step-no-badge {
  width: 20px; height: 20px; border-radius: 50%; background: var(--primary, #2563eb); color: #fff;
  font-size: .68rem; font-weight: 700; display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.wp-section-ico { color: var(--text-m); }
.wp-section-title { font-size: .82rem; font-weight: 700; color: var(--text); }
.wp-section-missing {
  font-size: .7rem; font-weight: 700; padding: 1px 8px; border-radius: 999px;
  background: var(--danger-l, #fee2e2); color: var(--danger, #dc2626);
}
.wp-section-totals {
  margin-left: auto; font-size: .72rem; color: var(--text-s, #475569); font-weight: 700;
  padding: 2px 9px; border: 1px solid var(--border, #e2e8f0); border-radius: 999px; background: #fff;
}
/* Mockup xc32frrg screen 1 — quantity card: tag · name/unit · stepper on one line. */
.wp-qty-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(208px, 1fr)); gap: 8px; }
.wp-qty-card { display:flex; align-items:center; gap:8px; min-width:0; padding:8px 10px; border:1px solid var(--border); border-radius:var(--r); background:var(--surface); }
.wp-qty-card.zero { border-style:dashed; background:#fbfcfe; }
.wp-qty-card.zero .wp-qty-name { color:var(--text-m); }
.wp-qty-tags { display:inline-flex; gap:3px; flex-shrink:0; }
.wp-qty-body { display:flex; flex-direction:column; min-width:0; }
.wp-qty-name { font-size:.76rem; font-weight:600; color:var(--text); }
.wp-qty-unit { font-size:.66rem; color:var(--text-m); }
.wp-qty-stepper { display:flex; align-items:center; margin-left:auto; overflow:hidden; border:1px solid var(--border); border-radius:var(--r-sm); background:var(--surface); }
.wp-stepper-btn { width:24px; height:24px; padding:0; border:0; background:transparent; color:var(--text-s); cursor:pointer; font-weight:700; }
.wp-stepper-btn:hover:not(:disabled) { background:var(--surface-h); }
.wp-stepper-btn:disabled { opacity:.4; cursor:not-allowed; }
.wp-qty-value { width:34px; text-align:center; font-weight:700; font-size:.76rem; }
.wp-qty-value.zero { color:var(--text-m); }
.wp-defaults-row { display:grid; grid-template-columns:auto minmax(150px,172px) minmax(190px,1fr) auto; align-items:center; gap:8px; margin-top:8px; }
.wp-defaults-label { font-size:.7rem; font-weight:700; color:var(--text-m); }
.wp-note-field { display:flex; min-width:0; flex-direction:column; gap:2px; }
.wp-defaults-note { width:100%; min-width:0; padding:4px 8px; border:1px solid var(--border); border-radius:var(--r-sm); }
.wp-note-count { color:var(--text-m); font-size:.62rem; line-height:1.15; text-align:right; }
.wp-note-count.is-over-limit { color:var(--danger); font-weight:700; }
.wp-defaults-note.is-over-limit,.wp-step-msg.is-over-limit { border-color:var(--danger); background:color-mix(in srgb,var(--danger) 6%,var(--surface)); }
.wp-step-head,.wp-step-row { display:grid; gap:6px; grid-template-columns:52px 40px minmax(96px,.9fr) 150px 176px minmax(140px,1.1fr) 30px; align-items:center; }
.wp-step-head { padding:7px 10px 6px 36px; margin-top:10px; border-bottom:1px solid var(--border-d); color:var(--text-m); font-size:.62rem; font-weight:700; letter-spacing:.07em; text-transform:uppercase; }
.wp-step-list {
  display: flex; flex-direction: column; gap: 4px;
  max-height: calc(342px + 1px); /* border-box keeps clientHeight at the mockup's 342px */
  padding: 8px 4px 8px 0;
  border-bottom: 1px solid var(--border);
  overflow-y: auto;
}
.wp-step-row { padding:3px 8px; border:1px solid var(--border); border-radius:var(--r-sm); background:var(--surface); flex-shrink:0; }
/* 0649 T#3 (NR0003 §5.3) — the card replaces the old "first row of a type" stripe: one frame per
   single step or instruction+result set, a grip column on the left to move it as a whole. */
.wp-card { display:flex; align-items:stretch; gap:4px; padding:3px 3px 3px 0; border:1px solid transparent; border-left:3px solid #c7d2fe; border-radius:var(--r-sm); flex-shrink:0; }
.wp-card.is-started { border-left-color:var(--text-m); background:color-mix(in srgb,var(--text-m) 5%,transparent); }
.wp-card.is-dragging { opacity:.5; }
.wp-card.drag-over { border-top:2px solid var(--primary); }
.wp-card-grip { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:1px; width:22px; flex-shrink:0; }
.wp-card-handle { display:flex; align-items:center; justify-content:center; width:20px; height:18px; color:var(--text-m); cursor:grab; font-size:.8rem; }
.wp-card-handle.is-disabled { cursor:not-allowed; opacity:.55; }
.wp-card-move { width:20px; height:14px; padding:0; border:0; background:none; color:var(--text-m); cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:.7rem; }
.wp-card-move:hover:not(:disabled) { color:var(--primary); }
.wp-card-move:disabled { opacity:.3; cursor:not-allowed; }
.wp-card-rows { display:flex; flex-direction:column; gap:2px; flex:1; min-width:0; }
.wp-card-drop-end { padding:6px; border:1px dashed var(--border-d); border-radius:var(--r-sm); color:var(--text-m); font-size:.68rem; text-align:center; flex-shrink:0; }
.wp-card-drop-end.drag-over { border-color:var(--primary); color:var(--primary); }
.wp-step-row.is-locked { background:#fbfcfe; }
.wp-step-no { color:var(--text-m); font-size:.66rem; font-weight:700; }
.wp-step-row .doc-tag { min-width:34px; text-align:center; font-size:.6rem; padding:1px 5px; }
.wp-step-label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text); font-size:.74rem; }
.wp-step-label small { color:var(--text-m); font-size:.66rem; }
.wp-step-row .prov-select,.wp-step-row select { width:100%; min-width:0; height:20px; padding:2px 8px; font-size:.74rem; }
.wp-step-msg { width:100%; min-width:0; height:20px; padding:2px 8px; border:1px solid var(--border); border-radius:var(--r-sm); color:var(--text); background:var(--surface); font-size:.74rem; }
.wp-step-msg.is-ai { border-color:#ddd6fe; background:#faf5ff; }
/* T0010 §6 / 시안 deck t17hbdfg v8 — step-name button, review pill, instruction icon: the
   three entry points that open the same per-step drawer (D0007 §6.1). */
.wp-step-no-btn { display:flex; align-items:center; gap:3px; background:none; border:0; padding:0; color:var(--text-m); font-size:.66rem; font-weight:700; cursor:pointer; }
.wp-step-no-btn:hover { color:var(--primary); }
.wp-step-caret { font-size:.68rem; transition:transform .15s ease; }
.wp-step-caret.open { transform:rotate(90deg); }
.wp-col-instr-head { text-align:center; }
.wp-review-pill {
  display:flex; align-items:center; justify-content:center; gap:4px; width:100%; min-width:0;
  height:22px; padding:2px 9px; border:1.5px solid var(--border); border-radius:999px;
  background:var(--surface); color:var(--text-s); font-size:.7rem; font-weight:600; cursor:pointer;
}
.wp-review-pill:hover:not(:disabled) { border-color:var(--primary); }
.wp-review-pill.has-review { border-color:var(--primary); color:var(--primary-h); background:var(--primary-l); }
.wp-review-pill:disabled, .wp-review-pill.is-locked { opacity:.5; cursor:not-allowed; }
.wp-review-pill-text { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.wp-instr-icon {
  width:26px; height:22px; border-radius:var(--r-sm); border:1.5px solid var(--border);
  background:var(--surface); color:var(--text-s); display:flex; align-items:center; justify-content:center;
  cursor:pointer; font-size:.8rem; margin:0 auto;
}
.wp-instr-icon:hover:not(:disabled) { border-color:var(--primary); color:var(--primary); }
.wp-instr-icon.has-value { border-color:var(--primary); color:var(--primary-h); background:var(--primary-l); }
.wp-instr-icon:disabled, .wp-instr-icon.is-locked { opacity:.4; cursor:not-allowed; }
.wp-step-drawer {
  margin:2px 2px 6px; padding:12px 14px; border:1px solid var(--border-d); border-radius:var(--r-sm);
  background:var(--surface-h); display:flex; flex-direction:column; gap:10px;
}
.wp-drawer-block { display:flex; flex-direction:column; gap:6px; }
.wp-drawer-block-hd { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
.wp-drawer-block-title { font-size:.76rem; font-weight:700; color:var(--text); }
.wp-drawer-divider { height:1px; margin:0 -14px; background:var(--border-d); }
.wp-drawer-excluded { display:flex; align-items:center; gap:4px; margin:0; font-size:.7rem; color:var(--text-m); }
.wp-drawer-review-row { display:flex; align-items:center; gap:8px; }
.wp-drawer-review-count { width:96px; flex-shrink:0; height:24px; padding:2px 8px; font-size:.74rem; }
.wp-drawer-select { border:1px solid var(--border); border-radius:var(--r-sm); background:var(--surface); color:var(--text-s); }
.wp-drawer-select:disabled { opacity:.5; }
.wp-drawer-review-count.is-active { border-color:var(--primary); color:var(--primary); font-weight:600; }
.wp-drawer-instr-block { display:flex; flex-direction:column; gap:4px; }
.wp-drawer-mode-label { display:flex; align-items:center; gap:4px; font-size:.7rem; font-weight:700; color:var(--text-m); }
.wp-drawer-textarea-wrap { position:relative; }
.wp-drawer-textarea {
  width:100%; min-height:68px; resize:vertical; padding:8px 10px 22px; font-size:.78rem; line-height:1.5;
  border:1px solid var(--border); border-radius:var(--r-sm); background:var(--surface); color:var(--text); font-family:inherit;
}
.wp-drawer-count { position:absolute; right:8px; bottom:6px; font-size:.62rem; color:var(--text-m); }
.wp-drawer-count.is-over-limit { color:var(--danger); font-weight:700; }
.wp-drawer-file-row { flex-direction:row; align-items:center; gap:8px; flex-wrap:wrap; }
.wp-drawer-file-btn { display:inline-flex; align-items:center; gap:4px; cursor:pointer; white-space:nowrap; }
.wp-drawer-file-btn.is-disabled { opacity:.5; cursor:not-allowed; pointer-events:none; }
.wp-drawer-file-name { font-size:.72rem; color:var(--text-m); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:260px; }
.wp-drawer-file-name.has-file { color:var(--text); font-weight:600; }
.wp-drawer-file-remove {
  width:20px; height:20px; padding:0; border:1px solid var(--border); border-radius:var(--r-sm);
  background:var(--surface); color:var(--text-s); cursor:pointer; display:inline-flex; align-items:center; justify-content:center;
}
.wp-drawer-file-remove:hover { background:var(--danger-l); border-color:var(--danger); color:var(--danger); }
.wp-drawer-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:2px; }
.step-empty { padding:16px; border:1px dashed var(--border-d); border-radius:var(--r); color:var(--text-m); background:var(--surface-h); }
.wp-row-error { border-color:var(--danger,#dc2626); }
.wp-step-errors { grid-column: 1 / -1; display:flex; flex-direction:column; gap:2px; padding:2px 2px 4px; }
.wp-step-error-msg { font-size:.7rem; line-height:1.4; color:var(--danger,#dc2626); white-space:normal; word-break:break-word; }
.wp-layout-narrow .wp-step-head,.wp-layout-narrow .wp-step-row { grid-template-columns:48px 36px 0 minmax(120px,1fr) 150px minmax(140px,1.1fr) 28px; }
.wp-layout-narrow .wp-step-label { visibility:hidden; }
/* Mockup xc32frrg screen 1 — bottom 3 quantity cards */
.wp-sum-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }
.wp-sum-card {
  border: 1px solid var(--border, #e2e8f0); border-radius: var(--r, 6px);
  background: var(--surface-h, #f8fafc); padding: 11px 13px;
}
.wp-sum-label { display: flex; align-items: center; gap: 5px; font-size: .7rem; color: var(--text-m); font-weight: 600; }
.wp-sum-value { font-size: 1.5rem; font-weight: 800; color: var(--text, #1e293b); line-height: 1.25; }
.wp-sum-value small { font-size: .7rem; font-weight: 600; color: var(--text-m); margin-left: 3px; }
.wp-sum-desc { font-size: .66rem; color: var(--text-m); }
.wp-unreadable { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 32px 16px; text-align: center; }
.wp-unreadable-icon { font-size: 2rem; color: var(--danger, #dc2626); }
.wp-unreadable-title { font-weight: 700; }
.wp-unreadable-desc, .wp-unreadable-detail { font-size: .8rem; color: var(--text-m); margin: 0; }
/* main's original rule, superseded by 0599's wider version directly below (same selector,
   later one wins the cascade) — kept so the pre-0599 sizing is still visible in history. */
.wp-unreadable-revisions { margin-top: 8px; font-size: .76rem; color: var(--text-m); text-align: left; }
.wp-unreadable-revisions { margin-top: 8px; width: min(640px, 100%); font-size: .76rem; color: var(--text-m); text-align: left; }
.wp-unreadable-revisions ul { margin: 6px 0 0; padding: 0; list-style: none; }
.wp-unreadable-revisions li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 0; }
/* flowgate.default.0576's row rule, kept alongside 0599's `li` rule above — the row still
   carries both this class and that ancestor, so both authors' spacing choices apply. */
.wp-revision-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 4px 0; }
.wp-restore-btn { flex: 0 0 auto; }
.wp-restore-unavailable, .wp-unreadable-no-baseline { font-size: .74rem; color: var(--text-m); }
.wp-restore-error { margin: 6px 0 0; color: var(--danger, #dc2626); font-size: .78rem; }
.wp-unreadable-raw { margin-top: 10px; width: 100%; max-height: 200px; overflow: auto; background: #0f172a; color: #e2e8f0; padding: 10px; border-radius: var(--r, 6px); font-size: .7rem; text-align: left; }
/* The overlay, the box and the title row belong to the common dialog layer now (T0018);
   only the raw JSON block is still this component's, and it is unchanged. `surface="sheet"`
   is what lets it stay unchanged: dialog.css hands a sheet's body to the feature as a bare
   `display:flex; flex-direction:column; overflow:hidden; padding:0` column — the same box
   `.wp-raw-box` was — so the dark block still bleeds to the surface edge and is still the one
   scroll container, instead of being inset by the panel body's padding and scrolled by it. */
.wp-raw-content { margin: 0; padding: 14px; overflow: auto; font-size: .74rem; background: #0f172a; color: #e2e8f0; flex: 1; }

/* L0010 §1.4: mid tier drops the document column, narrow tier stacks cards. */
.wp-editor { position: relative; }
.wp-layout-mid .wp-col-doc { display: none; }
</style>
