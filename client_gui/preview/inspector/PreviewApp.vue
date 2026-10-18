<template>
  <!-- flowgate.v02.0051 — Inspector parts gallery (fixture). One section per part, each sample inside a
       frame the width of the real Inspector (944px at the 1348px bottom panel, 0035 v7). Nothing here is
       server data; buttons are shown, not wired (⋯ → ID 복사 excepted). Stage 2 adds the card parts: the
       per-card slot lines, header items and settings blocks, and the Frame with the real registry on the
       Nodes whose parts need no server read. -->
  <main class="preview">
    <header class="preview-head">
      <h1>Inspector 부품 미리보기 <small>flowgate.v02.0051 · 1단계 공용 부품 + 2단계 카드별 부품</small></h1>
      <p class="preview-warn">⚠ fixture 화면 — 모든 값은 0035 v7 시안의 예시이며 실제 데이터가 아닙니다. 버튼은 보이기만 합니다.</p>
      <nav class="preview-nav">
        <a v-for="section in SECTIONS" :key="section.id" :href="`#${section.id}`">{{ section.title }}</a>
        <span class="preview-push" />
        <span class="preview-locale">locale: {{ locale }}</span>
      </nav>
    </header>

    <!-- 1. Header line -->
    <section v-if="shown('header')" id="header" class="preview-section">
      <h2>헤더 (InspectorHeader) <small>40px · 아이콘 · 종류 배지 · 제목 · [읽기 전용] · 메타 · ✎ 메모 · [문서 상태 · 실행 상태 · ● 상태] 한 pill · 액션 · ⋯</small></h2>

      <p class="preview-caption">요구사항 · 작성 중 (unassigned) — 액션: ✓ 승인</p>
      <div class="preview-frame">
        <InspectorHeader :target="REQ_DRAFT">
          <template #actions><Button size="sm" variant="primary">✓ 승인</Button></template>
        </InspectorHeader>
      </div>

      <p class="preview-caption">버그 · 작성 중 (unassigned) — 2단계: B 문서는 버그 아이콘 · 빨강 · “버그” 배지</p>
      <div class="preview-frame">
        <InspectorHeader :target="REQ_BUG">
          <template #actions><Button size="sm" variant="primary">✓ 승인</Button></template>
        </InspectorHeader>
      </div>

      <p class="preview-caption">작업 · 검수 대기 (Graph · AI 위임 전개) — 메타: 🤖 AI · [검수 대기 · waiting_review · ● 검수 대기] 한 pill · 액션: ✓ 승인 / ✕ 반려 ▾</p>
      <div class="preview-frame">
        <InspectorHeader :target="TASK_REVIEW">
          <template #meta><InspectorOriginBadge origin="ai" /></template>
          <template #actions>
            <Button size="sm" variant="primary">✓ 승인</Button>
            <Button size="sm" variant="danger">✕ 반려 ▾</Button>
          </template>
        </InspectorHeader>
      </div>

      <p class="preview-caption">설계 · 읽기 전용 (ownership conflict) — 읽기 전용 태그 · 액션 비활성</p>
      <div class="preview-frame">
        <InspectorHeader :target="DESIGN_READ_ONLY">
          <template #actions>
            <Button size="sm" variant="primary" disabled>✓ 승인</Button>
            <Button size="sm" variant="danger" disabled>✕ 반려 ▾</Button>
          </template>
        </InspectorHeader>
      </div>

      <p class="preview-caption">확인 (Graph gate · 문서 없음) — 헤더 공통 항목은 그대로 (ID 복사만 비활성)</p>
      <div class="preview-frame">
        <InspectorHeader :target="GATE_NO_DOCUMENT" />
      </div>

      <p class="preview-caption">좁은 폭 (360px Center 최소 → Inspector 264px) — 2단계: 제목이 먼저 줄고, 메모는 ✎만, 액션은 가로 스크롤, 줄 밖으로 넘치지 않음</p>
      <div class="preview-frame preview-frame--narrow">
        <InspectorHeader :target="TASK_REVIEW">
          <template #meta><InspectorOriginBadge origin="ai" /></template>
          <template #actions>
            <Button size="sm" variant="primary">✓ 승인</Button>
            <Button size="sm" variant="danger">✕ 반려 ▾</Button>
          </template>
        </InspectorHeader>
      </div>
    </section>

    <!-- 2. Status chips -->
    <section v-if="shown('chips')" id="chips" class="preview-section">
      <h2>상태 칩 (InspectorStatusChip) <small>v7 pill 7색 · sub · ● dot · title</small></h2>
      <div class="preview-frame preview-pad">
        <div class="preview-row">
          <InspectorStatusChip v-for="chip in CHIPS" :key="chip.tone" :label="chip.label" :tone="chip.tone" />
        </div>
        <div class="preview-row">
          <InspectorStatusChip label="승인" tone="success" sub="자동" dot title="자동 승인 — 실행 방식: 전체 실행" />
          <InspectorStatusChip label="위임 진행 중" tone="running" sub="2/4" dot />
          <InspectorStatusChip label="질문 대기" tone="warning" sub="미응답 2" dot />
          <InspectorStatusChip label="한도 도달" tone="danger" sub="검수/재작업 30/30" dot />
          <InspectorStatusChip label="대기" tone="neutral" sub="선행 미완료" dot />
        </div>
      </div>
    </section>

    <!-- 3. Slot lines -->
    <section v-if="shown('slots')" id="slots" class="preview-section">
      <h2>문서 / 결과 슬롯 행 (InspectorSlotRow) <small>38px · 이름 · 상태 배지(뜻이 있을 때만) + 요약 / 진행도 · 버튼(이력 · 문서 아이콘 · ✎)</small></h2>
      <div class="preview-frame preview-pad">
        <div class="preview-slots">
          <template v-for="(slot, i) in SLOT_FIXTURES" :key="i">
            <p class="preview-caption preview-caption--tight">{{ slot.caption }}</p>
            <InspectorSlotRow
              :kind="slot.kind ?? 'document'"
              :name="slot.name"
              :id="slot.id ?? null"
              :openable="slot.open ?? false"
              :empty="slot.empty ?? false"
              :empty-text="slot.emptyText ?? null"
              :state="slot.state ?? null"
              :summary="slot.summary ?? null"
              :progress="slot.progress ?? null"
              :focus="slot.focus ?? false"
              :danger="slot.danger ?? false"
            >
              <InspectorReviewBadge v-if="slot.review" :count="slot.review.count" :state="slot.review.state" :remain="slot.review.remain ?? null" />
              <Button v-if="slot.nav" size="sm" variant="secondary">{{ slot.nav }} ↗</Button>
              <InspectorOpenButton v-if="slot.open" />
              <IconButton v-if="slot.open" label="편집" title="편집" variant="secondary" size="sm">✎</IconButton>
            </InspectorSlotRow>
          </template>
        </div>
      </div>
    </section>

    <!-- 4. Review badge -->
    <section v-if="shown('review')" id="review" class="preview-section">
      <h2>검수 배지 (InspectorReviewBadge) <small>🛡 n · pass / warn / run / limit · 비활성</small></h2>
      <div class="preview-frame preview-pad">
        <div class="preview-row">
          <InspectorReviewBadge :count="2" state="pass" :remain="0" />
          <InspectorReviewBadge :count="1" state="warn" />
          <InspectorReviewBadge :count="1" state="run" />
          <InspectorReviewBadge :count="2" state="limit" :remain="1" />
          <InspectorReviewBadge :count="2" state="pass" :remain="0" disabled />
        </div>
      </div>
    </section>

    <!-- 5. Origin badge -->
    <section v-if="shown('origin')" id="origin" class="preview-section">
      <h2>출처 배지 (InspectorOriginBadge) <small>🤖 AI · ⇉ WP · 작성 사람 / AI</small></h2>
      <div class="preview-frame preview-pad">
        <div class="preview-row">
          <InspectorOriginBadge origin="ai" />
          <InspectorOriginBadge origin="wp" />
          <InspectorOriginBadge origin="wp" label="작성 사람" title="사람 WP: 사람이 쓰고 사람이 전개" />
          <InspectorOriginBadge origin="ai" label="작성 AI" title="AI 위임: Supervisor가 쓰고 자동 전개" />
        </div>
      </div>
    </section>

    <!-- 6. Block / control row / segment / lock -->
    <section v-if="shown('blocks')" id="blocks" class="preview-section">
      <h2>설정 블록 · 행 · 세그먼트 · 잠금 <small>InspectorBlock · InspectorControlRow · InspectorSegment · InspectorLockNote</small></h2>
      <div class="preview-frame preview-pad">
        <InspectorBlock>
          <InspectorControlRow label="지시서" hint="생략하면 문서 탭에 지시서 줄이 나오지 않습니다">
            <InspectorSegment v-model="useInstruction" :options="[{ value: '1', label: '사용' }, { value: '0', label: '생략' }]" aria-label="지시서" />
          </InspectorControlRow>
        </InspectorBlock>
        <div class="preview-gap" />
        <InspectorLockNote />
        <InspectorBlock locked>
          <InspectorControlRow label="지시서">
            <InspectorSegment model-value="1" :options="[{ value: '1', label: '사용' }, { value: '0', label: '생략' }]" disabled />
          </InspectorControlRow>
        </InspectorBlock>
      </div>
    </section>

    <!-- 7. Run settings -->
    <section v-if="shown('run-settings')" id="run-settings" class="preview-section">
      <h2>실행 설정 블록 (InspectorRunSettings) <small>실행 AI · 재시작 | 검수 AI · 검수 횟수 · 고급 ▾ (실행 제한 시간 · 검수 기준)</small></h2>
      <p class="preview-caption">전체 연결 가정 (시안 값) · 고급 열림</p>
      <div class="preview-frame preview-pad">
        <InspectorRunSettings :fields="RUN_SETTINGS_SAMPLE" :advanced-open="advancedB" @update:advanced-open="advancedB = $event" />
      </div>
      <p class="preview-caption">0048 실제 연결 범위 — 실행 AI · 검수 AI · 검수 횟수만 저장됨, 나머지는 비활성 ‘—’ (설정 탭의 실제 모습, 0051-R §2.3)</p>
      <div class="preview-frame preview-pad">
        <InspectorRunSettings :fields="RUN_SETTINGS_PARTIAL" :advanced-open="true" />
      </div>
    </section>

    <!-- 8. Pane states -->
    <section v-if="shown('pane')" id="pane" class="preview-section">
      <h2>빈 / 불러오는 중 / 오류 / 비활성 (InspectorPaneState) · 미정 표시 (InspectorTbdNote)</h2>
      <div class="preview-frame preview-pad">
        <div class="preview-grid-2">
          <InspectorPaneState state="empty" message="질문 없음" />
          <InspectorPaneState state="loading" />
          <InspectorPaneState state="error" message="409 문서가 잠겨 있습니다." retryable />
          <InspectorPaneState state="disabled" message="메모 — 아직 연결되지 않음" />
        </div>
        <div class="preview-gap" />
        <div class="preview-row">
          <InspectorTbdNote kind="tag" label="토론 중" title="확인 카드 용도는 토론 중" />
          <InspectorTbdNote kind="tag" label="미정" />
        </div>
      </div>
    </section>

    <!-- 9. Stage 2 — card slot lines -->
    <section v-if="shown('cards')" id="cards" class="preview-section">
      <h2>2단계 · 카드별 문서 탭 부품 <small>RequirementSlots · ResearchSlots · TaskSlots · DesignSlots · GateSlots — target만으로 그린 실제 부품</small></h2>
      <template v-for="sample in CARD_SAMPLES" :key="sample.caption">
        <p class="preview-caption">{{ sample.caption }}</p>
        <div class="preview-frame preview-pad">
          <component :is="sample.component" :target="sample.target" />
        </div>
      </template>

      <h2 class="preview-sub-h2">작업계획 · 테스트 · 확인 · AI 위임 — v7 예시 줄 (고정값) <small>실제 부품(WorkPlanTab · TestSpecTab · CheckTab)은 서버 읽기가 필요해 Designer에서 확인</small></h2>
      <template v-for="fixture in CARD_SLOT_FIXTURES" :key="fixture.caption">
        <p class="preview-caption">{{ fixture.caption }}</p>
        <div class="preview-frame preview-pad">
          <InspectorSlotList :slots="fixture.lines" />
        </div>
      </template>
    </section>

    <!-- 10. Stage 2 — card header items -->
    <section v-if="shown('card-headers')" id="card-headers" class="preview-section">
      <h2>2단계 · 카드별 헤더 부품 <small>종류 배지 ▾ (KindSwitchAction) · ⇉ 전개 / ⇉ 재전개 ▾ (WorkPlanActions) · 작성 사람 (WorkPlanOrigin) · ✓ 확인 / ✕ 반려 (GateActions) · ⋯ 메뉴 항목 (InspectorRunMenu)</small></h2>

      <p class="preview-caption">요구사항 (unassigned · 문서 있음) — 종류 배지 ▾: 실제 부품. 이 화면에서는 클릭을 막아 두었습니다(서버 호출 없음). 누르면 📄 요구사항 ✓ / 🐞 버그 메뉴 → 확인 대화상자</p>
      <div class="preview-frame preview-inert">
        <InspectorHeader :target="REQ_DRAFT">
          <template #kind><KindSwitchAction :target="REQ_DRAFT" /></template>
          <template #actions><Button size="sm" variant="primary">✓ 승인</Button></template>
        </InspectorHeader>
      </div>

      <p class="preview-caption">작업계획 (unassigned · WP 작성 중) — 메타: 작성 사람 · 액션: ⇉ 전개 / ⇉ 재전개 ▾ (비활성: 전개 API 없음)</p>
      <div class="preview-frame">
        <InspectorHeader :target="PLAN_WP">
          <template #meta><WorkPlanOrigin :target="PLAN_WP" /></template>
          <template #actions><WorkPlanActions :target="PLAN_WP" /></template>
        </InspectorHeader>
      </div>

      <p class="preview-caption">확인 (Graph gate · 문서 없음) — 액션: ✓ 확인 / ✕ 반려 ▾ (비활성: gate 확인·반려 API 없음, 용도 토론 중)</p>
      <div class="preview-frame">
        <InspectorHeader :target="GATE_NO_DOCUMENT">
          <template #actions><GateActions :target="GATE_NO_DOCUMENT" /></template>
        </InspectorHeader>
      </div>

      <p class="preview-caption">⋯ 메뉴 (조사 · 작업 카드) — ID 복사 뒤에 실행 이력 · ■ 중단 · ↻ 재실행 (비활성). 아래는 메뉴를 펼친 모습</p>
      <div class="preview-frame preview-pad">
        <div class="preview-menu">
          <InspectorMenuItem item="copy_id">ID 복사</InspectorMenuItem>
          <InspectorRunMenu />
        </div>
      </div>
    </section>

    <!-- 11. Stage 2 — card settings -->
    <section v-if="shown('card-settings')" id="card-settings" class="preview-section">
      <h2>2단계 · 카드별 설정 탭 부품 <small>RequirementSettings (AI 위임) · TestRunSettings (실행) · CheckSettings (용도 · Git 반영)</small></h2>

      <p class="preview-caption">요구사항 · 실제 (서버 없이: 끄기 · 비활성 · 사유는 툴팁)</p>
      <div class="preview-frame preview-pad"><RequirementSettings :target="REQ_DRAFT" /></div>
      <p class="preview-caption">요구사항 · AI 위임 ON (시안 값, fixture)</p>
      <div class="preview-frame preview-pad"><RequirementSettings :target="REQ_DRAFT" :view="REQ_SETTINGS_ON" /></div>
      <p class="preview-caption">요구사항 · AI 위임 한도 도달 · 🔒 위임 진행 중 (시안 모양, fixture)</p>
      <div class="preview-frame preview-pad"><RequirementSettings :target="REQ_DRAFT" :view="REQ_SETTINGS_LIMIT" /></div>

      <p class="preview-caption">테스트 · 실행 블록 · 실제 (서버 없이: 개수 ‘—’) — 지시서 · 시나리오 작성은 설정 feature가 위에 그림</p>
      <div class="preview-frame preview-pad"><TestRunSettings :target="TEST_TS_RUNNING" /></div>
      <p class="preview-caption">테스트 · 실행 블록 · 시안 값 (fixture: 자동 49 · 수동 6 · 외부 2)</p>
      <div class="preview-frame preview-pad"><TestRunSettings :target="TEST_TS_RUNNING" :view="TEST_RUN_SETTINGS_SAMPLE" /></div>

      <p class="preview-caption">확인 · 실제 (서버 없이: 용도 — · 토론 중 · Git 반영 비활성 · 사유는 툴팁)</p>
      <div class="preview-frame preview-pad"><CheckSettings :target="CHECK_AC" /></div>
      <p class="preview-caption">확인 · 시안 값 (fixture) · 그룹 종료 · 병합 · main · 원격 반영</p>
      <div class="preview-frame preview-pad"><CheckSettings :target="CHECK_AC" :view="CHECK_SETTINGS_SAMPLE" /></div>
    </section>

    <!-- 12. Stage 2 — the Frame with the real registry on Nodes without a document -->
    <section v-if="shown('card-frames')" id="card-frames" class="preview-section">
      <h2>2단계 · 실제 레지스트리로 조립한 프레임 <small>문서 없는 Graph Node — 서버 읽기 없이 실제 부품만으로 그려짐 (탭 노출 규칙은 아직 0048: 기여 탭만)</small></h2>
      <p class="preview-caption">확인 (Graph gate) — 0048에서는 헤더만 남던 Node: 문서 탭에 확인 결과 줄, 헤더에 ✓ 확인 / ✕ 반려 ▾ (비활성)</p>
      <div class="preview-frame preview-frame--inspector">
        <DesignerInspector :selection="SELECTION_GATE" />
      </div>
      <p class="preview-caption">요구사항 (Graph Node · 문서 없음) — 문서 탭: 요구사항 줄 (빈) · 설정 탭: AI 위임</p>
      <div class="preview-frame preview-frame--inspector">
        <DesignerInspector :selection="SELECTION_REQ_NO_DOCUMENT" />
      </div>
      <p class="preview-caption">조사 (Graph Node · 문서 없음) — 문서 탭: 지시서 · 레포트 줄 (빈) · ⋯ 메뉴: 실행 이력 · ■ 중단 · ↻ 재실행 (비활성)</p>
      <div class="preview-frame preview-frame--inspector">
        <DesignerInspector :selection="SELECTION_RESEARCH_NO_DOCUMENT" />
      </div>
      <p class="preview-caption">작업 (Graph Node · 문서 없음) — 문서 탭: 지시서 · 레포트 줄 (빈)</p>
      <div class="preview-frame preview-frame--inspector">
        <DesignerInspector :selection="SELECTION_TASK_NO_DOCUMENT" />
      </div>
    </section>

    <!-- 13. The frame with a fixture registry (stage 1 sample) -->
    <section v-if="shown('frame')" id="frame" class="preview-section">
      <h2>프레임 조립 예 (DesignerInspector + fixture registry) <small>헤더 40 + 탭 32 + Content · 944 × 360 · 조립은 3단계에서</small></h2>
      <p class="preview-caption">작업 · 검수 대기 — 문서 탭에 슬롯 2줄, Q&A / 첨부 빈 상태, 설정 탭에 실행 설정 (탭 노출 규칙은 아직 0048 그대로: 기여가 있는 탭만)</p>
      <div class="preview-frame preview-frame--inspector">
        <DesignerInspector :selection="SELECTION_TASK_REVIEW" :registry="PREVIEW_REGISTRY" />
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'
import DesignSlots from '@/features/designer/inspector/features/designCard/DesignSlots.vue'
import GateActions from '@/features/designer/inspector/features/gateCard/GateActions.vue'
import GateSlots from '@/features/designer/inspector/features/gateCard/GateSlots.vue'
import KindSwitchAction from '@/features/designer/inspector/features/kindSwitch/KindSwitchAction.vue'
import RequirementSlots from '@/features/designer/inspector/features/requirementCard/RequirementSlots.vue'
import RequirementSettings from '@/features/designer/inspector/features/requirementSettings/RequirementSettings.vue'
import ResearchSlots from '@/features/designer/inspector/features/researchCard/ResearchSlots.vue'
import TaskSlots from '@/features/designer/inspector/features/taskCard/TaskSlots.vue'
import TestRunSettings from '@/features/designer/inspector/features/testRunSettings/TestRunSettings.vue'
import WorkPlanActions from '@/features/designer/inspector/features/workPlan/WorkPlanActions.vue'
import WorkPlanOrigin from '@/features/designer/inspector/features/workPlan/WorkPlanOrigin.vue'
import { createInspectorRegistry, type InspectorRegistration } from '@/features/designer/inspector/inspectorRegistry'
import InspectorBlock from '@/features/designer/inspector/parts/InspectorBlock.vue'
import InspectorControlRow from '@/features/designer/inspector/parts/InspectorControlRow.vue'
import InspectorLockNote from '@/features/designer/inspector/parts/InspectorLockNote.vue'
import InspectorMenuItem from '@/features/designer/inspector/parts/InspectorMenuItem.vue'
import InspectorOriginBadge from '@/features/designer/inspector/parts/InspectorOriginBadge.vue'
import InspectorPaneState from '@/features/designer/inspector/parts/InspectorPaneState.vue'
import InspectorReviewBadge from '@/features/designer/inspector/parts/InspectorReviewBadge.vue'
import InspectorRunMenu from '@/features/designer/inspector/parts/InspectorRunMenu.vue'
import InspectorRunSettings from '@/features/designer/inspector/parts/InspectorRunSettings.vue'
import InspectorSegment from '@/features/designer/inspector/parts/InspectorSegment.vue'
import InspectorSlotList from '@/features/designer/inspector/parts/InspectorSlotList.vue'
import InspectorOpenButton from '@/features/designer/inspector/parts/InspectorOpenButton.vue'
import InspectorSlotRow from '@/features/designer/inspector/parts/InspectorSlotRow.vue'
import InspectorStatusChip, { type InspectorChipTone } from '@/features/designer/inspector/parts/InspectorStatusChip.vue'
import InspectorTbdNote from '@/features/designer/inspector/parts/InspectorTbdNote.vue'
import {
  CARD_SLOT_FIXTURES,
  CHECK_AC,
  CHECK_SETTINGS_SAMPLE,
  DESIGN_NO_DOCUMENT,
  DESIGN_READ_ONLY,
  GATE_NO_DOCUMENT,
  PLAN_WP,
  REQ_BUG,
  REQ_DRAFT,
  REQ_NO_DOCUMENT,
  REQ_SETTINGS_LIMIT,
  REQ_SETTINGS_ON,
  RESEARCH_N,
  RESEARCH_N_RUN,
  RESEARCH_NO_DOCUMENT,
  RESEARCH_NR_REJECTED,
  RESEARCH_NR_REVIEW,
  RUN_SETTINGS_PARTIAL,
  RUN_SETTINGS_SAMPLE,
  SELECTION_GATE,
  SELECTION_REQ_NO_DOCUMENT,
  SELECTION_RESEARCH_NO_DOCUMENT,
  SELECTION_TASK_NO_DOCUMENT,
  SELECTION_TASK_REVIEW,
  SLOT_FIXTURES,
  TASK_NO_DOCUMENT,
  TASK_REVIEW,
  TASK_T,
  TEST_RUN_SETTINGS_SAMPLE,
  TEST_TS_RUNNING,
} from './fixtures'
import PreviewDocumentsTab from './PreviewDocumentsTab.vue'

const props = defineProps<{ only: string | null }>()

const { locale } = useI18n({ useScope: 'global' })

const SECTIONS = [
  { id: 'header', title: '헤더' },
  { id: 'chips', title: '상태 칩' },
  { id: 'slots', title: '슬롯 행' },
  { id: 'review', title: '검수 배지' },
  { id: 'origin', title: '출처 배지' },
  { id: 'blocks', title: '블록·행·세그먼트' },
  { id: 'run-settings', title: '실행 설정' },
  { id: 'pane', title: '빈/비활성/미정' },
  { id: 'cards', title: '2단계 · 카드 문서 탭' },
  { id: 'card-headers', title: '2단계 · 카드 헤더' },
  { id: 'card-settings', title: '2단계 · 카드 설정' },
  { id: 'card-frames', title: '2단계 · 실제 조립' },
  { id: 'frame', title: '프레임 예' },
] as const

const shown = (id: string) => !props.only || props.only === id

const CHIPS: readonly { tone: InspectorChipTone; label: string }[] = [
  { tone: 'neutral', label: '대기 (idle)' },
  { tone: 'info', label: '실행 가능 (ready)' },
  { tone: 'running', label: '실행 중 (run)' },
  { tone: 'warning', label: '검수 대기 (wait)' },
  { tone: 'danger', label: '반려됨 (danger)' },
  { tone: 'success', label: '승인 (good)' },
  { tone: 'hold', label: '반영 보류 (hold)' },
]

/** The card parts drawn from a target alone (no server read), one sample per document state. */
const CARD_SAMPLES = [
  { caption: '요구사항 · 작성 중 (unassigned · 문서 있음)', component: RequirementSlots, target: REQ_DRAFT },
  { caption: '버그 · 작성 중 — 줄 이름이 버그', component: RequirementSlots, target: REQ_BUG },
  { caption: '요구사항 (Graph Node · 문서 없음) — 빈 줄 · ＋ 비활성', component: RequirementSlots, target: REQ_NO_DOCUMENT },
  { caption: '조사 · 지시서 (N) 승인 (v0.1) — 레포트는 “실행 후”', component: ResearchSlots, target: RESEARCH_N },
  { caption: '조사 · 레포트 (NR) 검수 대기 (v0.1) — 레포트 줄 강조, 지시서는 연결 전', component: ResearchSlots, target: RESEARCH_NR_REVIEW },
  { caption: '조사 · 레포트 (NR) 반려됨 (v0.1) — 빨강', component: ResearchSlots, target: RESEARCH_NR_REJECTED },
  { caption: '조사 · Graph N 실행 완료 — 레포트 줄에 실행 결과 문서 (상태 미연결)', component: ResearchSlots, target: RESEARCH_N_RUN },
  { caption: '조사 (Graph Node · 문서 없음)', component: ResearchSlots, target: RESEARCH_NO_DOCUMENT },
  { caption: '작업 · 지시서 (T) 승인 (v0.1)', component: TaskSlots, target: TASK_T },
  { caption: '작업 · 레포트 (TR) 검수 대기 (Graph · 실행 결과 = 자기 문서)', component: TaskSlots, target: TASK_REVIEW },
  { caption: '작업 (Graph Node · 문서 없음)', component: TaskSlots, target: TASK_NO_DOCUMENT },
  { caption: '설계 · 반려됨 (읽기 전용) — 지시서 · 레포트 줄', component: DesignSlots, target: DESIGN_READ_ONLY },
  { caption: '설계 · P (Graph Node · 문서 없음)', component: DesignSlots, target: DESIGN_NO_DOCUMENT },
  { caption: '확인 (Graph gate · 문서 없음) — 확인 결과 줄 (확인 전)', component: GateSlots, target: GATE_NO_DOCUMENT },
] as const

const useInstruction = ref('1')
const advancedB = ref(true)

// A fixture registry for the stage 1 frame sample: the four v7 tabs filled with common parts.
const EMPTY = { ko: {}, en: {}, ja: {} } as const
const pane = (message: string) => defineComponent({ props: { target: { type: Object, required: true } }, setup: () => () => h(InspectorPaneState, { state: 'empty', message }) })
const settings = defineComponent({
  props: { target: { type: Object, required: true } },
  setup: () => {
    const open = ref(false)
    return () => h(InspectorRunSettings, { fields: RUN_SETTINGS_SAMPLE, advancedOpen: open.value, 'onUpdate:advancedOpen': (value: boolean) => (open.value = value) })
  },
})
const entries: InspectorRegistration[] = [
  { key: 'documents', order: 500, requiresDocument: true, tab: { key: 'documents', component: PreviewDocumentsTab }, messages: EMPTY },
  { key: 'qna', order: 700, requiresDocument: true, tab: { key: 'qna', component: pane('질문 없음'), count: () => computed(() => null) }, messages: EMPTY },
  { key: 'attachments', order: 800, requiresDocument: true, tab: { key: 'attachments', component: pane('첨부 없음') }, messages: EMPTY },
  { key: 'settings', order: 900, requiresDocument: true, tab: { key: 'settings', component: settings }, messages: EMPTY },
]
const PREVIEW_REGISTRY = createInspectorRegistry(Object.fromEntries(entries.map((entry) => [`./features/${entry.key}/register.ts`, { default: entry }])))
</script>

<style scoped>
.preview {
  max-width: 1500px;
  margin: 0 auto;
  padding: 18px 22px 60px;
  color: var(--fg2-text);
}

.preview-head h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 800;
}

.preview-head h1 small,
.preview-section h2 small {
  margin-left: 8px;
  color: #7f96af;
  font-size: 11.5px;
  font-weight: 500;
}

.preview-warn {
  display: inline-block;
  margin: 10px 0 0;
  padding: 6px 12px;
  border: 1px solid #7b5a17;
  border-radius: 8px;
  background: #3b2a0d;
  color: #ffd27a;
  font-size: 12px;
}

.preview-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
  padding-bottom: 12px;
  border-bottom: 1px dashed #23405f;
}

.preview-nav a {
  padding: 3px 9px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 999px;
  color: #9fb3c9;
  font-size: 11px;
}

.preview-push {
  flex: 1 1 auto;
}

.preview-locale {
  color: #7f96af;
  font-size: 11px;
}

.preview-section {
  margin-top: 26px;
}

.preview-section h2 {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 800;
}

.preview-sub-h2 {
  margin-top: 18px !important;
}

.preview-caption {
  margin: 10px 0 4px;
  color: #a9bccf;
  font-size: 12px;
}

.preview-caption--tight {
  margin: 6px 0 0;
  color: #7f96af;
  font-size: 11px;
}

.preview-frame {
  display: flex;
  flex-direction: column;
  width: 944px;
  max-width: 100%;
  overflow: hidden;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: #091624;
}

.preview-frame--narrow {
  width: 264px;
}

.preview-frame--inspector {
  height: 360px;
}

.preview-frame--inspector :deep(.designer-inspector) {
  flex: 1 1 auto;
  min-height: 0;
}

/* The one live part with a server-side operation: shown, never clickable here. */
.preview-inert {
  pointer-events: none;
}

.preview-pad {
  padding: 12px 14px;
}

.preview-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.preview-row + .preview-row {
  margin-top: 10px;
}

.preview-slots {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.preview-grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 2px 18px;
}

.preview-gap {
  height: 12px;
}

.preview-muted {
  color: #7f96af;
  font-size: 11px;
}

.preview-menu {
  display: flex;
  flex-direction: column;
  width: 200px;
  padding: 4px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: #0f2035;
}
</style>
