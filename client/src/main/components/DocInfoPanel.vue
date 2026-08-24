<template>
  <aside class="doc-info-panel">
    <!-- Collapsed rail (vertical label) -->
    <button class="doc-panel-rail" @click="$emit('toggle')" :title="t('main.doc_info_panel.expand')">
      <AppIcon name="info" />
      {{ t('main.doc_info_panel.title') }}
    </button>

    <!-- Expanded body -->
    <div class="doc-info-body">
      <!-- Section 1: document status -->
      <div class="dip-section" :class="{ collapsed: sectionCollapsed.status }">
        <div class="dip-section-head">
          <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.status" @click="toggleSection('status')">
            <AppIcon name="caret-down" class="dip-acc-caret" />
            <AppIcon name="radio-button" />
            {{ t('main.doc_info_panel.section_status') }}
          </button>
          <button class="dip-panel-close btn-icon" @click="$emit('toggle')" :title="t('main.doc_info_panel.collapse')">
            <AppIcon name="caret-right" />
          </button>
        </div>
        <div class="dip-sec-body">
          <div :class="['dip-status-badge', statusClass, (nextStep?.visual === 'highlight' || nextStep?.visual === 'current') ? 'dip-badge-clickable' : '']" @click="(nextStep?.visual === 'highlight' || nextStep?.visual === 'current') ? emit('next-action') : undefined">
            <AppIcon :name="statusIcon" />
            {{ statusLabel }}
          </div>
          <p class="dip-status-desc">{{ statusDesc }}</p>
          <div v-if="orphan" class="dip-orphan-warning">
            <AppIcon name="warning" />
            <p>{{ t(recoverableSlot ? 'main.doc_info_panel.orphan_desc' : 'main.doc_info_panel.orphan_no_slot_desc') }}</p>
            <button type="button" class="btn btn-sm btn-primary" :disabled="recovering || !recoverableSlot" @click="recoverOrphan">
              {{ t(recovering ? 'main.doc_info_panel.orphan_recovering' : 'main.doc_info_panel.orphan_recover') }}
            </button>
          </div>
        </div>
      </div>

      <!-- Mockup xc32frrg screen 1 — the doc-info panel's [Provider assignment (by step)] box.
           0399 M0020 rejection — if there is nothing to show, this box is not drawn at all.
           No "불러오는 중" (loading) text while reading, no "failed to load" text either. An
           empty box sprouting in the sidebar is exactly what read as the "invisible sidebar". -->
      <div v-if="typeCode === 'WP' && wpAssignmentsShown" class="dip-section" :class="{ collapsed: sectionCollapsed.wp_assignments }">
        <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.wp_assignments" @click="toggleSection('wp_assignments')">
          <AppIcon name="caret-down" class="dip-acc-caret" />
          <AppIcon name="robot" />
          {{ t('main.doc_info_panel.wp_assignments') }}
        </button>
        <div class="dip-sec-body">
          <ul v-if="wpAssignments.length" class="dip-wp-assignments">
            <li v-for="row in wpAssignments" :key="row.provider_id">
              <span class="dip-wp-dot"></span>
              <span class="dip-wp-prov">{{ row.display_name }}</span>
              <strong>{{ t('main.doc_info_panel.wp_assignment_steps', { n: row.step_count }) }}</strong>
            </li>
          </ul>
          <p v-if="wpUnassignedSteps > 0" class="dip-qa-error">
            {{ t('main.doc_info_panel.wp_unassigned_steps', { n: wpUnassignedSteps }) }}
          </p>
        </div>
      </div>

      <!-- Section 1.5: source-change summary (0325 R0001 / N0004 §2·§3).
           Only at final approval (AC), it fills the space left empty once query/answer,
           AI review comments, and rejection reasons are gone. It's the one place, on the
           screen that decides "머지할까 말까" (merge or not), to see what and how much
           this group changed.
           Per N0004 §2, no per-directory file-count list is included — it would only
           lengthen the sidebar without helping the decision. -->
      <div v-if="canShowChangesSection" class="dip-section" :class="{ collapsed: sectionCollapsed.changes }">
        <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.changes" @click="toggleSection('changes')">
          <AppIcon name="caret-down" class="dip-acc-caret" />
          <AppIcon name="git-diff" />
          {{ t('main.doc_info_panel.section_changes') }}
        </button>
        <div class="dip-sec-body">
          <div v-if="changesLoading" class="dip-qa-hint">{{ t('common.loading') }}</div>
          <div v-else-if="changesError" class="dip-qa-error">{{ t('main.doc_info_panel.changes_failed') }}</div>
          <div v-else-if="changeSummary.total === 0" class="dip-reject-empty">
            <AppIcon name="check-circle" />
            <span>{{ t('main.doc_info_panel.changes_empty') }}</span>
          </div>
          <template v-else>
            <div class="dip-chg-headline">
              <strong class="dip-chg-files">{{ t('main.doc_info_panel.changes_files', { n: changeSummary.total }) }}</strong>
              <span v-if="changeSummary.lineStatsKnown" class="dip-chg-lines">
                <span class="dip-chg-add">+{{ changeSummary.insertions.toLocaleString() }}</span>
                <span class="dip-chg-del">−{{ changeSummary.deletions.toLocaleString() }}</span>
              </span>
            </div>
            <ul class="dip-chg-kinds">
              <li v-for="kind in changeKinds" :key="kind.key" class="dip-chg-kind">
                <span class="dip-chg-badge" :class="`dip-chg-${kind.key}`">{{ kind.badge }}</span>
                <span class="dip-chg-kind-label">{{ t(`main.doc_info_panel.changes_kind_${kind.key}`) }}</span>
                <span class="dip-chg-kind-count">{{ kind.count }}</span>
              </li>
            </ul>
            <p v-if="!changeSummary.lineStatsKnown" class="dip-chg-note">
              {{ t('main.doc_info_panel.changes_lines_unknown') }}
            </p>
            <p v-if="aheadBehindText" class="dip-chg-branch">
              <AppIcon name="git-branch" />
              {{ aheadBehindText }}
            </p>
            <!-- 0325 TR0007 rev1 (reflecting the rejection): the mockup's [Open changes].
                 The summary only answers "몇 파일 · 몇 줄" (how many files / lines), while what
                 R0001 asked — "소스가 잘 됐는지" (whether the source is actually right) — needs
                 an actual diff read. This button is that entry point, and the screen it opens
                 is an overlay that does not replace the approval screen, so closing it returns
                 right back here. -->
            <button type="button" class="dip-chg-open" @click="changesDialogOpen = true">
              <AppIcon name="arrow-square-out" />
              {{ t('main.doc_info_panel.changes_open') }}
            </button>
          </template>
        </div>
      </div>

      <!-- Section 2: query (0311 T0004 rev1 §1 — a standalone section split back out
           from the rejection section).
           The qa+reject merge that rev0 directed was reverted by a rejection saying
           "합칠 대상이 잘못됐다" (the wrong things were merged).
           rev3 rejection ("현재 적용되어있는 스타일을 전혀 사용하지 않는다" — none of the
           currently-applied styling is used at all): this section's markup and classes are
           exactly what is actually applied on screen today — .dip-qa-card (amber card)
           · .dip-qa-card-title/-body · .dip-qa-opt-list (option preview) · mini-action
           primary [답변] · answered-card. Nothing new was created.
           NR0003 §5-3 only added the cap "최신 N건 + 나머지는 전체보기로" (latest N, the
           rest via full view), and that link also reuses this file's existing
           .dip-ai-history-link idiom as-is.
           The show condition stays canShowQaSection as before — hidden only on AC
           documents. -->
      <div v-if="canShowQaSection" class="dip-section" :class="{ collapsed: sectionCollapsed.qa }">
        <div class="dip-qa-headline">
          <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.qa" @click="toggleSection('qa')">
            <AppIcon name="caret-down" class="dip-acc-caret" />
            <AppIcon name="chats" />
            {{ t('main.doc_info_panel.section_qa') }}
          </button>
          <div class="dip-qa-head-actions">
            <span v-if="qaUnansweredCount > 0" class="dip-qa-count">{{ t('main.doc_info_panel.qa_unanswered_count', { n: qaUnansweredCount }) }}</span>
            <button v-if="qaItems.length > 0" class="dip-qa-act dip-qa-fullview" type="button" @click="openQaFull(null, false)" :title="t('main.doc_info_panel.qa_view_full')">
              <AppIcon name="corners-out" />
              {{ t('main.doc_info_panel.qa_view_full') }}
            </button>
            <button class="dip-qa-act dip-qa-act--icon dip-qa-add" type="button" @click="toggleNewQ" :title="t('main.doc_info_panel.qa_add')">
              <AppIcon name="plus" />
            </button>
          </div>
        </div>

        <div class="dip-sec-body">
        <!-- new question inline form ([+ Query]) -->
        <div v-if="newQOpen" class="dip-qa-form">
          <input v-model="newQTitle" class="dip-qa-input" :placeholder="t('main.doc_info_panel.qa_title_ph')" />
          <textarea v-model="newQBody" class="dip-qa-textarea" rows="3" :placeholder="t('main.doc_info_panel.qa_body_ph')"></textarea>
          <!-- group 0243 R0001: optional option editor. Add none and the query is the
               pre-extension one. -->
          <div class="dip-qa-opt-edit">
            <div v-for="(_, idx) in newQOptions" :key="idx" class="dip-qa-opt-row">
              <input
                v-model="newQOptions[idx]"
                class="dip-qa-input"
                :placeholder="t('main.doc_info_panel.qa_option_ph', { n: idx + 1 })"
                :maxlength="200"
              />
              <button
                class="dip-qa-opt-del"
                type="button"
                :title="t('main.doc_info_panel.qa_option_remove')"
                @click="removeQOption(idx)"
              >
                <AppIcon name="x" />
              </button>
            </div>
            <button
              v-if="newQOptions.length < QA_MAX_OPTIONS"
              class="dip-qa-opt-add"
              type="button"
              @click="addQOption"
            >
              <AppIcon name="plus" /> {{ t('main.doc_info_panel.qa_option_add') }}
            </button>
          </div>
          <div class="dip-qa-form-actions">
            <button class="btn btn-sm btn-outline" type="button" @click="toggleNewQ">{{ t('common.cancel') }}</button>
            <button class="btn btn-sm btn-primary" type="button" :disabled="!newQBody.trim() || qaBusy" @click="submitNewQ">{{ t('main.doc_info_panel.qa_register') }}</button>
          </div>
        </div>

        <div v-if="qaLoading" class="dip-qa-hint">{{ t('common.loading') }}</div>
        <div v-else-if="qaError" class="dip-qa-error">{{ qaError }}</div>
        <template v-else>
          <div v-if="qaFeed.length === 0" class="dip-reject-empty">
            <AppIcon name="question" />
            <span>{{ t('main.doc_info_panel.qa_empty') }}</span>
          </div>
          <div
            v-for="item in qaFeedVisible"
            :key="item.id"
            class="dip-qa-card"
            :class="{ 'answered-card': itemAnswered(item) }"
          >
            <strong class="dip-qa-card-title">Q{{ item.seq }} · {{ item.title || item.body }}</strong>
            <p class="dip-qa-card-body">{{ item.body }}</p>
            <!-- group 0243 R0001: the card previews the options; picking one happens in the
                 full view, which [답변] opens. -->
            <ul v-if="(item.options?.length ?? 0) > 0" class="dip-qa-opt-list">
              <li v-for="opt in item.options" :key="opt.id" class="dip-qa-opt">{{ opt.label }}</li>
            </ul>
            <div class="dip-qa-card-actions">
              <button class="mini-action primary" type="button" @click="openQaFull(item.id, true)">
                <AppIcon name="arrow-bend-up-left" />
                {{ t('main.doc_info_panel.qa_answer') }}
              </button>
            </div>
          </div>
          <!-- rev5 rejection §3: the "이전 항목 N건 더 — 전체보기에서 확인" (N more
               earlier items — check via full view) line is gone. The only door to the
               overflow items is the [전체보기] on the header row's right side. -->
        </template>
        </div>
      </div>

      <!-- Section 2.5: AI review · rejection (0311 T0004 rev1 §2 — the pair that
           actually belongs merged together this time).
           The two sections' show conditions were identical down to the letter
           (canShowRejectSection ≡ canShowReviewSection), both already used the same
           "latest 1 + history link" pattern, and both carry a real timestamp column
           (rejected_at · reviewed_at/created_at) so no ordering has to be invented —
           hence merging them into a single chronological feed.

           rev3 rejection ("현재 적용되어있는 스타일을 전혀 사용하지 않는다 / 반려·대응이
           그렇게 되어있던가 / 작업검수가 이중박스로 되어있던가" — none of the currently-
           applied styling is used at all / is the rejection·response really laid out like
           that / is the review really a double box): even after merging, each card is
           exactly the markup already applied on screen. Rejection = .dip-reject-quote (a
           quote box that folds when its author/date header row is clicked) + the sibling
           .dip-ai-response thread attached beneath it; AI review = .dip-ai-entry. The new
           wrapper box around the cards (rev0's .dip-mix-card) is gone — that was exactly
           what turned the AI review into a double box. The only style added for the merge
           is the inter-item spacing rule (.dip-rr-entry).

           rev4 rejection: (1) the AI review item's header row (.dip-ai-entry-head/
           .dip-ai-meta), verdict badge (.dip-ai-verdict), and findings list
           (.dip-ai-findings) were dropped from the panel at that time. Later, 0422
           TR0003 rev2 restored just the verdict badge inside .dip-ai-comment-toggle.
           (2) the rejection card's name slot is 「반려」(rejection), and its
           .dip-ai-comment is 「검수 의견」(review comment) (only the i18n value changed).
           (3) instead of an "이전 항목 N건 더" (N more earlier items) line under the body,
           there is a [전체보기] to the right of the title. (4) capped at 3, and long
           strings are ellipsized whether folded or expanded. -->
      <div v-if="canShowReviewRejectSection" class="dip-section" :class="{ collapsed: sectionCollapsed.ai_review }">
        <!-- rev5 rejection §3: [전체보기] sits to the right of the title. The header row
             itself is exactly .dip-qa-headline / .dip-qa-head-actions / .dip-qa-act, already
             used by the query section right above it — this section's button only adds its
             own hook class (.dip-rr-fullview) on top.
             rev6 rejection §1·§3: this [전체보기] opens a different dialog than the query
             section (the review/rejection-only QaReviewHistoryDialog) — see
             openReviewRejectFull. -->
        <div class="dip-qa-headline">
          <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.ai_review" @click="toggleSection('ai_review')">
            <AppIcon name="caret-down" class="dip-acc-caret" />
            <AppIcon name="robot" />
            {{ t('main.doc_info_panel.section_review_reject') }}
          </button>
          <div class="dip-qa-head-actions">
            <button v-if="reviewRejectHasHistory" class="dip-qa-act dip-rr-fullview" type="button" @click="openReviewRejectFull" :title="t('main.doc_info_panel.qa_view_full')">
              <AppIcon name="corners-out" />
              {{ t('main.doc_info_panel.qa_view_full') }}
            </button>
            <!-- 0419 T0006 (NR0003 follow-up T recommendation 2 / TR0005 rev1
                 rejection): the entry point for re-editing the rejection message sits
                 here, in this sidebar header row that already shows the rejection reason,
                 not in the action bar. It only appears in the rejected state — once a
                 rework submission moves past it (revised), the past rejection reason is
                 not retroactively editable. -->
            <button v-if="canEditRejection" class="dip-qa-act dip-rr-edit" type="button" @click="emit('edit-rejection')" :title="t('common.edit')">
              <AppIcon name="pencil-simple" />
              {{ t('common.edit') }}
            </button>
          </div>
        </div>
        <div class="dip-sec-body">
        <template v-if="reviewRejectFeed.length > 0">
          <div v-for="entry in reviewRejectFeedVisible" :key="entry.key" class="dip-rr-entry">
            <!-- Rejection — exactly the markup of the former rejection-reason section -->
            <template v-if="entry.kind === 'reject'">
              <div class="dip-reject-quote" :class="{ open: foldOpen.reason[entry.key] }">
                <button
                  class="dip-reject-quote-toggle"
                  type="button"
                  :aria-expanded="!!foldOpen.reason[entry.key]"
                  :title="t(foldOpen.reason[entry.key] ? 'main.doc_info_panel.rejection_collapse' : 'main.doc_info_panel.rejection_expand')"
                  @click="toggleFold('reason', entry.key)"
                >
                  <span class="dip-reject-quote-author">
                    <AppIcon name="user-gear" />
                    <strong>{{ rejectedByDisplay(entry.reject!.rejected_by) || t('main.doc_info_panel.rejection_review_author') }}</strong>
                  </span>
                  <span v-if="entry.reject!.rejected_at" class="dip-reject-date">{{ formatRejectionDate(entry.reject!.rejected_at) }}</span>
                  <AppIcon name="caret-down" class="dip-reject-chevron" />
                </button>
                <div class="dip-reject-quote-body">
                  <span class="dip-reject-reason">{{ entry.reject!.reason }}</span>
                </div>
              </div>

              <!-- P0005/T0006: the AI's response to THIS rejection, threaded as a reply
                   directly under the quote (a sibling, not nested inside it) — the same
                   placement the standalone rejection section already used. -->
              <div v-if="entry.reject!.ai_response" class="dip-ai-response" :class="{ open: foldOpen.response[entry.key] }">
                <button
                  type="button"
                  class="dip-ai-response-head"
                  :aria-expanded="!!foldOpen.response[entry.key]"
                  :title="t(foldOpen.response[entry.key] ? 'main.doc_info_panel.rejection_collapse' : 'main.doc_info_panel.rejection_expand')"
                  @click="toggleFold('response', entry.key)"
                >
                  <span class="dip-ai-response-label">
                    <AppIcon name="arrow-bend-up-left" class="dip-ai-response-thread" />
                    <AppIcon name="robot" /> {{ t('main.doc_info_panel.ai_response_label') }}
                  </span>
                  <span v-if="entry.reject!.responded_at" class="dip-ai-response-date">{{ formatRejectionDate(entry.reject!.responded_at) }}</span>
                  <AppIcon name="caret-down" class="dip-ai-response-chevron" />
                </button>
                <div class="dip-ai-response-body">{{ entry.reject!.ai_response }}</div>
              </div>
            </template>

            <!-- AI review — exactly the .dip-ai-entry of the former AI review section
                 (no wrapper box).
                 rev5 rejection §1: the header row (verdict · AI) and findings list are not
                 drawn in the panel — the verdict and finding detail can still be seen as-is
                 in the [전체보기] window.
                 0422 TR0003 rev2 rejection ("dip-ai-comment-toggle 여기" — [it belongs] here
                 in dip-ai-comment-toggle): the badge R0001 asked for goes inside each review
                 comment's toggle header row, not the section headline or outside the card.
                 It draws the same value (verdict/finding_count) as the [전체보기] dialog's
                 (QaReviewHistoryDialog) .rhd-verdict, inside each .dip-ai-comment-toggle. -->
            <div v-else class="dip-ai-entry">
              <!-- R0001 (rev1): the comment fold uses the SAME control idiom as the
                   rejection reason and the AI response — a clickable header row carrying a
                   label + chevron over a clamped body. rev5 rejection §4: opening widens the
                   clamp from 2 lines to 6 instead of turning it into a scroll box, so a
                   long review comment always ends in an ellipsis. Accent colour stays amber. -->
              <div v-if="entry.review!.comment" class="dip-ai-comment" :class="{ open: foldOpen.comment[entry.key] }">
                <button
                  type="button"
                  class="dip-ai-comment-toggle"
                  :aria-expanded="!!foldOpen.comment[entry.key]"
                  :title="t(foldOpen.comment[entry.key] ? 'main.doc_info_panel.ai_comment_collapse' : 'main.doc_info_panel.ai_comment_expand')"
                  @click="toggleFold('comment', entry.key)"
                >
                  <span class="dip-ai-comment-label">
                    <AppIcon name="robot" /> {{ t('main.doc_info_panel.ai_comment_label') }}
                  </span>
                  <span class="dip-ai-verdict" :class="reviewVerdictClass(entry.review!)">
                    {{ reviewVerdictLabel(entry.review!) }}
                  </span>
                  <AppIcon name="caret-down" class="dip-ai-comment-chevron" />
                </button>
                <div class="dip-ai-comment-body">{{ entry.review!.comment }}</div>
              </div>
            </div>
          </div>

          <!-- rev5 rejection §3: the "이전 항목 N건 더 / 전체 보기" (N more earlier items
               / view all) link under the body is gone. The door to the full history is the
               [전체보기] on the header row's right side. -->
        </template>
        <template v-else>
          <div class="dip-reject-empty">
            <AppIcon name="chat-circle-dots" />
            <span>{{ t('main.doc_info_panel.review_reject_empty') }}</span>
            <span class="dip-reject-hint">{{ t('main.doc_info_panel.review_reject_hint') }}</span>
          </div>
        </template>
        </div>
      </div>

      <!-- Section 2.5: TR work-scope verification (0299 D0004 §6).
           Folded when the result is pass with no reasons — if this card were expanded on
           a normal submission, a list with nothing worth reading would take up space on
           every document. It is only shown expanded on a warning or reject. -->
      <div v-if="trScope" class="dip-section" :class="{ collapsed: sectionCollapsed.tr_scope }">
        <button type="button" class="dip-section-title dip-sec-toggle" :aria-expanded="!sectionCollapsed.tr_scope" @click="toggleSection('tr_scope')">
          <AppIcon name="caret-down" class="dip-acc-caret" />
          <AppIcon name="git-branch" />
          {{ t('main.doc_info_panel.section_tr_scope') }}
        </button>
        <div class="dip-sec-body">
          <div class="dip-trs-head">
            <span class="dip-trs-verdict" :class="`dip-trs-${trScope.verdict}`">
              {{ t(`main.doc_info_panel.tr_scope_verdict_${trScope.verdict}`) }}
            </span>
            <span v-if="trScope.stage" class="dip-trs-stage">
              {{ t(`main.doc_info_panel.tr_scope_stage_${trScope.stage}`) }}
            </span>
          </div>
          <p v-if="trScope.branch" class="dip-trs-assign">
            {{ t('main.doc_info_panel.tr_scope_branch') }}: <code>{{ trScope.branch }}</code>
          </p>
          <!-- A document where verification did not run at submission time, so there is
               no comparison result. Instead of hiding the section, this states why there is
               no verdict and shows only the list reported in the body. rev3: a document
               whose body has no changed-files section at all (a TS submitted before it
               became a verification target, so it never got the guidance to write that
               section — normal for it to be missing) is also not hidden; it states plainly
               that "절이 없다" (there is no section). -->
          <p v-if="trScopeUnevaluated" class="dip-trs-unevaluated">
            {{ t(`main.doc_info_panel.${trScopeUnevaluatedKey}`) }}
          </p>

          <ul v-if="trScope.codes?.length" class="dip-trs-codes">
            <li v-for="code in trScope.codes" :key="code">
              <strong>{{ code }}</strong> — {{ t(`main.doc_info_panel.tr_scope_code_${code.replace('-', '_').toLowerCase()}`) }}
            </li>
          </ul>

          <!-- Mismatched items come first. The full reported/detected lists come after,
               laid out in the same shape side by side so they can be visually compared. -->
          <div v-for="key in trScopeDiffKeys" :key="key">
            <template v-if="trScope[key]?.count">
              <p class="dip-trs-list-label dip-trs-mismatch">
                {{ t(`main.doc_info_panel.tr_scope_${key}`) }} ({{ trScope[key].count }})
              </p>
              <ul class="dip-trs-list dip-trs-mismatch">
                <li v-for="p in trScope[key].items" :key="p"><code>{{ p }}</code></li>
                <li v-if="trScope[key].count > trScope[key].items.length" class="dip-trs-more">
                  {{ t('main.doc_info_panel.tr_scope_more', { n: trScope[key].count - trScope[key].items.length }) }}
                </li>
              </ul>
            </template>
          </div>

          <div v-for="key in visibleTrScopeAllKeys" :key="key">
            <p class="dip-trs-list-label">
              {{ t(`main.doc_info_panel.tr_scope_${key}`) }} ({{ trScopeSlice(key).count }})
            </p>
            <ul class="dip-trs-list">
              <li v-for="p in trScopeSlice(key).items" :key="p"><code>{{ p }}</code></li>
              <li v-if="!trScopeSlice(key).count" class="dip-trs-more">
                {{ t('main.doc_info_panel.tr_scope_empty') }}
              </li>
              <li
                v-else-if="trScopeSlice(key).count > trScopeSlice(key).items.length"
                class="dip-trs-more"
              >
                {{ t('main.doc_info_panel.tr_scope_more', { n: trScopeSlice(key).count - trScopeSlice(key).items.length }) }}
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>

    <!-- 0311 T0004 / TR0005 rev6 rejection §3 ("질의는 빼라" — leave query out): the
         query full view again uses its own dedicated dialog (QaHistoryDialog). The query
         headline's [전체보기] and each card's [답변] open this dialog with the doc-id
         context. -->
    <QaHistoryDialog
      v-model:visible="qaHistoryVisible"
      :items="qaItems"
      :doc-id="props.docId"
      :busy="qaBusy"
      :focus-id="qaFocusId"
      :start-answer="qaStartAnswer"
      :submit-answer="submitAnswerCore"
      :request-ai-answer="requestAiAnswer"
      :ai-providers="aiProviderStore.providers"
      :selected-provider-id="aiProviderStore.selectedProviderId"
      :provider-loading="aiProviderStore.loading"
      :provider-errored="!!aiProviderStore.error"
      :select-provider="aiProviderStore.selectProvider"
      :copy-answer-mention="copyAnswerMention"
      :ai-run-item-id="aiRunItemId"
    />

    <!-- Review/rejection-only "전체보기" (full view) — opens only the [전체보기] of the
         AI review · rejection section. -->
    <QaReviewHistoryDialog
      v-model:visible="reviewRejectHistoryVisible"
      :reviews="props.aiReviewHistory ?? []"
      :rejections="props.rejectionHistory ?? []"
      :rejected-by-display="rejectedByDisplay"
    />

    <!-- 0325 TR0007 rev1 — what [변경사항 열기] opens: the file list + unified/split
         diff of everything this group changed against its base. Mounted lazily so a
         reviewer who never opens it pays for no diff request at all. -->
    <GroupChangesDialog
      v-if="changesDialogOpen && props.groupId"
      :project-id="changesProjectId"
      :group-id="props.groupId"
      :branch="changesBranch"
      :base-branch="changesBaseBranch"
      :changes="changes"
      :tool-artifacts="changesToolArtifacts"
      @close="changesDialogOpen = false"
    />
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import QaHistoryDialog from './QaHistoryDialog.vue'
import QaReviewHistoryDialog from './QaReviewHistoryDialog.vue'
import GroupChangesDialog from './GroupChangesDialog.vue'
import { useQaAnswers, type QaItem } from '../composables/useQaAnswers'
import { useAiProviderStore } from '../stores/aiProvider'
import { useExplorerStore, type GroupChangeData } from '../stores/explorer'
import { useToast } from './common/useToast'
import { useMentionCopy } from '../composables/useMentionCopy'
import { ClipboardAbort, copyToClipboardDeferred } from '../utils/clipboard'
import type { StepState } from '../workflow/workflowViewState'
import type { AiReview } from '../types/aiReview'
import type { RejectionHistoryItem } from '../composables/useFlowGateToken'
import type { TrScopePathSlice, TrScopeVerdict } from '../types/trScope'

const { t } = useI18n()
const aiProviderStore = useAiProviderStore()

const props = defineProps<{
  docId: string
  typeCode: string | null
  // 0325 T0006: the group this document belongs to, used ONLY by the AC
  // source-change summary. Optional so every non-AC mount (and every existing
  // test) keeps working without it.
  groupId?: string | null
  reviewStatus: string | null
  rejectReason: string | null
  rejectionHistory?: RejectionHistoryItem[]
  aiReview?: AiReview | null
  aiReviewHistory?: AiReview[]
  // TR work-scope verification result (0299 D0004 §6). The server unpacks this from documents.meta.
  trScope?: TrScopeVerdict | null
  qStatus?: string | null
  workflowSteps?: string[] | null
  orphan?: boolean
  // 0457 T0009: item_seq/type/empty triples for the recover button — GET .../relations
  // `workflow.candidate_slots`. Whichever the first typeCode-matching empty slot is (the
  // array already arrives sort_order ASC) is the recover target.
  candidateSlots?: Array<{ item_seq: number | null; type: string | null; empty: boolean }> | null
  selfIndex?: number | null
  stepStates: StepState[]
  nextStepIndex: number | null
  collapsed: boolean
}>()

const emit = defineEmits<{
  toggle: []
  'next-action': []
  'orphan-recovered': []
  // 0419 T0006: sidebar [수정] entry point for correcting the latest rejection's
  // wording. No payload — the parent already has this doc's id/existing reason.
  'edit-rejection': []
}>()

// ── R0001 (group 0126 / option C): section-level accordion ──────────────────────────
// Each info-panel section (status · query · AI review·rejection) folds independently under its
// own title caret — the same caret idiom as the left file-tree. This is separate from
// the whole-panel collapse (the `dip-panel-close` chevron / `toggle` emit) so the two
// controls don't fight. Sections start expanded.
// 0311 T0004 rev1 §2: 'ai_review' is now the MERGED AI review·rejection section's key. The old
// standalone 'reject' key is dropped — a repo-wide grep found no other reference to it
// (it had already been left dangling with no section of its own).
type SectionKey = 'status' | 'wp_assignments' | 'qa' | 'ai_review' | 'tr_scope' | 'changes'
const sectionCollapsed = reactive<Record<SectionKey, boolean>>({
  status: false,
  // Mockup xc32frrg screen 1 draws this box already expanded.
  wp_assignments: false,
  qa: false,
  ai_review: false,
  // Folded when the result is pass with no reasons (D0004 §6). The watch below opens it based on the verdict.
  tr_scope: true,
  // 0325 T0006: this section only appears on AC, and the very reason it appears is "지금 보라" (look now), so it starts expanded.
  changes: false,
})

interface WorkPlanAssignment { provider_id: string; display_name: string; step_count: number }
const wpAssignments = ref<WorkPlanAssignment[]>([])
const wpUnassignedSteps = ref(0)
// 0399 M0020 rejection — the box appears only once there is actually something to
// show. It takes no space while loading or if loading failed, so the sidebar does not
// grow and shrink when a document is opened.
const wpAssignmentsShown = computed(() => wpAssignments.value.length > 0 || wpUnassignedSteps.value > 0)

async function fetchWpAssignments() {
  if (props.typeCode !== 'WP') return
  try {
    const res = await getRequest<any>('/api/v1/documents/' + encodeURIComponent(props.docId) + '/work-plan')
    wpAssignments.value = res.data.assignment_summary ?? []
    wpUnassignedSteps.value = res.data.unassigned_step_count ?? 0
  } catch {
    wpAssignments.value = []
    wpUnassignedSteps.value = 0
  }
}

watch(
  () => [props.docId, props.typeCode] as const,
  () => { void fetchWpAssignments() },
  { immediate: true },
)

// 0434 B0001 ("F5를 누르지 않으면 적용되지 않음") — 이 칸은 문서를 여는 순간 한 번 읽은
// 값을 그대로 들고 있었다. 바로 옆 작업계획 표에서 공급자를 배정하거나 수량을 고치고
// [저장]을 눌러도 이 칸은 이전 숫자를 그대로 그렸고, 바뀐 값은 F5 뒤에야 보였다 —
// 사람이 보기엔 배정이 적용되지 않은 것이다. 서버는 계획 저장마다
// document_explorer_refresh(operation='updated')를 보내고 useFlowGateSse가 그것을
// fg:document_content_changed로 바꿔 넣어 준다. 아래 Q&A 칸이 fg:qa_refresh를 듣는 것과
// 같은 얼개로 그 이벤트를 들으면, 내 저장이든 AI 워커의 채우기든 같은 경로로 다시 읽힌다.
// 지금 보고 있는 문서의 것만 받는다.
function _onWorkPlanDocChanged(e: Event) {
  const detail = (e as CustomEvent).detail as { doc_id?: string } | undefined
  if (detail?.doc_id && detail.doc_id !== props.docId) return
  void fetchWpAssignments()
}
onMounted(() => window.addEventListener('fg:document_content_changed', _onWorkPlanDocChanged))
onBeforeUnmount(() => window.removeEventListener('fg:document_content_changed', _onWorkPlanDocChanged))

// Mismatched items — shown prominently, before the full reported/detected lists (D0004 §6).
const trScopeDiffKeys = ['out_of_scope', 'unconfirmed', 'unreported', 'format_errors'] as const

// Full reported/detected lists — placed side by side in the same shape after the
// mismatch list (D0004 §6). Left as an inline array literal, the keys would infer as
// string and block indexing, so this keeps a literal union with as const, same as the
// mismatch list above.
const trScopeAllKeys = ['reported', 'detected'] as const

// 0390 TR0005 rev2 — a document where verification did not run at submission time has
// no detected list at all. Drawing "감지 0건" (0 detected) then would be the lie
// "변경이 없었다" (nothing changed), so only the reported list is kept, with a one-line
// note attached explaining why there is no comparison result.
const trScopeUnevaluated = computed(() => props.trScope?.evaluated === false)
// 0390 TR0005 rev3 — there are two reasons for being unverified, and the notice text
// must differ. If the body has no changed-files section at all, a sentence pointing at
// the "신고된 파일" (reported files) list would be false, so a separate sentence states
// plainly that the section is missing.
const trScopeUnevaluatedKey = computed(() =>
  props.trScope?.scope_reason === 'not_evaluated_no_section'
    ? 'tr_scope_unevaluated_no_section'
    : 'tr_scope_unevaluated',
)
const visibleTrScopeAllKeys = computed(() =>
  trScopeUnevaluated.value ? (['reported'] as const) : trScopeAllKeys,
)

// A missing slice is returned filled in as empty. The template does not have to rely
// on optional chaining and v-if/v-else-if narrowing, and the "0건" (0 items) display
// still holds up as-is.
const emptyTrScopeSlice: TrScopePathSlice = { count: 0, items: [] }
function trScopeSlice(key: (typeof trScopeAllKeys)[number]): TrScopePathSlice {
  return props.trScope?.[key] ?? emptyTrScopeSlice
}

const trScopeAutoExpanded = computed(() =>
  !!props.trScope
  && (props.trScope.verdict !== 'pass' || (props.trScope.codes?.length ?? 0) > 0),
)

watch(
  [() => props.docId, trScopeAutoExpanded],
  ([, expanded]) => {
    // Expanded when the verdict is a warning/reject, or there is even one reason code.
    // The observation stage records it as a pass but the reasons still remain, so this
    // expands then too, letting the operator preview what will trip before advancing the
    // stage.
    sectionCollapsed.tr_scope = !expanded
  },
  { immediate: true },
)
function toggleSection(key: SectionKey) {
  sectionCollapsed[key] = !sectionCollapsed[key]
}

function formatRejectionDate(iso: string): string {
  try {
    const d = new Date(iso)
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${mm}-${dd} ${hh}:${min}`
  } catch {
    return iso
  }
}

// Newest first. The merged AI review·rejection feed re-sorts by real timestamp anyway, but this
// keeps the pre-sort deterministic when several rejections share a timestamp.
const rejectionHistoryList = computed(() => [...(props.rejectionHistory ?? [])].reverse())

// rejected_by is a user UUID; resolve it to a display name (same /api/v1/users/{id}
// pattern as DocHeader.fetchOwner) so a rejection line never shows a raw UUID.
// An empty cache entry means "resolved but unknown" -> show nothing rather than the UUID.
const rejectedByNames = ref<Record<string, string>>({})

async function resolveRejectedBy(userId: string) {
  if (!userId || rejectedByNames.value[userId] !== undefined) return
  rejectedByNames.value = { ...rejectedByNames.value, [userId]: '' } // mark in-flight
  try {
    const res = await getRequest<any>(`/api/v1/users/${encodeURIComponent(userId)}`)
    const user = (res.data as any)?.data ?? res.data
    const name = user?.username ?? user?.display_name ?? ''
    rejectedByNames.value = { ...rejectedByNames.value, [userId]: name }
  } catch {
    rejectedByNames.value = { ...rejectedByNames.value, [userId]: '' }
  }
}

function rejectedByDisplay(userId: string | null | undefined): string {
  if (!userId) return ''
  return rejectedByNames.value[userId] || ''
}

// 0311 T0004: the merged full-history dialog lists EVERY rejection, not just the
// latest, so every distinct rejected_by across the whole history needs resolving
// (the old single-fold panel only ever needed the latest one).
watch(
  () => rejectionHistoryList.value.map((r) => r.rejected_by),
  (ids) => { for (const id of ids) if (id) void resolveRejectedBy(id) },
  { immediate: true },
)

const isRootWorkflowUndecided = computed(
  () =>
    ['R', 'B'].includes(props.typeCode ?? '') &&
    (props.workflowSteps == null || props.workflowSteps.length === 0),
)

const statusClass = computed(() => {
  if (isQDoc.value) return isQDone.value ? 'approved' : 'wf-in-progress'
  switch (effectiveStatus.value) {
    case 'pending_review': return 'review-pending'
    case 'approved':       return 'approved'
    case 'rejected':       return 'rejected'
    case 'revised':        return 'revised'
    case 'wf_in_progress': return 'wf-in-progress'
    case 'wf_done':        return 'wf-done'
    default:               return isRootWorkflowUndecided.value ? 'not-decided' : 'review-pending'
  }
})

const statusIcon = computed(() => {
  if (isQDoc.value) return isQDone.value ? 'check-circle' : 'clock'
  switch (effectiveStatus.value) {
    case 'pending_review': return 'hourglass-medium'
    case 'approved':       return 'check-circle'
    case 'rejected':       return 'x-circle'
    case 'revised':        return 'arrows-clockwise'
    case 'wf_in_progress': return 'play'
    case 'wf_done':        return 'check-circle'
    default:               return isRootWorkflowUndecided.value ? 'question' : 'hourglass-medium'
  }
})

const statusLabel = computed(() => {
  if (isQDoc.value) {
    return isQDone.value
      ? t('main.doc_info_panel.status_q_done')
      : t('main.doc_info_panel.status_q_answering')
  }
  switch (effectiveStatus.value) {
    case 'pending_review': return t('main.doc_info_panel.status_pending')
    case 'approved':       return t('main.doc_info_panel.status_approved')
    case 'rejected':       return t('main.doc_info_panel.status_rejected')
    case 'revised':        return t('main.doc_info_panel.status_revised')
    case 'wf_in_progress': return t('main.doc_info_panel.status_wf_in_progress')
    case 'wf_done':        return t('main.doc_info_panel.status_wf_done')
    default:
      return isRootWorkflowUndecided.value
        ? t('main.doc_info_panel.status_not_decided')
        : t('main.doc_info_panel.status_pending')
  }
})

const statusDesc = computed(() => {
  if (isQDoc.value) {
    return isQDone.value
      ? t('main.doc_info_panel.status_q_done_desc')
      : t('main.doc_info_panel.status_q_answering_desc')
  }
  switch (props.reviewStatus) {
    case 'pending_review':
      return props.aiReview
        ? t('main.doc_info_panel.status_pending_desc_ai')
        : t('main.doc_info_panel.status_pending_desc')
    case 'approved':       return t('main.doc_info_panel.status_approved_desc')
    case 'rejected':       return t('main.doc_info_panel.status_rejected_desc')
    case 'revised':        return t('main.doc_info_panel.status_revised_desc')
    case 'wf_in_progress': return t('main.doc_info_panel.status_wf_in_progress_desc')
    case 'wf_done':        return t('main.doc_info_panel.status_wf_done_desc')
    default:               return ''
  }
})

const currentTypeCode = computed(() => props.typeCode || 'R')
const isQDoc = computed(() => props.typeCode === 'Q')
const isQDone = computed(() => props.qStatus === 'done')
// 0325 N0004 §3: on the final-approval (AC) screen, query/answer, AI review comments,
// and rejection reasons are hidden and the source-change summary floats in their place.
// All three sections are already-settled matters by that point and would only lengthen
// the scroll.
const isAcDoc = computed(() => props.typeCode === 'AC')
// 0311 T0004 rev1 §1: qa is its own section again — the merge partner was wrong.
// Its show condition never matched reject/ai_review's in the first place (R/B/Q/M
// docs show queries but never a rejection), which is one of the reasons the pairing
// was wrong; only AC hides it.
const canShowQaSection = computed(() => !isAcDoc.value)

// ── AI review·rejection (0311 T0004 rev1 §2: the pair that actually belongs together) ──
// These two conditions are character-for-character identical today, but they are kept
// as two named computeds and OR'd explicitly so the merged section keeps rendering if
// they ever diverge.
const canShowRejectSection = computed(() => !['R', 'B', 'Q', 'M', 'AC'].includes(props.typeCode ?? ''))
const canShowReviewSection = computed(() => !['R', 'B', 'Q', 'M', 'AC'].includes(props.typeCode ?? ''))
const canShowReviewRejectSection = computed(() => canShowRejectSection.value || canShowReviewSection.value)
// 0419 T0006 (NR0003 §"수정 허용 시점" — when editing is allowed): the [수정] entry point only appears while
// the document is currently rejected — once a rework submission moves it past
// 'rejected' (revised/pending_review/approved), the past rejection is history, not
// something to keep correcting. Group-disposed / AI-running / permission checks are
// authoritative on the server (update_rejection_reason_endpoint); this is UX-only.
const canEditRejection = computed(() => props.reviewStatus === 'rejected')

// Per-entry folds. The panel used to render exactly ONE review and ONE rejection, so a
// single ref each was enough; the merged feed renders several cards, so each card's
// comment / AI-response / reason fold is tracked under that card's own feed key.
// rev5 rejection §1: the findings fold is gone — the panel no longer draws the verdict badge
// or the findings list at all, so there is nothing left to open there.
// All three reset when the document changes (see the props.docId watch below).
const foldOpen = reactive<Record<'comment' | 'response' | 'reason', Record<string, boolean>>>({
  comment: {},
  response: {},
  reason: {},
})
function toggleFold(kind: 'comment' | 'response' | 'reason', key: string) {
  foldOpen[kind][key] = !foldOpen[kind][key]
}

// A review's real time column, in the order the server fills them.
function reviewWhen(r: AiReview): string {
  return r.reviewed_at ?? r.created_at ?? ''
}
const isBehindWorkflowHead = computed(() =>
  !['R', 'B'].includes(props.typeCode ?? '') &&
  props.stepStates.find(s => s.code === currentTypeCode.value)?.visual === 'done'
)
const isRootDecided = computed(() =>
  ['R', 'B'].includes(props.typeCode ?? '') && (props.reviewStatus?.startsWith('wf_') ?? false)
)
const isCompletedDoc = computed(() =>
  (isQDoc.value && isQDone.value) ||
  isBehindWorkflowHead.value ||
  isRootDecided.value ||
  props.reviewStatus === 'approved' ||
  props.reviewStatus === 'wf_done'
)

const effectiveStatus = computed(() =>
  isCompletedDoc.value ? 'wf_done' : props.reviewStatus
)

const nextStep = computed(() =>
  props.nextStepIndex != null ? (props.stepStates[props.nextStepIndex] ?? null) : null
)

// ── 0325 R0001 / N0004 §2: source-change summary (AC only) ────────────────────
// R0001: "승인 후 머지 할까 말까 고민되는데 어떤 파일이 수정됐는지 볼 길이 없다." (after
// approving, deciding whether to merge is a struggle, and there's no way to see which
// files changed.)
// The numbers come from two endpoints that already exist and are already called
// elsewhere in the app — no new backend route:
//   · /projects/{pid}/git/groups/{gid}/changes  → changed-file list + per-file +/- line counts
//   · /groups/{gid}/git/finalize                → ahead/behind commit counts vs. base
// N0004 §2 dropped the per-directory file counts, so this stays a few lines tall.
const explorerStore = useExplorerStore()
const changes = ref<GroupChangeData[]>([])
const changesLoading = ref(false)
const changesError = ref(false)
const aheadCount = ref<number | null>(null)
const behindCount = ref<number | null>(null)
// 0325 TR0007 rev1 — branch names for the [Open changes] viewer title, straight off
// the same /changes response the summary already reads.
const changesBranch = ref<string | null>(null)
const changesBaseBranch = ref<string | null>(null)
// 0382 proposal 3: "도구가 남긴 흔적" (traces left by tools), excluded from the change
// list. Always shown as a single collapsed line.
const changesToolArtifacts = ref<string[]>([])
const changesDialogOpen = ref(false)

const canShowChangesSection = computed(() => isAcDoc.value && !!props.groupId)
// The group id's first segment is the project id — the same derivation
// GitFinalizePanel uses for its own group-scoped calls.
const changesProjectId = computed(() => (props.groupId ?? '').split('.')[0] || '')

const CHANGE_KINDS = [
  { key: 'added', badge: 'A', statuses: ['A', '?'] },
  { key: 'modified', badge: 'M', statuses: ['M'] },
  { key: 'deleted', badge: 'D', statuses: ['D'] },
] as const

const changeSummary = computed(() => {
  let insertions = 0
  let deletions = 0
  // null (binary / unscannable) must not silently read as 0: if NOTHING reported a
  // count, the +/- pair is a lie and the panel says so instead of showing "+0 −0".
  let lineStatsKnown = false
  for (const change of changes.value) {
    if (typeof change.insertions === 'number') { insertions += change.insertions; lineStatsKnown = true }
    if (typeof change.deletions === 'number') { deletions += change.deletions; lineStatsKnown = true }
  }
  return { total: changes.value.length, insertions, deletions, lineStatsKnown }
})

// Only the kinds actually present are listed — an empty "삭제 0" row is noise.
const changeKinds = computed(() =>
  CHANGE_KINDS
    .map((kind) => ({
      key: kind.key,
      badge: kind.badge,
      count: changes.value.filter((c) => (kind.statuses as readonly string[]).includes(c.status)).length,
    }))
    .filter((kind) => kind.count > 0),
)

const aheadBehindText = computed(() => {
  if (aheadCount.value == null || behindCount.value == null) return ''
  return t('main.git_finalize.ahead_behind', { ahead: aheadCount.value, behind: behindCount.value })
})

async function loadChangeSummary() {
  const groupId = props.groupId ?? ''
  const projectId = changesProjectId.value
  if (!canShowChangesSection.value || !projectId) return
  changesLoading.value = true
  changesError.value = false
  try {
    const changeSet = await explorerStore.fetchGroupBranchChangeSet(projectId, groupId)
    changes.value = changeSet.changes
    changesBranch.value = changeSet.branch ?? null
    changesBaseBranch.value = changeSet.base_branch ?? null
    changesToolArtifacts.value = changeSet.tool_artifacts ?? []
  } catch {
    changes.value = []
    changesBranch.value = null
    changesBaseBranch.value = null
    changesToolArtifacts.value = []
    changesError.value = true
  } finally {
    changesLoading.value = false
  }
  // ahead/behind is supplemental: the file counts are the point, so a failure here
  // just drops the branch line rather than failing the whole section.
  try {
    const { data } = await getRequest<{ state: { ahead_count: number | null; behind_count: number | null } }>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/git/finalize`,
    )
    aheadCount.value = data.state?.ahead_count ?? null
    behindCount.value = data.state?.behind_count ?? null
  } catch {
    aheadCount.value = null
    behindCount.value = null
  }
}

watch(
  () => [props.groupId, props.typeCode] as const,
  () => {
    // Switching documents must not leave the viewer open over a different group's
    // approval screen.
    changesDialogOpen.value = false
    void loadChangeSummary()
  },
  { immediate: true },
)

// ── group 0022 §3.1/§3.2 · group 0093 R0001: document-bound query/answer panel ──────
// The data + write actions live in the shared useQaAnswers composable so the
// "full view" dialog (QaHistoryDialog) can reuse the SAME qaItems ref and bound
// actions — answering there refetches once and both surfaces stay in sync.
const {
  qaItems,
  qaLoading,
  qaError,
  qaBusy,
  aiRunItemId,
  itemAnswered,
  fetchQa,
  submitQuestion,
  submitAnswer: submitAnswerCore,
  fetchAnswerMention,
  requestAiAnswer,
} = useQaAnswers(toRef(props, 'docId'))

const qaProjectId = computed(() => props.docId.split('.')[0] || '')
watch(qaProjectId, (projectId) => {
  if (projectId) void aiProviderStore.ensureLoaded(projectId)
}, { immediate: true })

const { showToast } = useToast()
const recovering = ref(false)

// 0457 T0009 item 1: the recover endpoint's 409 body carries a stable error.code; map
// each of the 7 known codes (the claim-time race collapses to the same slot_occupied
// code as the pre-check) to its own Korean sentence. An unknown/missing code (e.g. the
// group_disposed 409 or the mutation_policy 423, which are not this endpoint's own
// codes, or a network failure) falls back to the generic orphan_recover_failed message
// — never the server's English detail/message.
const ORPHAN_RECOVER_FAILURE_KEYS: Record<string, string> = {
  slot_type_not_recoverable: 'main.doc_info_panel.orphan_recover_failed_slot_type_not_recoverable',
  not_orphaned: 'main.doc_info_panel.orphan_recover_failed_not_orphaned',
  no_group_or_project: 'main.doc_info_panel.orphan_recover_failed_no_group_or_project',
  no_available_slot: 'main.doc_info_panel.orphan_recover_failed_no_available_slot',
  slot_occupied: 'main.doc_info_panel.orphan_recover_failed_slot_occupied',
  slot_type_mismatch: 'main.doc_info_panel.orphan_recover_failed_slot_type_mismatch',
  no_file_path: 'main.doc_info_panel.orphan_recover_failed_no_file_path',
}

const recoverableSlot = computed(() => {
  const slots = props.candidateSlots ?? []
  return slots.find((slot) => slot.type === props.typeCode && slot.empty) ?? null
})

async function recoverOrphan() {
  const slot = recoverableSlot.value
  if (!props.orphan || !slot || recovering.value) return
  recovering.value = true
  try {
    await postRequest('/api/v1/documents/' + encodeURIComponent(props.docId) + '/workflow/recover', { item_seq: slot.item_seq })
    showToast(t('main.doc_info_panel.orphan_recovered'), 'success')
    emit('orphan-recovered')
  } catch (e: any) {
    const code = e?.response?.data?.error?.code as string | undefined
    const key = (code && ORPHAN_RECOVER_FAILURE_KEYS[code]) || 'main.doc_info_panel.orphan_recover_failed'
    showToast(t(key), 'danger')
  } finally {
    recovering.value = false
  }
}
const { recordMentionCopy } = useMentionCopy()

// [Copy mention] for one query item (0248 B0001 rework). The mention is fetched INSIDE the
// deferred producer, not awaited before it: the token round-trip would otherwise outlive the
// click's transient activation and the clipboard write would silently reject (group 0133
// NR0003). ClipboardAbort keeps a failed fetch from also reporting a copy failure — the
// composable has already put the reason in qaError.
async function copyAnswerMention(itemId: number): Promise<boolean> {
  const ok = await copyToClipboardDeferred(async () => {
    const mention = await fetchAnswerMention(itemId)
    if (!mention) throw new ClipboardAbort()
    return mention
  })
  if (ok) {
    showToast(t('main.doc_info_panel.qa_answer_mention_copied'), 'success')
    // 'qa_answer' is the mention kind this exact hand-off was registered as (useMentionCopy);
    // the document-bound panel is simply the second producer of it.
    void recordMentionCopy(props.docId, 'qa_answer')
  } else if (qaError.value) {
    showToast(qaError.value, 'danger')
  } else {
    showToast(t('main.doc_info_panel.qa_answer_mention_copy_failed'), 'danger')
  }
  return ok
}

const newQOpen = ref(false)
const newQTitle = ref('')
const newQBody = ref('')
// group 0243 R0001: optional reference options on a human-written query. Leaving every row
// blank yields exactly the pre-extension query — blank rows are dropped on submit.
const newQOptions = ref<string[]>([])
const QA_MAX_OPTIONS = 10  // mirrors q_service.MAX_OPTIONS (L0008 §1)

function addQOption() {
  if (newQOptions.value.length < QA_MAX_OPTIONS) newQOptions.value.push('')
}
function removeQOption(idx: number) {
  newQOptions.value.splice(idx, 1)
}

// group 0126 / option C: unanswered count shown on the Q&A headline badge (matches the
// prototype's "미응답 N" (N unanswered) pill — and the same count the header counter
// would show).
const qaUnansweredCount = computed(() => qaItems.value.filter((it) => !itemAnswered(it)).length)

// ── 0311 T0004 rev1: the two capped feeds ───────────────────────────────────────
// Both sections cap the panel at FEED_VISIBLE cards (NR0003 §5-3's actual
// recommendation). rev5 rejection §3·§4: the cap is now hard — the overflow is not
// announced with a "이전 항목 N건 더" (N more earlier items) line any more, it is simply
// not drawn, and each section's header-row [전체보기] is the one door to the rest. Every
// preview text is clamped with an
// ellipsis so a long string cannot stretch the panel either (§4).
const FEED_VISIBLE = 3

// Query: newest-registered first. question_items has no created_at column (server
// schema — server/sql/queries/queries.json "get_question_items" is seq ASC only), so
// seq is the only ordering on record. That is fine here precisely because qa is no
// longer interleaved with anything that has a real clock.
const qaFeed = computed<QaItem[]>(() => [...qaItems.value].reverse())
const qaFeedVisible = computed(() => qaFeed.value.slice(0, FEED_VISIBLE))

// AI review·rejection: sorted by REAL time — rejections carry rejected_at and reviews carry
// reviewed_at ?? created_at, so nothing has to be invented to interleave them (this is
// exactly what the qa+reject merge could not do).
// An entry with no parseable timestamp sorts newest: the only way to get one is the
// legacy single-reason path (rejectReason with no rejectionHistory) or a review the
// server has not stamped yet, and in both cases it IS the state just handed to us.
interface ReviewRejectEntry {
  kind: 'reject' | 'review'
  key: string
  when: number
  reject?: RejectionHistoryItem
  review?: AiReview
}
function whenMs(iso: string | null | undefined): number {
  if (!iso) return Number.POSITIVE_INFINITY
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms
}
const reviewRejectFeed = computed<ReviewRejectEntry[]>(() => {
  const out: ReviewRejectEntry[] = []
  if (canShowRejectSection.value) {
    if (rejectionHistoryList.value.length > 0) {
      rejectionHistoryList.value.forEach((r, i) => out.push({
        kind: 'reject',
        key: `reject:${r.rejection_id ?? r.rejected_at ?? i}`,
        when: whenMs(r.rejected_at),
        reject: r,
      }))
    } else if (props.rejectReason) {
      // Pre-history documents deliver only the current reason, with no history array.
      out.push({
        kind: 'reject',
        key: 'reject:legacy',
        when: whenMs(null),
        reject: { reason: props.rejectReason, rejected_at: '', rejected_by: null },
      })
    }
  }
  if (canShowReviewSection.value) {
    // Same fallback shape as the rejection above: prefer the full history, fall back to
    // the single latest review when only that prop was handed in. The panel card is a
    // review-comment fold, so reviews without a comment remain available in [전체보기] but do
    // not create an empty feed row here.
    const reviews = ((props.aiReviewHistory?.length ?? 0) > 0
      ? [...(props.aiReviewHistory ?? [])]
      : (props.aiReview ? [props.aiReview] : []))
      .filter((r) => !!r.comment)
    reviews.forEach((r, i) => out.push({
      kind: 'review',
      key: `review:${r.id ?? r.reviewed_at ?? r.created_at ?? i}`,
      when: whenMs(reviewWhen(r)),
      review: r,
    }))
  }
  // Not `b.when - a.when`: two unstamped entries would both be Infinity and yield NaN.
  return out.sort((a, b) => (a.when === b.when ? 0 : a.when > b.when ? -1 : 1))
})
const reviewRejectFeedVisible = computed(() => reviewRejectFeed.value.slice(0, FEED_VISIBLE))
// The header-row [전체보기] must stay reachable even when the panel feed itself is empty, and
// even when the feed has more entries than FEED_VISIBLE shows.
const reviewRejectHasHistory = computed(() =>
  reviewRejectFeed.value.length > 0
  || (props.aiReviewHistory?.length ?? 0) > 0
  || !!props.aiReview
  || rejectionHistoryList.value.length > 0
  || !!props.rejectReason)
// 0422 TR0003 rev2 rejection: the badge inside each review-comment toggle header row
// follows the exact same mapping as the [전체보기] dialog's verdictClass/verdictLabel.
function reviewVerdictClass(r: AiReview): string {
  return r.verdict === 'pass' ? 'pass' : 'warn'
}
function reviewVerdictLabel(r: AiReview): string {
  if (r.verdict === 'pass') return t('main.doc_info_panel.ai_verdict_pass')
  if (r.verdict === 'hold') return t('main.doc_info_panel.ai_verdict_hold')
  return t('main.doc_info_panel.ai_verdict_issues', { n: r.finding_count ?? 0 })
}

// group 0126 / option C + T0013 + 0311 T0004: qa full-history modal. TR0005 rev6
// rejection §3 ("질의는 빼라" — leave query out) split the once-merged dialog back in
// two — this ref only opens QaHistoryDialog now. The query headline's [전체보기] opens
// it unfocused; each qa card's [답변] opens it focused on that query with the answer
// form started.
const qaHistoryVisible = ref(false)
const qaFocusId = ref<number | null>(null)
const qaStartAnswer = ref(false)
function openQaFull(focusId: number | null = null, startAnswer = false) {
  qaFocusId.value = focusId
  qaStartAnswer.value = startAnswer
  qaHistoryVisible.value = true
}

// The [전체보기] of the AI review·rejection section — opens the review/rejection-only QaReviewHistoryDialog.
const reviewRejectHistoryVisible = ref(false)
function openReviewRejectFull() {
  reviewRejectHistoryVisible.value = true
}

function toggleNewQ() {
  newQOpen.value = !newQOpen.value
  if (!newQOpen.value) { newQTitle.value = ''; newQBody.value = ''; newQOptions.value = [] }
}

async function submitNewQ() {
  if (await submitQuestion(newQTitle.value, newQBody.value, newQOptions.value)) {
    newQTitle.value = ''; newQBody.value = ''; newQOptions.value = []; newQOpen.value = false
  }
}

watch(
  () => props.docId,
  () => {
    newQOpen.value = false
    qaHistoryVisible.value = false
    reviewRejectHistoryVisible.value = false
    // Feed keys are per-document; carrying folds across would open an unrelated card.
    foldOpen.comment = {}
    foldOpen.response = {}
    foldOpen.reason = {}
    fetchQa()
  },
  { immediate: true },
)

// Query registration, answer registration, and AI-run completion all use this refresh-only
// signal. The semantic q_registered/q_answered events remain reserved for run-card state.
function _onQaRefresh(e: Event) {
  const detail = (e as CustomEvent).detail as { doc_id?: string } | undefined
  if (detail?.doc_id && detail.doc_id === props.docId) fetchQa()
}
onMounted(() => window.addEventListener('fg:qa_refresh', _onQaRefresh))
onBeforeUnmount(() => window.removeEventListener('fg:qa_refresh', _onQaRefresh))
</script>

<style scoped>
.dip-orphan-warning {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 8px;
  align-items: start;
  margin-top: 10px;
  padding: 10px;
  border: 1px solid #f59e0b;
  border-radius: 8px;
  background: #fffbeb;
  color: #92400e;
}
.dip-orphan-warning p { margin: 0; font-size: .72rem; line-height: 1.45; }
.dip-orphan-warning .btn { grid-column: 1 / -1; justify-self: stretch; }
.dip-badge-clickable {
  cursor: pointer;
}
.dip-badge-clickable:hover {
  box-shadow: 0 0 0 2px rgba(37, 99, 235, .25);
}
.dip-step-clickable {
  cursor: pointer;
}
.dip-step-clickable:hover {
  box-shadow: 0 0 0 2px rgba(37, 99, 235, .25);
}
.dip-step-disabled {
  cursor: default;
  opacity: .72;
}
/* R0001 (group 0126 / option C): section-level accordion. The section title becomes a
   caret toggle — a button reset back to the .dip-section-title look (the typography
   is restated here because a bare `font: inherit` would lose the .63rem/upper-case
   title style under scoped-style specificity). The body wrapper hides and the caret
   rotates -90° when the section is collapsed, mirroring the left file-tree caret. The
   whole-panel close chevron stays a separate control. */
.dip-sec-toggle {
  appearance: none;
  background: none;
  border: none;
  width: 100%;
  padding: 0;
  font-family: inherit;
  font-size: .63rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--text-m);
  text-align: left;
  cursor: pointer;
}
.dip-sec-toggle:hover { color: var(--text-s); }
.dip-acc-caret {
  font-size: .6rem;
  color: #9aa3af;
  transition: transform .18s ease;
}
.dip-section.collapsed .dip-acc-caret { transform: rotate(-90deg); }
.dip-section.collapsed .dip-sec-body { display: none; }

/* group 0022 §3.1: Q&A panel (reuses the rejection-box idiom) */
.dip-section-head { display: flex; align-items: center; justify-content: space-between; }
.dip-qa-add { color: var(--primary); }
.dip-qa-hint { font-size: .72rem; color: #6b7280; padding: 4px 0; }
.dip-qa-error { font-size: .72rem; color: var(--danger); padding: 4px 0; }
.dip-qa-form {
  display: flex; flex-direction: column; gap: 6px;
  margin: 6px 0; padding: 8px; background: #f8fafc;
  border: 1px solid var(--border); border-radius: 6px;
}
.dip-qa-input, .dip-qa-textarea {
  width: 100%; box-sizing: border-box; font-size: .78rem;
  padding: 5px 7px; border: 1px solid var(--border); border-radius: 4px;
  font-family: inherit;
}
.dip-qa-textarea { resize: vertical; }
.dip-qa-form-actions { display: flex; justify-content: flex-end; gap: 6px; }

/* group 0243 R0001: option editor in the new-query form + option preview on a card. The
   preview is inert text — no recommendation accent, nothing preselected (0022 rule). */
.dip-qa-opt-edit { display: flex; flex-direction: column; gap: 4px; }
.dip-qa-opt-row { display: flex; align-items: center; gap: 4px; }
.dip-qa-opt-row .dip-qa-input { flex: 1; }
.dip-qa-opt-del {
  border: none; background: none; cursor: pointer; color: #9ca3af;
  padding: 2px 4px; line-height: 1; font-size: .7rem;
}
.dip-qa-opt-del:hover { color: var(--danger); }
.dip-qa-opt-add {
  align-self: flex-start; border: none; background: none; cursor: pointer;
  color: var(--primary); font-size: .7rem; padding: 2px 0;
  display: inline-flex; align-items: center; gap: 3px;
}
.dip-qa-opt-list { list-style: none; margin: 4px 0 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
.dip-qa-opt {
  font-size: .7rem; color: #6b7280;
  padding: 3px 7px; border: 1px solid var(--border); border-radius: 4px; background: #f8fafc;
  /* rev5 rejection §4: a long option label used to wrap over several lines inside a card that
     is only a preview — clamp it to one line with an ellipsis. */
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* group 0126: prototype card layout for the Q&A section. The headline carries a
   caret-title toggle plus an unanswered-count pill and a compact [+ add] button; the
   body lists amber cards (title + 2-line preview + Answer only). */
.dip-qa-headline {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; margin-bottom: 10px;
}
.dip-qa-headline .dip-sec-toggle { width: auto; flex: 1; min-width: 0; }
.dip-qa-head-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 6px; }
.dip-qa-count {
  padding: 2px 7px; border: 1px solid #fde68a; border-radius: 999px;
  color: #92400e; background: #fef3c7; font-size: .66rem; font-weight: 800;
  white-space: nowrap;
}
/* group 0126 T0013: the headline keeps BOTH [전체보기] and [+] (the instruction asked
   to align their styling, not remove either). They share one amber button family so
   they read as a matched pair next to the unanswered-count pill — same 24px height,
   border, radius and palette; [전체보기] carries an icon + label, [+] is the square
   icon-only variant. */
.dip-qa-act {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  height: 24px; padding: 0 9px;
  border: 1px solid #fcd34d; border-radius: var(--r, 6px);
  color: #92400e; background: #fff7ed;
  font-family: inherit; font-size: .66rem; font-weight: 700;
  white-space: nowrap; cursor: pointer;
}
.dip-qa-act:hover { background: #fef3c7; }
.dip-qa-act--icon { width: 24px; padding: 0; font-size: .74rem; }
.dip-qa-card {
  padding: 10px 11px; border: 1px solid #fde68a; border-left: 3px solid #f59e0b;
  border-radius: var(--r, 6px); background: #fffbeb;
}
.dip-qa-card + .dip-qa-card { margin-top: 8px; }
.dip-qa-card-title {
  display: block; overflow: hidden; margin-bottom: 4px;
  color: #78350f; font-size: .76rem; text-overflow: ellipsis; white-space: nowrap;
}
.dip-qa-card-body {
  display: -webkit-box; overflow: hidden; margin: 0;
  color: #6b4f1d; font-size: .72rem; line-height: 1.45;
  -webkit-box-orient: vertical; -webkit-line-clamp: 2;
}
.dip-qa-card-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 8px; }
.dip-qa-card.answered-card { opacity: .6; border-left-color: #86efac; }
.mini-action {
  padding: 3px 7px; border: 1px solid #fcd34d; border-radius: var(--r, 6px);
  color: #92400e; background: #fff7ed; font-size: .66rem; font-weight: 700; cursor: pointer;
}
.mini-action:hover { filter: brightness(.97); }
.mini-action.primary { color: #fff; border-color: #d97706; background: #d97706; }
.dip-qa-item {
  border: 1px solid var(--border); border-radius: 6px;
  margin-bottom: 6px; overflow: hidden; background: #fff;
}
.dip-qa-item-head {
  display: flex; align-items: center; gap: 7px; width: 100%;
  padding: 7px 9px; background: none; border: none; cursor: pointer; text-align: left;
}
.dip-qa-item-head:hover { background: #f8fafc; }
.dip-qa-item-title { flex: 1; font-size: .76rem; color: #1e293b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dip-qa-chevron { font-size: .55rem; color: #9aa3af; transition: transform .18s ease; }
.dip-qa-item.open .dip-qa-chevron { transform: rotate(180deg); }
.dip-qa-item-body { padding: 4px 10px 10px; }
.dip-qa-meta { font-size: .62rem; color: #59606a; margin-bottom: 5px; }
/* R0001 (rev1): question / answer bodies are shown directly when the Q item is
   expanded (no nested fold toggle), so reading a query needs a single disclosure.
   A labelled header row sits over a height-capped, 14px-scrollbar body — long text
   scrolls inside the box instead of stretching the side panel. Only the accent/scroll
   tone differs: neutral grey for the question, green for the answer. */
.dip-qa-fold {
  margin-top: 4px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-left: 3px solid #94a3b8;
  border-radius: 6px;
  overflow: hidden;
}
.dip-qa-fold--answer { border-left-color: #22c55e; }
.dip-qa-fold-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
}
.dip-qa-fold-label {
  font-size: .64rem;
  font-weight: 700;
  color: #475569;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.dip-qa-fold--answer .dip-qa-fold-label { color: #15803d; }
.dip-qa-answer-icon { color: #15803d; }
.dip-qa-fold-body {
  display: block;
  max-height: 8rem;
  overflow-y: auto;
  overflow-wrap: anywhere;
  padding: 0 9px 8px;
  font-size: .78rem;
  color: #1e293b;
  white-space: pre-wrap;
  line-height: 1.65;
  scrollbar-width: auto;
  scrollbar-color: #94a3b8 #e2e8f0;
}
.dip-qa-fold--answer .dip-qa-fold-body { scrollbar-color: #22c55e #dcfce7; }
@supports selector(::-webkit-scrollbar) {
  .dip-qa-fold-body::-webkit-scrollbar { width: 14px; }
  .dip-qa-fold-body::-webkit-scrollbar-track { border-radius: 999px; background: #e2e8f0; }
  .dip-qa-fold-body::-webkit-scrollbar-thumb {
    border: 3px solid #e2e8f0;
    border-radius: 999px;
    background: #94a3b8;
  }
  .dip-qa-fold-body::-webkit-scrollbar-thumb:hover { background: #64748b; }
  .dip-qa-fold--answer .dip-qa-fold-body::-webkit-scrollbar-track { background: #dcfce7; }
  .dip-qa-fold--answer .dip-qa-fold-body::-webkit-scrollbar-thumb { border-color: #dcfce7; background: #22c55e; }
  .dip-qa-fold--answer .dip-qa-fold-body::-webkit-scrollbar-thumb:hover { background: #16a34a; }
}
.dip-qa-actions { display: flex; gap: 6px; margin-top: 8px; }
.dip-wf-step.current.dip-wf-completed {
  background: #fff;
  border-color: #d1d5db;
  color: #111827;
  font-weight: 400;
}
.dip-reject-history-label {
  font-size: .68rem;
  color: #6b7280;
  margin-left: 6px;
  font-weight: 400;
}
/* AI review feedback section.
   rev5 rejection §1: the entry head (.dip-ai-entry-head / .dip-ai-meta) and the findings list
   (.dip-ai-findings / .dip-ai-finding*) stay out of the panel — those stay in the
   [전체보기] dialog (.rhd-findings).
   0422 TR0003 rev2 rejection ("dip-ai-comment-toggle 여기" — [it belongs] here in
   dip-ai-comment-toggle): each verdict badge lives inside its review-comment toggle
   header — same colours/shape as the dialog's .rhd-verdict. */
.dip-ai-verdict {
  display: inline-block;
  flex: 0 0 auto;
  font-size: .62rem;
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 999px;
  margin-left: auto;
}
.dip-ai-verdict.pass { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
.dip-ai-verdict.warn { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
/* R0001 (rev1): the comment box now shares the rejection-reason / AI-response control
   idiom exactly — a clickable header row (label + chevron, no "expand/collapse" text button)
   sitting above a body that is clamped to two lines and expands to a height-capped,
   14px-scrollbar body. Only the accent colour stays amber (vs red/blue) to keep this
   box's verdict tone, so the fold behaves identically across all three sections. */
.dip-ai-comment {
  margin-top: 6px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-left: 3px solid #f59e0b;
  border-radius: 6px;
  overflow: hidden;
}
.dip-ai-comment-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
}
.dip-ai-comment-toggle:hover { background: #fffdf5; }
.dip-ai-comment-label {
  font-size: .64rem;
  font-weight: 700;
  color: #b45309;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.dip-ai-comment-chevron {
  margin-left: 0;
  color: #d08a2c;
  font-size: .6rem;
  transition: transform .18s ease;
}
.dip-ai-comment.open .dip-ai-comment-chevron { transform: rotate(180deg); }
.dip-ai-comment-body {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  max-height: 3.5em;
  overflow: hidden;
  overflow-wrap: anywhere;
  padding: 0 9px 8px;
  font-size: .78rem;
  color: #1e293b;
  white-space: pre-wrap;
  line-height: 1.65;
}
/* rev5 rejection §4 ("문자열이 너무 길면 ... 을 넣어라 어차피 전체보기에서 볼테니까" — if
   the string is too long, add an ellipsis, since it can be seen in full view anyway):
   opening no longer turns the body into a scroll box. It stays a clamped box — 2 lines
   closed, 6 lines open — so a long comment always ends in an ellipsis and the panel
   never grows past ~6 lines. The uncut text is in [전체보기]. */
.dip-ai-comment.open .dip-ai-comment-body {
  max-height: 10em;
  -webkit-line-clamp: 6;
}
.dip-ai-history-link {
  margin-top: 6px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: .72rem;
  font-weight: 600;
  color: var(--primary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.dip-ai-history-link:hover { text-decoration: underline; }

/* TR work-scope verification card (0299 D0004 §6). The reported/detected lists are
   laid out in the same shape for visual comparison, and only mismatched items are
   colour-coded. */
.dip-trs-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dip-trs-verdict {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: .72rem;
  font-weight: 700;
}
.dip-trs-pass { background: var(--success-bg, #dcfce7); color: var(--success, #15803d); }
.dip-trs-warn { background: var(--warning-bg, #fef3c7); color: var(--warning, #b45309); }
.dip-trs-reject { background: var(--danger-bg, #fee2e2); color: var(--danger, #b91c1c); }
.dip-trs-skipped { background: var(--muted-bg, #f1f5f9); color: var(--muted, #64748b); }
.dip-trs-stage { font-size: .7rem; color: var(--muted, #64748b); }
.dip-trs-assign { margin: 6px 0 0; font-size: .72rem; color: var(--muted, #64748b); }
/* Unverified notice (0390 TR0005 rev2). One line right under the verdict badge, read before the list. */
.dip-trs-unevaluated { margin: 6px 0 0; font-size: .72rem; line-height: 1.5; color: var(--muted, #64748b); }
.dip-trs-codes { margin: 6px 0 0; padding-left: 16px; font-size: .72rem; line-height: 1.5; }
.dip-trs-list-label { margin: 8px 0 2px; font-size: .72rem; font-weight: 600; }
.dip-trs-list {
  margin: 0;
  padding-left: 16px;
  font-size: .7rem;
  line-height: 1.5;
  max-height: 180px;   /* self-scrolls so a long list does not stretch the whole panel */
  overflow-y: auto;
  word-break: break-all;
}
.dip-trs-mismatch { color: var(--danger, #b91c1c); }
.dip-trs-more { color: var(--muted, #64748b); list-style: none; margin-left: -16px; }

/* Source-change summary (0325 R0001 / N0004 §2). Appears only on the final-approval
   screen, showing the bare minimum needed for that screen's "머지할까 말까" (merge or
   not) decision — file count · added/removed line counts · per-kind counts · ahead/
   behind vs. base. Per-directory distribution is left out per N0004 §2. */
.dip-chg-headline {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.dip-chg-files { font-size: .82rem; }
.dip-chg-lines { display: inline-flex; gap: 6px; font-size: .74rem; font-variant-numeric: tabular-nums; }
.dip-chg-add { color: var(--success, #15803d); font-weight: 600; }
.dip-chg-del { color: var(--danger, #b91c1c); font-weight: 600; }
.dip-chg-kinds { margin: 7px 0 0; padding: 0; list-style: none; }
.dip-chg-kind {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: .72rem;
  line-height: 1.9;
}
.dip-chg-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 4px;
  font-size: .62rem;
  font-weight: 700;
}
.dip-chg-added { background: var(--success-bg, #dcfce7); color: var(--success, #15803d); }
.dip-chg-modified { background: var(--warning-bg, #fef3c7); color: var(--warning, #b45309); }
.dip-chg-deleted { background: var(--danger-bg, #fee2e2); color: var(--danger, #b91c1c); }
.dip-chg-kind-label { flex: 1; color: var(--muted, #64748b); }
.dip-chg-kind-count { font-variant-numeric: tabular-nums; font-weight: 600; }
.dip-chg-note { margin: 6px 0 0; font-size: .68rem; color: var(--muted, #64748b); }
.dip-chg-branch {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 8px 0 0;
  font-size: .7rem;
  color: var(--muted, #64748b);
}
/* [Open changes] — placed full-width at the bottom of the section since it is an entry point, not the summary's conclusion. */
.dip-chg-open {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  margin: 9px 0 0;
  padding: 7px 9px;
  font-size: .72rem;
  font-weight: 600;
  color: var(--primary, #1d4ed8);
  background: var(--primary-bg, #eff6ff);
  border: 1px solid var(--primary-border, #bfdbfe);
  border-radius: 7px;
  cursor: pointer;
}
.dip-chg-open:hover { background: var(--primary-bg-hover, #dbeafe); }

/* P0005/T0006: the AI's response to a rejection — threaded as a reply directly
   under the rejection quote (a sibling, not nested inside the quote box).
   Collapsible like the quote: folded by default, the header toggles the body. */
.dip-ai-response {
  margin: 7px 0 0 12px; /* indent so it reads as a reply to the quote above it */
  background: #f0f7ff;
  border: 1px solid #cfe2ff;
  border-left: 3px solid var(--primary, #2563eb);
  border-radius: 6px;
  overflow: hidden;
}
.dip-ai-response-head {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
}
.dip-ai-response-head:hover { background: #e7f1ff; }
.dip-ai-response-label {
  font-size: .64rem;
  font-weight: 700;
  color: #1d4ed8;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.dip-ai-response-thread { color: #93b4e6; }
.dip-ai-response-date {
  margin-left: auto;
  color: #6b86ad;
  font-size: .62rem;
  white-space: nowrap;
}
.dip-ai-response-chevron {
  color: #6b86ad;
  font-size: .6rem;
  transition: transform .18s ease;
}
.dip-ai-response.open .dip-ai-response-chevron { transform: rotate(180deg); }
/* Collapsed still shows a few lines (clamped) exactly like the rejection reason —
   NOT crushed to zero height. Opening lifts the clamp and height-caps the body
   with a scrollbar, so a long response scrolls instead of stretching the panel.
   This is the rejection quote's own idiom ("like the rejection — show only a few lines, scroll when expanded"). */
.dip-ai-response-body {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  max-height: 3.5em;
  overflow: hidden;
  overflow-wrap: anywhere;
  padding: 6px 9px 8px;
  font-size: .78rem;
  color: #1e293b;
  white-space: pre-wrap;
  line-height: 1.65;
}
/* rev5 rejection §4: same as the comment box — open means 6 clamped lines with an
   ellipsis, not a scroll box. */
.dip-ai-response.open .dip-ai-response-body {
  max-height: 10em;
  -webkit-line-clamp: 6;
}

/* Mockup xc32frrg screen 1 — provider assignment (by step) */
.dip-wp-assignments { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.dip-wp-assignments li { display: flex; align-items: center; gap: 6px; font-size: .74rem; }
.dip-wp-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--primary, #2563eb); flex-shrink: 0; }
.dip-wp-prov { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dip-wp-assignments strong { margin-left: auto; font-variant-numeric: tabular-nums; }

/* 0311 T0004 rev1 §2 — spacing between items in the merged AI review·rejection feed.
   The only style added for this merge: the cards themselves reuse .dip-reject-quote /
   .dip-ai-entry above as-is, and this only supplies the gap when several cards stack
   vertically (no wrapper box). */
.dip-rr-entry + .dip-rr-entry { margin-top: 10px; }
</style>
