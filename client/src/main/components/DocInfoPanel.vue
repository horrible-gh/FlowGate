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
            <p>{{ t('main.doc_info_panel.orphan_desc') }}</p>
            <button type="button" class="btn btn-sm btn-primary" :disabled="recovering" @click="recoverOrphan">
              {{ t(recovering ? 'main.doc_info_panel.orphan_recovering' : 'main.doc_info_panel.orphan_recover') }}
            </button>
          </div>
        </div>
      </div>

      <!-- Mockup xc32frrg screen 1 — 문서 정보의 [프로바이더 배정 (단계 기준)] 칸.
           0399 M0020 반려 — 보여줄 배정이 하나도 없으면 이 칸을 아예 그리지 않는다.
           읽는 동안의 "불러오는 중" 도, 읽기에 실패했다는 문구도 두지 않는다. 빈 칸이
           사이드바에 돋아나 "안 보이던 사이드바" 로 읽히던 자리다. -->
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
           최종 승인(AC) 시점에만, 질의 응답·AI 검수 의견·반려 사유가 비운 자리에
           들어간다. "머지할까 말까" 를 판단하는 그 화면에서 이번 그룹이 무엇을
           얼마나 바꿨는지 볼 수 있는 유일한 자리다.
           N0004 §2 에 따라 디렉터리별 파일 수 목록은 넣지 않는다 — 사이드바만
           길어지고 판단에는 보태는 게 없다. -->
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
            <!-- 0325 TR0007 rev1 (반려 반영): 시안의 [변경사항 열기]. 요약은 "몇 파일 ·
                 몇 줄"까지만 답하고, R0001 이 물은 "소스가 잘 됐는지" 는 실제 diff 를
                 읽어야 답이 난다. 이 버튼이 그 진입점이고, 여는 화면은 승인 화면을
                 대체하지 않는 오버레이라 닫으면 이 자리로 그대로 돌아온다. -->
            <button type="button" class="dip-chg-open" @click="changesDialogOpen = true">
              <AppIcon name="arrow-square-out" />
              {{ t('main.doc_info_panel.changes_open') }}
            </button>
          </template>
        </div>
      </div>

      <!-- Section 2: 질의 (0311 T0004 rev1 §1 — 반려와 다시 분리한 독립 섹션).
           rev0 이 지시했던 qa+reject 병합은 "합칠 대상이 잘못됐다"는 반려로 되돌렸다.
           rev3 반려("현재 적용되어있는 스타일을 전혀 사용하지 않는다"): 이 섹션의 마크업과
           클래스는 지금 화면에 실제로 적용되어 있는 것 그대로다 — .dip-qa-card(앰버 카드)
           · .dip-qa-card-title/-body · .dip-qa-opt-list(선택지 미리보기) · mini-action
           primary [답변] · answered-card. 새로 만든 것은 없다.
           NR0003 §5-3 이 권한 "최신 N건 + 나머지는 전체보기로" 상한선만 얹었고, 그 링크도
           이 파일이 이미 쓰던 .dip-ai-history-link 관용구를 그대로 쓴다.
           표시 조건은 원래대로 canShowQaSection — AC 문서에서만 감춘다. -->
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
          <!-- rev5 반려 §3: "이전 항목 N건 더 — 전체보기에서 확인" 줄은 없앴다. 넘친
               항목으로 나가는 문은 머리줄 오른쪽의 [전체보기] 하나뿐이다. -->
        </template>
        </div>
      </div>

      <!-- Section 2.5: AI 검수·반려 (0311 T0004 rev1 §2 — 이번에 실제로 합칠 한 쌍).
           두 섹션은 표시 조건이 글자 하나 다르지 않게 같았고(canShowRejectSection ≡
           canShowReviewSection), 원래부터 같은 "최신 1건 + 이력 링크" 패턴을 썼으며, 둘 다
           진짜 시각 컬럼(rejected_at · reviewed_at/created_at)을 갖고 있어 순서를 지어낼
           필요가 없다 — 그래서 하나의 시간순 피드로 합친다.

           rev3 반려("현재 적용되어있는 스타일을 전혀 사용하지 않는다 / 반려·대응이 그렇게
           되어있던가 / 작업검수가 이중박스로 되어있던가"): 합친 뒤에도 카드 한 장 한 장은
           지금 화면에 적용되어 있는 그 마크업 그대로다. 반려 = .dip-reject-quote(작성자·
           날짜 머리줄을 누르면 접히는 인용 상자) + 그 아래 형제로 붙는 .dip-ai-response
           스레드, AI 검수 = .dip-ai-entry. 카드를 감싸는 새 상자(rev0 의 .dip-mix-card)는
           없앴다 — 그것이 AI 검수를 이중박스로 만든 장본인이었다. 합치기 위해 새로 넣은
           스타일은 항목 사이 간격 규칙(.dip-rr-entry) 하나뿐이다.

           rev4 반려: ① AI 검수 항목의 머리줄(.dip-ai-entry-head/.dip-ai-meta)·판정 배지
           (.dip-ai-verdict)·지적 목록(.dip-ai-findings)은 당시 패널에서 뺐다. 이후 0422
           TR0003 rev2에서 판정 배지만 .dip-ai-comment-toggle 안에 복원했다. ② 반려 카드의
           이름 자리는 「반려」, 그 .dip-ai-comment 는
           「검수 의견」(i18n 값만 바꿨다). ③ 본문 아래 "이전 항목 N건 더" 줄 대신 제목 오른쪽
           [전체보기]. ④ 최대 3건, 긴 문자열은 접었든 펼쳤든 말줄임. -->
      <div v-if="canShowReviewRejectSection" class="dip-section" :class="{ collapsed: sectionCollapsed.ai_review }">
        <!-- rev5 반려 §3: [전체보기] 를 제목 오른쪽에 둔다. 머리줄 자체는 바로 위 질의
             섹션이 이미 쓰고 있는 .dip-qa-headline / .dip-qa-head-actions / .dip-qa-act
             그대로이고, 이 섹션의 단추에는 자기 훅 클래스(.dip-rr-fullview)만 더 붙였다.
             rev6 반려 §1·§3: 이 [전체보기] 는 질의 섹션과 다른 다이얼로그(검수·반려
             전용 QaReviewHistoryDialog)를 연다 — openReviewRejectFull 참고. -->
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
            <!-- 0419 T0006 (NR0003 후속 T 권고 2 / TR0005 rev1 반려): 반려 메시지
                 재수정 진입점은 액션바가 아니라 이미 반려 사유를 보여주는 이
                 사이드바 머리줄에 둔다. rejected 상태에서만 뜬다 — 재작업
                 제출 이후(revised)는 과거 반려 사유를 소급 수정하지 않는다. -->
            <button v-if="canEditRejection" class="dip-qa-act dip-rr-edit" type="button" @click="emit('edit-rejection')" :title="t('common.edit')">
              <AppIcon name="pencil-simple" />
              {{ t('common.edit') }}
            </button>
          </div>
        </div>
        <div class="dip-sec-body">
        <template v-if="reviewRejectFeed.length > 0">
          <div v-for="entry in reviewRejectFeedVisible" :key="entry.key" class="dip-rr-entry">
            <!-- 반려 — 기존 반려 사유 섹션의 마크업 그대로 -->
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

            <!-- AI 검수 — 기존 AI 검수 섹션의 .dip-ai-entry 그대로 (감싸는 상자 없음).
                 rev5 반려 §1: 머리줄(시각 · AI)·지적 목록은 패널에 그리지 않는다 — 시각·
                 지적 상세는 [전체보기] 창에서 그대로 볼 수 있다.
                 0422 TR0003 rev2 반려("dip-ai-comment-toggle 여기"): R0001 이 요청한 배지는
                 섹션 헤드라인이나 카드 바깥이 아니라 각 검수의견 토글 머리줄 안에 둔다.
                 [전체보기] 다이얼로그(QaReviewHistoryDialog)의 .rhd-verdict 와 같은 값
                 (verdict/finding_count)을 각 .dip-ai-comment-toggle 안에서 그린다. -->
            <div v-else class="dip-ai-entry">
              <!-- R0001 (rev1): the comment fold uses the SAME control idiom as the
                   rejection reason and the AI response — a clickable header row carrying a
                   label + chevron over a clamped body. rev5 반려 §4: opening widens the
                   clamp from 2 lines to 6 instead of turning it into a scroll box, so a
                   long 검수 의견 always ends in an ellipsis. Accent colour stays amber. -->
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

          <!-- rev5 반려 §3: 본문 아래의 "이전 항목 N건 더 / 전체 보기" 링크는 없앴다.
               전체 이력으로 나가는 문은 머리줄 오른쪽의 [전체보기] 다. -->
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

      <!-- Section 2.5: TR 작업범위 검증 (0299 D0004 §6).
           결과가 통과이고 사유가 없으면 접어 둔다 — 정상 제출에서 이 카드가 펼쳐져
           있으면 매 문서마다 읽을 것 없는 목록이 자리를 차지한다. 경고·거부일 때만
           펼쳐서 보여준다. -->
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
          <!-- 제출 시점에 검증이 돌지 않아 대조 결과가 없는 문서. 영역을 감추는 대신
               왜 판정이 없는지 밝히고 본문에 신고된 목록만 보여준다. rev3: 본문에
               변경 파일 절이 아예 없는 문서(검증 대상이 되기 전에 제출된 TS 는 그
               절을 쓰라는 안내를 받지 못했으므로 없는 것이 정상)도 감추지 않고,
               "절이 없다"는 사실을 그대로 밝힌다. -->
          <p v-if="trScopeUnevaluated" class="dip-trs-unevaluated">
            {{ t(`main.doc_info_panel.${trScopeUnevaluatedKey}`) }}
          </p>

          <ul v-if="trScope.codes?.length" class="dip-trs-codes">
            <li v-for="code in trScope.codes" :key="code">
              <strong>{{ code }}</strong> — {{ t(`main.doc_info_panel.tr_scope_code_${code.replace('-', '_').toLowerCase()}`) }}
            </li>
          </ul>

          <!-- 어긋난 항목이 먼저다. 신고/감지 전체 목록은 그다음이고, 눈으로 대조할
               수 있게 같은 모양으로 나란히 둔다. -->
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

    <!-- 0311 T0004 / TR0005 rev6 반려 §3 ("질의는 빼라"): 질의 전체보기는 다시 자기
         전용 다이얼로그(QaHistoryDialog)를 쓴다. 질의 헤드라인의 [전체보기]와 각 카드의
         [답변]이 이 다이얼로그를 doc-id 컨텍스트로 연다. -->
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

    <!-- 검수·반려 전용 "전체보기" — AI검수·반려 섹션의 [전체보기]만 연다. -->
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
  // TR 작업범위 검증 결과 (0299 D0004 §6). 서버가 documents.meta 에서 펼쳐 준다.
  trScope?: TrScopeVerdict | null
  qStatus?: string | null
  workflowSteps?: string[] | null
  orphan?: boolean
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

// ── R0001 (group 0126 / C안): section-level accordion ──────────────────────────
// Each info-panel section (status · 질의 · AI검수·반려) folds independently under its
// own title caret — the same caret idiom as the left file-tree. This is separate from
// the whole-panel collapse (the `dip-panel-close` chevron / `toggle` emit) so the two
// controls don't fight. Sections start expanded.
// 0311 T0004 rev1 §2: 'ai_review' is now the MERGED AI검수·반려 section's key. The old
// standalone 'reject' key is dropped — a repo-wide grep found no other reference to it
// (it had already been left dangling with no section of its own).
type SectionKey = 'status' | 'wp_assignments' | 'qa' | 'ai_review' | 'tr_scope' | 'changes'
const sectionCollapsed = reactive<Record<SectionKey, boolean>>({
  status: false,
  // 시안 xc32frrg 화면 1 은 이 칸을 펼친 채로 그린다.
  wp_assignments: false,
  qa: false,
  ai_review: false,
  // 통과이고 사유가 없으면 접어 둔다 (D0004 §6). 아래 watch 가 판정을 보고 연다.
  tr_scope: true,
  // 0325 T0006: AC 에서만 뜨는 섹션이고, 뜨는 이유 자체가 "지금 보라"이므로 펼친 채 시작한다.
  changes: false,
})

interface WorkPlanAssignment { provider_id: string; display_name: string; step_count: number }
const wpAssignments = ref<WorkPlanAssignment[]>([])
const wpUnassignedSteps = ref(0)
// 0399 M0020 반려 — 보여줄 것이 실제로 생겼을 때만 칸이 나타난다. 읽는 도중이거나
// 읽기에 실패했을 때는 자리를 차지하지 않으므로, 문서를 열 때 사이드바가
// 늘었다 줄었다 하지 않는다.
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

// 어긋난 항목 — 신고/감지 전체보다 먼저, 눈에 띄게 보여준다 (D0004 §6).
const trScopeDiffKeys = ['out_of_scope', 'unconfirmed', 'unreported', 'format_errors'] as const

// 신고/감지 전체 목록 — 어긋남 목록 다음에 같은 모양으로 나란히 둔다 (D0004 §6).
// 인라인 배열 리터럴로 두면 키가 string 으로 추론돼 인덱싱이 막히므로, 위 어긋남
// 목록과 똑같이 as const 로 리터럴 유니온을 유지한다.
const trScopeAllKeys = ['reported', 'detected'] as const

// 0390 TR0005 rev2 — 제출 시점에 검증이 돌지 않은 문서는 감지 목록이 존재하지
// 않는다. 그때 "감지 0건"을 그리면 "변경이 없었다"는 거짓말이 되므로 신고 목록만
// 남기고, 대신 왜 대조 결과가 없는지 한 줄 안내를 붙인다.
const trScopeUnevaluated = computed(() => props.trScope?.evaluated === false)
// 0390 TR0005 rev3 — 미검증에는 두 가지 사유가 있고 안내문이 달라야 한다. 본문에
// 변경 파일 절이 아예 없으면 "신고된 파일" 목록을 가리키는 문장은 거짓이 되므로,
// 절이 없다는 사실을 밝히는 별도 문장을 쓴다.
const trScopeUnevaluatedKey = computed(() =>
  props.trScope?.scope_reason === 'not_evaluated_no_section'
    ? 'tr_scope_unevaluated_no_section'
    : 'tr_scope_unevaluated',
)
const visibleTrScopeAllKeys = computed(() =>
  trScopeUnevaluated.value ? (['reported'] as const) : trScopeAllKeys,
)

// 없는 슬라이스는 빈 것으로 채워 돌려준다. 템플릿이 옵셔널 체이닝과 v-if/v-else-if
// 좁히기에 기대지 않아도 되고, "0건" 표시도 그대로 성립한다.
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
    // 경고·거부이거나 사유 코드가 하나라도 있으면 펼친다. 관측 단계에서는 통과로
    // 기록되지만 사유는 남으므로, 그때도 펼쳐서 운영자가 단계를 올리기 전에 무엇이
    // 걸릴지 미리 볼 수 있게 한다.
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

// Newest first. The merged AI검수·반려 feed re-sorts by real timestamp anyway, but this
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
// 0325 N0004 §3: 최종 승인(AC) 화면에서는 질의 응답 · AI 검수 의견 · 반려 사유를
// 감추고 그 자리에 소스 변경 요약을 띄운다. 세 섹션 모두 그 시점에는 이미 끝난
// 얘기라 스크롤만 늘린다.
const isAcDoc = computed(() => props.typeCode === 'AC')
// 0311 T0004 rev1 §1: qa is its own section again — the merge partner was wrong.
// Its show condition never matched reject/ai_review's in the first place (R/B/Q/M
// docs show queries but never a rejection), which is one of the reasons the pairing
// was wrong; only AC hides it.
const canShowQaSection = computed(() => !isAcDoc.value)

// ── AI검수·반려 (0311 T0004 rev1 §2: the pair that actually belongs together) ──
// These two conditions are character-for-character identical today, but they are kept
// as two named computeds and OR'd explicitly so the merged section keeps rendering if
// they ever diverge.
const canShowRejectSection = computed(() => !['R', 'B', 'Q', 'M', 'AC'].includes(props.typeCode ?? ''))
const canShowReviewSection = computed(() => !['R', 'B', 'Q', 'M', 'AC'].includes(props.typeCode ?? ''))
const canShowReviewRejectSection = computed(() => canShowRejectSection.value || canShowReviewSection.value)
// 0419 T0006 (NR0003 §"수정 허용 시점"): the [수정] entry point only appears while
// the document is currently rejected — once a rework submission moves it past
// 'rejected' (revised/pending_review/approved), the past rejection is history, not
// something to keep correcting. Group-disposed / AI-running / permission checks are
// authoritative on the server (update_rejection_reason_endpoint); this is UX-only.
const canEditRejection = computed(() => props.reviewStatus === 'rejected')

// Per-entry folds. The panel used to render exactly ONE review and ONE rejection, so a
// single ref each was enough; the merged feed renders several cards, so each card's
// comment / AI-response / reason fold is tracked under that card's own feed key.
// rev5 반려 §1: the findings fold is gone — the panel no longer draws the verdict badge
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
// R0001: "승인 후 머지 할까 말까 고민되는데 어떤 파일이 수정됐는지 볼 길이 없다."
// The numbers come from two endpoints that already exist and are already called
// elsewhere in the app — no new backend route:
//   · /projects/{pid}/git/groups/{gid}/changes  → 변경 파일 목록 + 파일별 +/- 줄 수
//   · /groups/{gid}/git/finalize                → base 대비 ahead/behind 커밋 수
// N0004 §2 dropped the per-directory file counts, so this stays a few lines tall.
const explorerStore = useExplorerStore()
const changes = ref<GroupChangeData[]>([])
const changesLoading = ref(false)
const changesError = ref(false)
const aheadCount = ref<number | null>(null)
const behindCount = ref<number | null>(null)
// 0325 TR0007 rev1 — branch names for the [변경사항 열기] viewer title, straight off
// the same /changes response the summary already reads.
const changesBranch = ref<string | null>(null)
const changesBaseBranch = ref<string | null>(null)
// 0382 제안 3: 변경 목록에서 뺀 "도구가 남긴 흔적". 접힌 한 줄로 늘 보여 준다.
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

async function recoverOrphan() {
  if (!props.orphan || recovering.value) return
  recovering.value = true
  try {
    await postRequest('/api/v1/documents/' + encodeURIComponent(props.docId) + '/workflow/recover', {})
    showToast(t('main.doc_info_panel.orphan_recovered'), 'success')
    emit('orphan-recovered')
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? String(e)
    showToast(t('main.doc_info_panel.orphan_recover_failed', { detail }), 'danger')
  } finally {
    recovering.value = false
  }
}
const { recordMentionCopy } = useMentionCopy()

// [멘트 복사] for one query item (0248 B0001 rework). The mention is fetched INSIDE the
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

// group 0126 / C안: unanswered count shown on the Q&A headline badge (matches the
// prototype's "미응답 N" pill — and the same count the header counter would show).
const qaUnansweredCount = computed(() => qaItems.value.filter((it) => !itemAnswered(it)).length)

// ── 0311 T0004 rev1: the two capped feeds ───────────────────────────────────────
// Both sections cap the panel at FEED_VISIBLE cards (NR0003 §5-3's actual
// recommendation). rev5 반려 §3·§4: the cap is now hard — the overflow is not announced
// with a "이전 항목 N건 더" line any more, it is simply not drawn, and each section's
// 머리줄 [전체보기] is the one door to the rest. Every preview text is clamped with an
// ellipsis so a long string cannot stretch the panel either (§4).
const FEED_VISIBLE = 3

// 질의: newest-registered first. question_items has no created_at column (server
// schema — server/sql/queries/queries.json "get_question_items" is seq ASC only), so
// seq is the only ordering on record. That is fine here precisely because qa is no
// longer interleaved with anything that has a real clock.
const qaFeed = computed<QaItem[]>(() => [...qaItems.value].reverse())
const qaFeedVisible = computed(() => qaFeed.value.slice(0, FEED_VISIBLE))

// AI검수·반려: sorted by REAL time — rejections carry rejected_at and reviews carry
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
    // 검수의견 fold, so reviews without a comment remain available in [전체보기] but do
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
// The 머리줄 [전체보기] must stay reachable even when the panel feed itself is empty, and
// even when the feed has more entries than FEED_VISIBLE shows.
const reviewRejectHasHistory = computed(() =>
  reviewRejectFeed.value.length > 0
  || (props.aiReviewHistory?.length ?? 0) > 0
  || !!props.aiReview
  || rejectionHistoryList.value.length > 0
  || !!props.rejectReason)
// 0422 TR0003 rev2 반려: 각 검수의견 토글 머리줄 안의 배지는 [전체보기]
// 다이얼로그의 verdictClass/verdictLabel과 같은 매핑을 그대로 따른다.
function reviewVerdictClass(r: AiReview): string {
  return r.verdict === 'pass' ? 'pass' : 'warn'
}
function reviewVerdictLabel(r: AiReview): string {
  if (r.verdict === 'pass') return t('main.doc_info_panel.ai_verdict_pass')
  if (r.verdict === 'hold') return t('main.doc_info_panel.ai_verdict_hold')
  return t('main.doc_info_panel.ai_verdict_issues', { n: r.finding_count ?? 0 })
}

// group 0126 / C안 + T0013 + 0311 T0004: qa full-history modal. TR0005 rev6 반려 §3
// ("질의는 빼라") split the once-merged dialog back in two — this ref only opens
// QaHistoryDialog now. The 질의 headline's [전체보기] opens it unfocused; each qa
// card's [답변] opens it focused on that query with the answer form started.
const qaHistoryVisible = ref(false)
const qaFocusId = ref<number | null>(null)
const qaStartAnswer = ref(false)
function openQaFull(focusId: number | null = null, startAnswer = false) {
  qaFocusId.value = focusId
  qaStartAnswer.value = startAnswer
  qaHistoryVisible.value = true
}

// AI검수·반려 섹션의 [전체보기] — 검수·반려 전용 QaReviewHistoryDialog 를 연다.
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
/* R0001 (group 0126 / C안): section-level accordion. The section title becomes a
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
  /* rev5 반려 §4: a long option label used to wrap over several lines inside a card that
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
   rev5 반려 §1: the entry head (.dip-ai-entry-head / .dip-ai-meta) and the findings list
   (.dip-ai-findings / .dip-ai-finding*) stay out of the panel — those stay in the
   [전체보기] dialog (.rhd-findings).
   0422 TR0003 rev2 반려("dip-ai-comment-toggle 여기"): each verdict badge lives inside
   its 검수의견 toggle header — same colours/shape as the dialog's .rhd-verdict. */
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
/* rev5 반려 §4 ("문자열이 너무 길면 ... 을 넣어라 어차피 전체보기에서 볼테니까"):
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

/* TR 작업범위 검증 카드 (0299 D0004 §6). 신고/감지 두 목록을 같은 모양으로 두어
   눈으로 대조할 수 있게 하고, 어긋난 항목만 색으로 구분한다. */
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
/* 미검증 안내 (0390 TR0005 rev2). 판정 뱃지 바로 아래 한 줄로, 목록보다 먼저 읽히게. */
.dip-trs-unevaluated { margin: 6px 0 0; font-size: .72rem; line-height: 1.5; color: var(--muted, #64748b); }
.dip-trs-codes { margin: 6px 0 0; padding-left: 16px; font-size: .72rem; line-height: 1.5; }
.dip-trs-list-label { margin: 8px 0 2px; font-size: .72rem; font-weight: 600; }
.dip-trs-list {
  margin: 0;
  padding-left: 16px;
  font-size: .7rem;
  line-height: 1.5;
  max-height: 180px;   /* 긴 목록이 패널 전체를 늘리지 않게 자체 스크롤 */
  overflow-y: auto;
  word-break: break-all;
}
.dip-trs-mismatch { color: var(--danger, #b91c1c); }
.dip-trs-more { color: var(--muted, #64748b); list-style: none; margin-left: -16px; }

/* 소스 변경 요약 (0325 R0001 / N0004 §2). 최종 승인 화면에서만 뜨고, 그 화면의
   판단("머지할까 말까")에 필요한 최소한만 — 파일 수 · 증감 줄 수 · 종류별 건수 ·
   base 대비 앞섬/뒤처짐. 디렉터리별 분포는 N0004 §2 에 따라 넣지 않는다. */
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
/* [변경사항 열기] — 요약의 결론이 아니라 진입점이므로 섹션 맨 아래 전체 너비로 둔다. */
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
/* rev5 반려 §4: same as the comment box — open means 6 clamped lines with an
   ellipsis, not a scroll box. */
.dip-ai-response.open .dip-ai-response-body {
  max-height: 10em;
  -webkit-line-clamp: 6;
}

/* 시안 xc32frrg 화면 1 — 프로바이더 배정 (단계 기준) */
.dip-wp-assignments { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.dip-wp-assignments li { display: flex; align-items: center; gap: 6px; font-size: .74rem; }
.dip-wp-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--primary, #2563eb); flex-shrink: 0; }
.dip-wp-prov { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dip-wp-assignments strong { margin-left: auto; font-variant-numeric: tabular-nums; }

/* 0311 T0004 rev1 §2 — 합쳐진 AI 검수·반려 피드에서 항목 사이의 간격. 이번 병합에서
   새로 넣은 유일한 스타일이다: 카드 자체는 위의 .dip-reject-quote / .dip-ai-entry 를
   그대로 쓰고, 여러 장이 세로로 이어질 때의 간격만 여기서 준다(감싸는 상자 없음). */
.dip-rr-entry + .dip-rr-entry { margin-top: 10px; }
</style>
