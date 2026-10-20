<template>
  <div class="settings-hub">
    <div class="fgs-layout">
      <SettingsNav :current="null" />
      <main class="fgs-content">
        <section class="fgs-panel" data-panel="hub">
          <div class="fgs-phead">
            <span class="fgs-tico"><SettingsIcon name="gear" size="lg" /></span>
            <div class="fgs-ptitle">
              <h2>{{ t('gui.settings.title') }}</h2>
              <p>{{ t('gui.settings.hub.sub') }}</p>
            </div>
          </div>

          <div class="fgs-panel-body">
            <Alert
              v-if="notice === 'admin_only'"
              class="hub-notice"
              tone="warning"
              dismissible
              :dismiss-label="t('gui.settings.notice_close')"
              data-testid="settings-access-notice"
              @dismiss="clearNotice"
            >
              {{ t('gui.settings.access_denied') }}
            </Alert>

            <!-- 확인 필요 (시스템 관리자): GET /system/agents 한 번, 상태를 고치러 시스템 Agent 로 간다 -->
            <div v-if="showAttention" class="fgs-sec" data-testid="hub-attention">
              <div class="fgs-sec-head">
                <h3>{{ t('gui.settings.hub.attention') }} <span v-if="attention.length" class="count">{{ t('gui.settings.hub.attention_count', { n: attention.length }) }}</span></h3>
              </div>
              <ErrorState v-if="attentionError" compact :title="t('gui.settings.hub.attention_failed')" :message="attentionError" data-testid="hub-attention-error">
                <template #action>
                  <Button variant="secondary" @click="loadAttention">{{ t('gui.common.retry') }}</Button>
                </template>
              </ErrorState>
              <LoadingState v-else-if="!agentList" inline size="sm" :label="t('gui.common.loading')" />
              <div v-else-if="attention.length" class="attn">
                <div v-for="item in attention" :key="item.key" class="attn-item" :data-attention="item.id">
                  <div class="attn-ic" :class="item.tone"><SettingsIcon :name="item.icon" size="sm" /></div>
                  <div class="grow">
                    <strong>{{ item.title }}</strong>
                    <small>{{ item.sub }}</small>
                  </div>
                  <button type="button" class="fgs-btn sm" data-testid="hub-attention-go" @click="openAgent(item.id)">{{ t('gui.settings.hub.attention_go') }}</button>
                </div>
              </div>
              <div v-else class="fgs-rows">
                <div class="fgs-empty" data-testid="hub-attention-empty">
                  <strong>{{ t('gui.settings.hub.attention_empty') }}</strong>{{ t('gui.settings.hub.attention_empty_hint') }}
                </div>
              </div>
            </div>

            <div v-if="cards.length" class="fgs-sec">
              <div class="fgs-sec-head"><h3>{{ t('gui.settings.hub.section') }}</h3></div>
              <div class="hub-grid">
                <article v-for="card in cards" :key="card.key" class="hub-card" :data-scope-card="card.key">
                  <div class="top">
                    <div class="ci" :class="`t-${card.key}`"><SettingsIcon :name="card.icon" size="lg" /></div>
                    <div>
                      <h3>{{ card.title }}</h3>
                      <div class="s">{{ card.sub }}</div>
                    </div>
                  </div>
                  <div class="links">
                    <span v-for="tag in card.tags" :key="tag" class="tag">{{ tag }}</span>
                  </div>
                  <RouterLink :to="card.to" class="foot">{{ t('gui.settings.hub.open') }} <SettingsIcon name="chev" size="sm" /></RouterLink>
                </article>
              </div>
            </div>
            <EmptyState v-else :title="t('gui.settings.hub.empty')" data-testid="settings-hub-empty" />
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
// 환경 설정 개요 (mockup ⑩ hub, flowgate.v02.0077; cards of 0076 T#1 D §1.1): the settings
// navigation and one card per settings scope — 내 설정 / 프로젝트 / 시스템 — built from the shared
// settings menu (../settingsMenu). A card shows only when at least one of its screens is registered
// and allowed: 프로젝트 and 시스템 for a system administrator once /auth/me has answered. Its tags
// name exactly those screens and the card opens the first one; there are no "준비 중" or disabled
// cards, and Workspace is gone. A refused settings URL lands here with a notice.
// 확인 필요 (mockup ⑩): for a system administrator only, the overview reads GET /system/agents once
// and lists the Agents that need a look (waiting for enrollment, refused, offline, a capability
// allowed but not reported); 확인 opens that Agent in 시스템 Agent. Anyone else's overview sends
// nothing. An answer that arrives after leaving, or after the administrator role went, is dropped.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Alert from '@/components/ui/Alert.vue'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsNav from '../components/SettingsNav.vue'
import { useSettingsAccessNotice } from '../access'
import { AGENT_CAPS, AGENT_STATE_TONE, agentState, agentStateSub, capMissing, needsAttention } from '../agentState'
import { listSystemAgents } from '../api'
import type { SettingsIconName } from '../icons'
import { useSettingsMenu, type SettingsScopeKey } from '../settingsMenu'
import type { SystemAgent } from '../types'
import '../settings.css'

const { t, locale } = useI18n()
const router = useRouter()
const menu = useSettingsMenu()
const { notice, clearNotice } = useSettingsAccessNotice()

// ── 확인 필요: 시스템 Agent 화면을 열 수 있는 사람(시스템 관리자)에게만 ──
const showAttention = computed(() => menu.value.some((page) => page.key === 'agents'))
const agentList = ref<SystemAgent[] | null>(null)
const attentionError = ref('')
let alive = true

async function loadAttention() {
  attentionError.value = ''
  try {
    const list = await listSystemAgents()
    if (!alive || !showAttention.value) return
    agentList.value = Array.isArray(list) ? list : []
  } catch (err) {
    if (!alive || !showAttention.value) return
    attentionError.value = err instanceof Error ? err.message : String(err)
  }
}

interface AttentionItem {
  key: string
  id: string
  tone: 'warn' | 'bad' | 'info'
  icon: SettingsIconName
  title: string
  sub: string
}

const attention = computed<AttentionItem[]>(() => {
  if (!showAttention.value) return []
  const where = t('gui.settings.hub.attention_where')
  return (agentList.value ?? []).filter(needsAttention).flatMap((a) => {
    const items: AttentionItem[] = []
    const state = agentState(a)
    const tone = AGENT_STATE_TONE[state]
    const title = `${a.name} ${t(`gui.settings.agents.state.${state}`)}`
    const sub = `${where} · ${agentStateSub(a, t, locale.value)}`
    if (state === 'pending' || state === 'reenroll') items.push({ key: `${a.agent_id}:state`, id: a.agent_id, tone: 'warn', icon: 'key', title, sub })
    else if (tone === 'bad') items.push({ key: `${a.agent_id}:state`, id: a.agent_id, tone: 'bad', icon: 'alert', title, sub })
    else if (state === 'offline') items.push({ key: `${a.agent_id}:state`, id: a.agent_id, tone: 'warn', icon: 'power', title, sub })
    const missing = AGENT_CAPS.filter((cap) => capMissing(a, cap)).map((cap) => t(`gui.settings.agents.caps.${cap}`))
    if (missing.length && tone !== 'bad') {
      items.push({
        key: `${a.agent_id}:caps`,
        id: a.agent_id,
        tone: 'info',
        icon: 'info',
        title: t('gui.settings.hub.attention_mismatch', { name: a.name }),
        sub: `${where} · ${t('gui.settings.hub.attention_mismatch_sub', { caps: missing.join(', ') })}`,
      })
    }
    return items
  })
})

function openAgent(id: string) {
  void router.push({ path: '/settings/system/agents', query: { agent: id } })
}

onMounted(() => {
  if (showAttention.value) void loadAttention()
})

// The notice belongs to the move that was refused; leaving the overview drops it.
onBeforeUnmount(() => {
  alive = false
  clearNotice()
})

interface ScopeCard {
  key: SettingsScopeKey
  icon: SettingsIconName
  title: string
  sub: string
  tags: string[]
  to: string
}

const SCOPES: { key: SettingsScopeKey; icon: SettingsIconName }[] = [
  { key: 'me', icon: 'user' },
  { key: 'project', icon: 'folder' },
  { key: 'system', icon: 'server' },
]

const cards = computed<ScopeCard[]>(() =>
  SCOPES.flatMap((scope) => {
    const pages = menu.value.filter((page) => page.scope === scope.key)
    if (!pages.length) return []
    return [{
      key: scope.key,
      icon: scope.icon,
      title: t(`gui.settings.scope.${scope.key}`),
      sub: t(`gui.settings.scope_sub.${scope.key}`),
      tags: pages.map((page) => t(page.labelKey)),
      to: pages[0].path,
    }]
  }),
)
</script>

<style scoped>
.settings-hub {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 22px 28px 48px;
}

.fgs-content {
  min-width: 0;
}

.hub-notice {
  margin-bottom: 18px;
}

.fgs-sec-head .count {
  font-size: 12px;
  font-weight: 500;
  color: #70869f;
}

.attn {
  border: 1px solid #263d57;
  border-radius: 12px;
  overflow: hidden;
}

.attn-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #22384f;
  background: #0d2035;
}

.attn-item:last-child {
  border-bottom: 0;
}

.attn-item .grow {
  flex: 1;
  min-width: 0;
}

.attn-item strong {
  font-size: 13px;
  display: block;
}

.attn-item small {
  font-size: 12px;
  color: #94a8c0;
}

.attn-ic {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  flex: none;
}

.attn-ic.warn { background: #382e1f; color: #f2be55; }
.attn-ic.bad { background: #341e2a; color: #fb7a80; }
.attn-ic.info { background: #132c44; color: #8cbdff; }

.hub-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 14px;
}

.hub-card {
  position: relative;
  padding: 20px 22px;
  border: 1px solid #263d57;
  border-radius: 15px;
  background: linear-gradient(138deg, #0d1f36 0%, #0a192b 95%);
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color 0.13s, background 0.13s;
}

.hub-card:hover {
  border-color: #54789c;
  background: linear-gradient(138deg, #132b47 0%, #0b1b2e 95%);
}

.hub-card .top {
  display: flex;
  align-items: center;
  gap: 13px;
}

.hub-card .ci {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  border: 1px solid;
  flex: none;
}

.t-me { color: #54a2ff; border-color: #296fb0; background: #112f50; }
.t-project { color: #ad85ff; border-color: #6650a9; background: #282047; }
.t-system { color: #9cbbdd; border-color: #526e8a; background: #1a2b41; }

.hub-card h3 {
  font-size: 17px;
  margin: 0 0 3px;
  letter-spacing: -0.4px;
}

.hub-card .s {
  font-size: 12px;
  color: #94a8c0;
}

.hub-card .links {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.hub-card .tag {
  border: 1px solid #314762;
  background: #0c2136;
  padding: 3px 9px;
  border-radius: 99px;
  font-size: 11.5px;
  color: #b1c3d8;
}

/* The whole card opens its first screen; the link is the one control. */
.hub-card .foot {
  font-size: 12px;
  color: #8fb3d8;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.hub-card .foot::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 15px;
}
</style>
