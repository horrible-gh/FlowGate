<template>
  <div class="ai-overview-panel">
    <div class="page-title">{{ t('gui.ai.title') }}</div>
    <div class="page-sub">{{ t('gui.ai.subtitle') }}</div>

    <!-- 3-step banner -->
    <section class="step-grid">
      <div class="step-wrap">
        <article class="step-card blue">
          <div class="step-head">
            <div class="step-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8">
                <ellipse cx="12" cy="5" rx="7" ry="2.8" />
                <path d="M5 5v14c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V5" />
                <path d="M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8" />
              </svg>
            </div>
            <div class="step-title"><span class="num">1</span> {{ t('gui.ai.overview.step1_title') }}</div>
          </div>
          <div class="step-desc">{{ t('gui.ai.overview.step1_desc') }}</div>
          <button class="step-action" type="button" @click="emit('navigate', 'providers')">
            {{ t('gui.ai.overview.step1_action') }} <span>→</span>
          </button>
        </article>
      </div>

      <div class="step-wrap">
        <article class="step-card cyan">
          <div class="step-head">
            <div class="step-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8">
                <path d="M9 11.5 11 13.5 15.5 9" />
                <rect x="4" y="3.5" width="16" height="17" rx="2.5" />
              </svg>
            </div>
            <div class="step-title"><span class="num">2</span> {{ t('gui.ai.overview.step2_title') }}</div>
          </div>
          <div class="step-desc">{{ t('gui.ai.overview.step2_desc') }}</div>
          <button class="step-action" type="button" @click="emit('navigate', 'evaluation')">
            {{ t('gui.ai.overview.step2_action') }} <span>→</span>
          </button>
        </article>
      </div>

      <div class="step-wrap">
        <article class="step-card purple">
          <div class="step-head">
            <div class="step-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8">
                <path d="M16 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2" />
                <circle cx="9.5" cy="7" r="3.2" />
                <path d="M21 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M14.5 4.2A3.2 3.2 0 0 1 14.5 10.5" />
              </svg>
            </div>
            <div class="step-title"><span class="num">3</span> {{ t('gui.ai.overview.step3_title') }}</div>
          </div>
          <div class="step-desc">{{ t('gui.ai.overview.step3_desc') }}</div>
          <button class="step-action" type="button" @click="emit('navigate', 'groups')">
            {{ t('gui.ai.overview.step3_action') }} <span>→</span>
          </button>
        </article>
      </div>
    </section>

    <!-- 3 lower panels -->
    <section class="lower">
      <!-- Panel 1: Provider -->
      <section class="panel">
        <div class="panel-head">
          <div class="grow">
            <div class="panel-title">{{ t('gui.ai.overview.providers_title') }}</div>
            <div class="panel-sub">
              {{ providersSummaryText }}
            </div>
          </div>
          <button class="panel-action" type="button" @click="emit('navigate', 'providers')">
            ＋ {{ t('gui.ai.overview.action_add') }}
          </button>
        </div>
        <div class="panel-body">
          <div v-if="hasSectionError('providers')" class="panel-error">
            <p>{{ t('gui.ai.overview.section_error') }}</p>
          </div>
          <div v-else-if="!providers.length" class="panel-empty">
            <p>{{ t('gui.ai.overview.providers_empty') }}</p>
          </div>
          <div
            v-for="provider in providers"
            :key="provider.id"
            class="provider-row clickable"
            @click="emit('navigate', 'providers')"
          >
            <div class="avatar-round" :class="getAvatarClass(provider.name, provider.kind)">
              {{ getInitials(provider.name) }}
            </div>
            <div class="provider-info">
              <div class="main-title">{{ provider.name }}</div>
              <div class="subrow">
                <span class="badge">{{ provider.exec_type.toUpperCase() }}</span>
                <span>{{ provider.kind || 'Custom' }}</span>
              </div>
            </div>
            <div class="status-chip" :class="{ off: !provider.enabled }">
              <i></i>{{ provider.enabled ? t('gui.ai.common.active') : t('gui.ai.common.inactive') }}
            </div>
            <div class="chev">›</div>
          </div>
        </div>
      </section>

      <!-- Panel 2: Evaluation Results -->
      <section class="panel">
        <div class="panel-head">
          <div class="grow">
            <div class="panel-title">{{ t('gui.ai.overview.evaluation_title') }}</div>
            <div class="panel-sub">
              {{ evaluationSummaryText }}
            </div>
          </div>
          <button class="panel-action" type="button" @click="emit('navigate', 'evaluation')">
            <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
              <path d="m8 5 11 7-11 7z" />
            </svg>
            {{ t('gui.ai.overview.action_run_eval') }}
          </button>
        </div>
        <div class="panel-body">
          <div v-if="hasSectionError('evaluation')" class="panel-error">
            <p>{{ t('gui.ai.overview.section_error') }}</p>
          </div>
          <div v-else-if="!evaluations.length" class="panel-empty">
            <p>{{ t('gui.ai.overview.evaluation_empty') }}</p>
          </div>
          <div
            v-for="item in evaluations"
            :key="item.evaluation_id"
            class="eval-row clickable"
            @click="emit('navigate', 'evaluation')"
          >
            <div class="avatar-round" :class="getAvatarClass(item.provider_name || item.provider_id, '')">
              {{ getInitials(item.provider_name || item.provider_id) }}
            </div>
            <div class="eval-info">
              <div class="main-title">{{ item.provider_name || item.provider_id }}</div>
              <div class="eval-date">{{ formatEvalDate(item.created_at) }}</div>
            </div>
            <div class="eval-verdict">
              <Badge
                :tone="item.verdict === 'suitable' ? 'success' : (item.verdict === 'unsuitable' ? 'danger' : 'neutral')"
                size="sm"
              >
                {{ item.verdict ? t(`gui.ai.evaluation.verdict_${item.verdict}`) : item.lifecycle }}
              </Badge>
            </div>
            <div class="chev">›</div>
          </div>
        </div>
      </section>

      <!-- Panel 3: AI Groups -->
      <section class="panel">
        <div class="panel-head">
          <div class="grow">
            <div class="panel-title">{{ t('gui.ai.overview.groups_title') }}</div>
            <div class="panel-sub">
              {{ groupsSummaryText }}
            </div>
          </div>
          <button class="panel-action" type="button" @click="emit('navigate', 'groups')">
            ＋ {{ t('gui.ai.overview.action_new_group') }}
          </button>
        </div>
        <div class="panel-body team-list">
          <div v-if="hasSectionError('ai_groups')" class="panel-error">
            <p>{{ t('gui.ai.overview.section_error') }}</p>
          </div>
          <div v-else-if="!groups.length" class="panel-empty">
            <p>{{ t('gui.ai.overview.groups_empty') }}</p>
          </div>
          <article
            v-for="(group, idx) in groups"
            :key="group.ai_group_id"
            class="team-card clickable"
            @click="emit('navigate', 'groups')"
          >
            <div class="team-top">
              <div class="team-name">
                <span class="star">{{ getGroupIcon(idx) }}</span>
                {{ group.name }}
              </div>
            </div>
            <div class="info-grid">
              <div class="info-label">Supervisor</div>
              <div class="info-value">
                <span v-if="group.work_executor" class="member-dot">
                  <span class="mini" :class="getAvatarClass(group.work_executor.provider_name || '', '')">
                    {{ getInitials(group.work_executor.provider_name || 'SE') }}
                  </span>
                  {{ group.work_executor.provider_name || group.work_executor.provider_id }}
                  <span v-if="!group.work_executor.is_available" class="warn-chip">!</span>
                </span>
                <span v-else class="text-muted">{{ t('gui.ai.common.none') }}</span>
              </div>

              <div class="info-label">Members</div>
              <div class="info-value member-badges">
                <span v-if="group.reviewer" class="member-dot">
                  <span class="mini" :class="getAvatarClass(group.reviewer.provider_name || '', '')">
                    {{ getInitials(group.reviewer.provider_name || 'RV') }}
                  </span>
                  {{ group.reviewer.provider_name || group.reviewer.provider_id }}
                  <span v-if="!group.reviewer.is_available" class="warn-chip">!</span>
                </span>
                <span v-if="group.reworker" class="member-dot">
                  <span class="mini" :class="getAvatarClass(group.reworker.provider_name || '', '')">
                    {{ getInitials(group.reworker.provider_name || 'RW') }}
                  </span>
                  {{ group.reworker.provider_name || group.reworker.provider_id }}
                  <span v-if="!group.reworker.is_available" class="warn-chip">!</span>
                </span>
                <span v-if="!group.reviewer && !group.reworker" class="text-muted">{{ t('gui.ai.common.none') }}</span>
              </div>

              <div class="info-label">{{ t('gui.ai.groups.roles') }}</div>
              <div class="info-value role-tags">
                <span class="role indigo">{{ t('gui.ai.groups.role_executor') }}</span>
                <span v-if="group.reviewer" class="role cyan">{{ t('gui.ai.groups.role_reviewer') }}</span>
                <span v-if="group.reworker" class="role purple">{{ t('gui.ai.groups.role_reworker') }}</span>
              </div>
            </div>
            <div class="bottom-arrow">›</div>
          </article>
        </div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import Badge from '@/components/ui/Badge.vue'
import { useI18n } from 'vue-i18n'
import type {
  AiEvaluation,
  AiGroup,
  AiOverviewData,
  ProviderItem,
} from '../types'

const props = withDefaults(
  defineProps<{
    overview: AiOverviewData | null
    providers: ProviderItem[]
    evaluations: AiEvaluation[]
    groups: AiGroup[]
    sectionErrors?: Record<string, string | null>
  }>(),
  {
    sectionErrors: () => ({}),
  }
)

const emit = defineEmits<{
  navigate: [tab: 'providers' | 'evaluation' | 'groups']
}>()

const { t } = useI18n()

function hasSectionError(section: 'providers' | 'evaluation' | 'ai_groups'): boolean {
  if (props.overview?.errors?.some((e) => e.section === section)) {
    return true
  }
  if (props.sectionErrors) {
    if (section === 'providers') {
      return Boolean(props.sectionErrors.providers || props.sectionErrors.settings)
    }
    if (section === 'evaluation') {
      return Boolean(props.sectionErrors.evaluation || props.sectionErrors.evaluations)
    }
    if (section === 'ai_groups') {
      return Boolean(props.sectionErrors.ai_groups || props.sectionErrors.groups)
    }
  }
  return false
}

const providersSummaryText = computed(() => {
  if (props.overview?.providers) {
    const { total, enabled, mode } = props.overview.providers
    const modeLabel = mode === 'inherit' ? t('gui.ai.providers.mode_inherit') : mode === 'disabled' ? t('gui.ai.providers.mode_disabled') : t('gui.ai.providers.mode_custom')
    return `${t('gui.ai.overview.providers_summary', { count: total, enabled })} (${modeLabel})`
  }
  return t('gui.ai.overview.providers_desc')
})

const evaluationSummaryText = computed(() => {
  if (props.overview?.evaluation) {
    const { providers_with_current_result, running, failed } = props.overview.evaluation
    return `${t('gui.ai.overview.eval_summary', { evaluated: providers_with_current_result, running, failed })}`
  }
  return t('gui.ai.overview.evaluation_desc')
})

const groupsSummaryText = computed(() => {
  if (props.overview?.ai_groups) {
    const { total, invalid } = props.overview.ai_groups
    return `${t('gui.ai.overview.groups_summary', { total, invalid })}`
  }
  return t('gui.ai.overview.groups_desc')
})

function getInitials(name: string): string {
  if (!name) return 'AI'
  const parts = name.trim().split(/\s+/)
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase()
  }
  return name.slice(0, 2).toUpperCase()
}

function getAvatarClass(name: string, kind?: string): string {
  const n = (name + ' ' + (kind || '')).toLowerCase()
  if (n.includes('sonnet') || n.includes('claude')) return 'av-cs'
  if (n.includes('opus')) return 'av-co'
  if (n.includes('haiku')) return 'av-ch'
  if (n.includes('gpt') || n.includes('openai')) return 'av-og'
  if (n.includes('gemini')) return 'av-gp'
  if (n.includes('llama')) return 'av-l3'
  if (n.includes('terra')) return 'av-te'
  if (n.includes('luna')) return 'av-lu'
  return 'av-cs'
}

function getGroupIcon(index: number): string {
  const icons = ['★', '🗂', '🩵', '⦿']
  return icons[index % icons.length]
}

function formatEvalDate(isoStr?: string): string {
  if (!isoStr) return ''
  try {
    const d = new Date(isoStr)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return isoStr
  }
}
</script>

<style scoped>
.ai-overview-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.page-title {
  font-size: 21px;
  font-weight: 850;
  margin: 0 0 6px;
  color: var(--fg2-text, #edf3fa);
}

.page-sub {
  color: #b9c8d8;
  font-size: 15px;
  margin-bottom: 20px;
}

.step-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 36px;
  margin-bottom: 22px;
  align-items: stretch;
}

.step-wrap {
  position: relative;
  min-width: 0;
  display: block;
}

.step-wrap:not(:last-child)::after {
  content: "→";
  position: absolute;
  right: -28px;
  top: 50%;
  transform: translateY(-50%);
  color: #95aac1;
  font-size: 32px;
  line-height: 1;
  z-index: 4;
  pointer-events: none;
}

.step-card {
  position: relative;
  width: 100%;
  min-width: 0;
  min-height: 160px;
  padding: 18px 20px;
  border-radius: 12px;
  border: 1px solid var(--fg2-line, rgba(154, 181, 210, 0.16));
  overflow: hidden;
  background: linear-gradient(145deg, rgba(17, 31, 47, 0.92), rgba(11, 23, 36, 0.95));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.025);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.step-card::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.94;
}

.step-card.blue::before {
  background: radial-gradient(580px 220px at 12% 0%, rgba(28, 101, 197, 0.34), transparent 56%);
}

.step-card.cyan::before {
  background: radial-gradient(580px 220px at 12% 0%, rgba(18, 151, 145, 0.29), transparent 56%);
}

.step-card.purple::before {
  background: radial-gradient(580px 220px at 12% 0%, rgba(103, 67, 189, 0.31), transparent 56%);
}

.step-card > * {
  position: relative;
  z-index: 1;
}

.step-head {
  display: flex;
  align-items: center;
  gap: 14px;
}

.step-icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.12);
}

.step-card.blue .step-icon { background: #24589b; color: #9eceff; }
.step-card.cyan .step-icon { background: #176766; color: #96f2e6; }
.step-card.purple .step-icon { background: #5c44a4; color: #d1bcff; }

.step-icon svg {
  width: 20px;
  height: 20px;
  stroke: currentColor;
}

.step-title {
  font-size: 17px;
  font-weight: 850;
  color: #fff;
}

.step-title .num {
  font-size: 20px;
  margin-right: 8px;
}

.step-desc {
  margin: 14px 0 14px 52px;
  color: #d7e4f1;
  font-size: 14px;
  line-height: 1.5;
  white-space: pre-line;
}

.step-action {
  margin-left: 52px;
  width: 154px;
  height: 36px;
  border-radius: 8px;
  border: 1px solid rgba(120, 177, 233, 0.32);
  background: rgba(9, 21, 34, 0.32);
  color: #dce9f7;
  font-size: 13.5px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  cursor: pointer;
  transition: 0.16s ease;
}

.step-action:hover {
  filter: brightness(1.15);
  border-color: rgba(111, 183, 255, 0.6);
}

.step-card.cyan .step-action { border-color: rgba(73, 223, 208, 0.26); }
.step-card.purple .step-action { border-color: rgba(163, 123, 255, 0.24); }

.lower {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  flex: 1;
  min-height: 540px;
}

.panel {
  border: 1px solid var(--fg2-line, rgba(154, 181, 210, 0.16));
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(13, 26, 40, 0.92), rgba(10, 22, 35, 0.9));
  overflow: hidden;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 16px 18px 14px;
  border-bottom: 1px solid rgba(154, 181, 210, 0.1);
  flex: 0 0 auto;
}

.panel-head .grow {
  flex: 1;
  min-width: 0;
}

.panel-title {
  font-size: 16px;
  font-weight: 850;
  margin-bottom: 4px;
  color: #eaf1f8;
}

.panel-sub {
  color: #93a7bc;
  font-size: 12.5px;
  line-height: 1.35;
}

.panel-action {
  height: 34px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid rgba(90, 159, 233, 0.32);
  background: linear-gradient(180deg, #163d68, #112f53);
  color: #daebfd;
  font-size: 12.5px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  transition: 0.16s ease;
}

.panel-action:hover {
  background: linear-gradient(180deg, #1f4f84, #153b65);
}

.panel-body {
  padding: 0 10px 8px;
  overflow-y: auto;
  flex: 1 1 auto;
  min-height: 0;
}

.panel-error,
.panel-empty {
  padding: 32px 16px;
  text-align: center;
  color: #8da2b6;
  font-size: 13px;
}

.panel-error {
  color: var(--fg2-status-danger-fg, #ff8585);
}

.provider-row,
.eval-row {
  display: grid;
  align-items: center;
  border-bottom: 1px solid rgba(154, 181, 210, 0.08);
  transition: background 0.12s ease;
}

.provider-row:last-child,
.eval-row:last-child {
  border-bottom: 0;
}

.clickable {
  cursor: pointer;
}

.clickable:hover {
  background: rgba(255, 255, 255, 0.025);
}

.provider-row {
  grid-template-columns: 40px minmax(0, 1fr) auto auto;
  gap: 12px;
  padding: 12px 8px;
}

.avatar-round {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 11.5px;
  color: #fff;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.06) inset;
}

.av-cs, .av-co, .av-ch { background: linear-gradient(145deg, #d98466, #8d3f31); }
.av-og, .av-o4, .av-lu { background: linear-gradient(145deg, #67a2ff, #3667d7); }
.av-gp { background: linear-gradient(145deg, #9b79ff, #6f49d7); }
.av-l3 { background: linear-gradient(145deg, #58adff, #2e77c6); }
.av-te { background: linear-gradient(145deg, #e6b089, #b97851); }

.main-title {
  font-size: 14.5px;
  font-weight: 750;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #edf3fa;
}

.subrow {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 3px;
  color: #7f95ac;
  font-size: 11.5px;
}

.badge {
  height: 20px;
  padding: 0 7px;
  border-radius: 5px;
  background: #11243a;
  border: 1px solid rgba(111, 141, 176, 0.18);
  display: inline-flex;
  align-items: center;
  font-size: 10.5px;
  color: #d7e6f5;
  font-weight: 600;
}

.status-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #78dcb0;
  font-size: 12px;
  padding-right: 4px;
}

.status-chip.off {
  color: #94a8bd;
}

.status-chip i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 15%, transparent);
}

.chev {
  color: #88a0b8;
  font-size: 24px;
  line-height: 1;
}

.eval-row {
  grid-template-columns: 40px minmax(0, 1fr) 52px 100px 16px;
  gap: 10px;
  padding: 12px 8px;
}

.eval-date {
  color: #91a4b8;
  font-size: 11.5px;
  margin-top: 4px;
}

.score {
  font-family: var(--fg2-font-mono, monospace);
  font-weight: 800;
  font-size: 18px;
  color: #8be2ff;
  text-align: right;
}

.score--unsuitable {
  color: #ff9d9d;
}

.bars {
  display: grid;
  gap: 3px;
  padding-left: 2px;
}

.barline {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 6px;
  align-items: center;
  font-size: 9.5px;
  color: #90a4ba;
}

.track {
  height: 6px;
  border-radius: 999px;
  background: #172a42;
  overflow: hidden;
}

.fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #67b6ff, #54d1ff);
}

.fill.p2 { background: linear-gradient(90deg, #57d2c9, #5fe0b6); }
.fill.p3 { background: linear-gradient(90deg, #7ba8ff, #8c84ff); }

.team-list {
  display: grid;
  gap: 12px;
  padding: 10px;
  overflow-y: auto;
  align-content: start;
}

.team-card {
  border: 1px solid rgba(154, 181, 210, 0.14);
  border-radius: 10px;
  background: linear-gradient(180deg, rgba(14, 28, 43, 0.72), rgba(12, 24, 37, 0.75));
  padding: 12px 14px;
  position: relative;
}

.team-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.team-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 850;
  color: #edf3fa;
}

.team-name .star {
  color: #ffcf59;
  font-size: 16px;
}

.team-badge {
  height: 20px;
  padding: 0 8px;
  border-radius: 6px;
  background: #10352e;
  border: 1px solid rgba(73, 223, 208, 0.18);
  color: #86efd9;
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  margin-left: 6px;
}

.info-grid {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  row-gap: 8px;
  column-gap: 8px;
  margin-top: 6px;
  font-size: 12.5px;
}

.info-label {
  color: #b8c7d8;
}

.info-value {
  min-width: 0;
  color: #e8f1fb;
}

.member-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 10px;
}

.member-dot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.mini {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 9px;
  color: #fff;
}

.warn-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  background: #f59e0b;
  color: #111;
  font-size: 10px;
  font-weight: 900;
}

.role-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.role {
  height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(112, 151, 193, 0.2);
  background: #11263c;
  color: #dce9f8;
  font-size: 11.5px;
  display: inline-flex;
  align-items: center;
}

.role.cyan {
  background: #102b2d;
  border-color: rgba(73, 223, 208, 0.2);
  color: #95eee5;
}

.role.purple {
  background: #241a3d;
  border-color: rgba(163, 123, 255, 0.22);
  color: #d0bdfd;
}

.role.indigo {
  background: #132744;
  border-color: rgba(91, 146, 255, 0.22);
  color: #b4d1ff;
}

.bottom-arrow {
  text-align: right;
  color: #8ea4b9;
  font-size: 22px;
  line-height: 1;
  padding-top: 4px;
}

.text-muted {
  color: #7f95ac;
}

@media (max-width: 1400px) {
  .step-grid {
    grid-template-columns: 1fr;
  }
  .step-wrap::after {
    display: none;
  }
  .lower {
    grid-template-columns: 1fr;
  }
}
</style>