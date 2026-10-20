<template>
  <SettingsShell :crumbs="crumbs" scope="system">
    <template #nav>
      <SettingsSubnav :title="t('gui.settings.scope.system')" :items="systemNav" active="users" />
    </template>

    <section class="fgs-panel" data-panel="users">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="users" size="lg" /></span>
        <h2>{{ t('gui.settings.users.title') }}</h2>
        <div class="fgs-push">
          <button type="button" class="fgs-iconbtn" :title="t('gui.settings.csv')" :aria-label="t('gui.settings.csv')" @click="exportUsers">
            <SettingsIcon name="download" />
          </button>
          <button type="button" class="fgs-btn pri" data-testid="user-create" @click="openCreate">
            <SettingsIcon name="plus" />{{ t('gui.settings.users.create') }}
          </button>
        </div>
      </div>

      <div class="fgs-panel-body">
        <LoadingState v-if="loading && !data" :label="t('gui.common.loading')" />
        <ErrorState v-else-if="error && !data" :title="t('gui.settings.errors.load_failed')" :message="error">
          <template #action>
            <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
          </template>
        </ErrorState>

        <template v-else-if="data">
          <!-- A failed re-read with a list on screen: the list is the last one read, not the current state. -->
          <div v-if="error" class="fgs-refresh-error" data-testid="users-refresh-error">
            <ErrorState compact :title="t('gui.settings.users.refresh_failed')" :message="error">
              <template #action>
                <Button variant="secondary" data-testid="users-refresh-retry" :disabled="loading" @click="load">{{ t('gui.common.retry') }}</Button>
              </template>
            </ErrorState>
          </div>
          <div class="fgs-glance">
            <div class="fgs-tile">
              <span class="fgs-tico"><SettingsIcon name="users" /></span>
              <div class="tbody">
                <div class="big" data-testid="users-total">{{ data.summary.total }}</div>
                <div class="fgs-sbar">
                  <i class="fgs-c-ok" :style="{ width: pct(data.summary.active) }" />
                  <i class="fgs-c-warn" :style="{ width: pct(data.summary.invited) }" />
                  <i class="fgs-c-off" :style="{ width: pct(data.summary.disabled) }" />
                </div>
                <div class="fgs-legend">
                  <span><span class="fgs-dot fgs-c-ok" />{{ t('gui.settings.state.active') }} {{ data.summary.active }}</span>
                  <span><span class="fgs-dot fgs-c-warn" />{{ t('gui.settings.state.invited_short') }} {{ data.summary.invited }}</span>
                  <span><span class="fgs-dot fgs-c-off" />{{ t('gui.settings.state.disabled') }} {{ data.summary.disabled }}</span>
                </div>
              </div>
            </div>
            <button type="button" class="fgs-tile" :class="{ on: adminOnly }" data-testid="tile-admin" @click="adminOnly = !adminOnly">
              <span class="fgs-tico"><SettingsIcon name="star" /></span>
              <div class="tbody">
                <div class="big">{{ data.summary.admins }}</div>
                <div class="lbl">{{ t('gui.settings.users.system_admin') }}</div>
              </div>
              <span class="avstack">
                <span v-for="admin in admins.slice(0, 3)" :key="admin.user_id" class="fgs-av sm">{{ initials(admin.username) }}</span>
              </span>
            </button>
            <button type="button" class="fgs-tile warn" :class="{ on: tfaMissingOnly }" data-testid="tile-2fa" @click="tfaMissingOnly = !tfaMissingOnly">
              <div class="ring" :style="{ '--p': tfaPct }"><span>{{ data.summary.tfa_enabled }}/{{ data.summary.tfa_enabled + data.summary.tfa_missing }}</span></div>
              <div class="tbody">
                <div class="big">{{ data.summary.tfa_missing }}</div>
                <div class="lbl"><SettingsIcon name="shieldno" size="sm" />{{ t('gui.settings.users.tfa_missing') }}</div>
              </div>
            </button>
          </div>

          <div class="fgs-bar">
            <label class="fgs-search">
              <SettingsIcon name="search" class="fgs-search-icon" />
              <input v-model="query" class="fgs-input" :placeholder="t('gui.settings.search_people')" data-testid="user-search" />
            </label>
            <div class="fgs-chips">
              <button
                v-for="chip in stateChips"
                :key="chip.value"
                type="button"
                class="fgs-chip"
                :class="{ on: stateFilter === chip.value }"
                :data-state-filter="chip.value || 'all'"
                @click="stateFilter = chip.value"
              >
                <span v-if="chip.dot" class="fgs-dot" :class="chip.dot" />{{ chip.label }}
              </button>
            </div>
            <span class="fgs-vsep" />
            <button type="button" class="fgs-chip" :class="{ on: adminOnly }" @click="adminOnly = !adminOnly"><SettingsIcon name="star" size="sm" />{{ t('gui.settings.users.admin') }}</button>
            <button type="button" class="fgs-chip warn" :class="{ on: tfaMissingOnly }" data-testid="chip-2fa" @click="tfaMissingOnly = !tfaMissingOnly"><SettingsIcon name="shieldno" size="sm" />{{ t('gui.settings.users.tfa_missing_short') }}</button>
          </div>

          <div class="fgs-dtable" style="--cols: minmax(240px, 1.7fr) 110px 90px 100px 110px 24px">
            <div class="fgs-dtr head">
              <div>{{ t('gui.settings.users.col_user') }}</div>
              <div>{{ t('gui.settings.users.col_permission') }}</div>
              <div>{{ t('gui.settings.detail.col_state') }}</div>
              <div>{{ t('gui.settings.users.col_tfa') }}</div>
              <div>{{ t('gui.settings.users.col_last_login') }}</div>
              <div />
            </div>
            <div
              v-for="row in filtered"
              :key="row.user_id"
              class="fgs-dtr row"
              :class="{ sel: selected?.user_id === row.user_id, dim: row.state === 'disabled' }"
              :data-user="row.user_id"
              @click="openDrawer(row)"
            >
              <div class="fgs-who">
                <div class="fgs-av">{{ initials(row.username) }}</div>
                <div><b>{{ row.username }}</b><span>{{ row.email }}</span></div>
              </div>
              <div>
                <span v-if="row.is_admin" class="adm"><SettingsIcon name="star" size="sm" />{{ t('gui.settings.users.admin') }}</span>
                <span v-else class="fgs-muted">—</span>
              </div>
              <div><span class="fgs-st"><span class="fgs-dot" :class="stateDot(row.state)" />{{ t(`gui.settings.state.${row.state}`) }}</span></div>
              <div>
                <span v-if="row.state === 'disabled'" class="tfa off"><SettingsIcon name="shield" /></span>
                <span v-else-if="row.tfa_enabled" class="tfa ok" :title="t('gui.settings.users.tfa_on')"><SettingsIcon name="shieldok" /></span>
                <span v-else class="tfa no" :title="t('gui.settings.users.tfa_off')"><SettingsIcon name="shieldno" />{{ t('gui.settings.users.tfa_off') }}</span>
              </div>
              <div class="fgs-muted">{{ relativeTime(row.last_login_at, locale) }}</div>
              <div class="fgs-chev"><SettingsIcon name="chev" /></div>
            </div>
            <div v-if="!filtered.length" class="fgs-empty">{{ t('gui.settings.no_match') }}</div>
          </div>
          <div class="fgs-tablefoot" data-testid="users-foot">{{ filtered.length }} / {{ data.summary.total }}</div>
        </template>
      </div>
    </section>

    <!-- 사용자 상세 드로어 -->
    <div class="drawerback" :class="{ on: !!selected }" @click="closeDrawer" />
    <aside class="drawer" :class="{ on: !!selected }" :aria-hidden="!selected" :aria-label="t('gui.settings.users.detail_aria')" data-testid="user-drawer">
      <template v-if="selected">
        <div class="dr-hd">
          <div class="fgs-av lg">{{ initials(selected.username) }}</div>
          <div class="dr-title">
            <h3>{{ selected.username }}</h3>
            <p>{{ selected.email }}</p>
            <span class="fgs-pill" :class="statePill(selected.state)"><span class="fgs-dot" :class="stateDot(selected.state)" />{{ t(`gui.settings.state.${selected.state}`) }}</span>
          </div>
          <button type="button" class="fgs-iconbtn" :title="t('gui.common.close')" :aria-label="t('gui.common.close')" @click="closeDrawer"><SettingsIcon name="x" /></button>
        </div>
        <div class="dr-bd">
          <div class="fgs-actlist">
            <div class="fgs-actrow">
              <span class="fgs-tico sm"><SettingsIcon name="star" /></span>
              <b>{{ t('gui.settings.users.system_admin') }}</b>
              <span class="fgs-spacer" />
              <span v-if="isLastAdmin(selected)" class="fgs-lockmark" :title="t('gui.settings.users.last_admin')"><SettingsIcon name="lock" size="sm" /></span>
              <label class="fgs-toggle">
                <input
                  type="checkbox"
                  :checked="selected.is_admin"
                  :disabled="busy || isLastAdmin(selected) || selected.state === 'disabled'"
                  :aria-label="t('gui.settings.users.system_admin')"
                  data-testid="drawer-admin"
                  @click.prevent="askAdmin(!selected.is_admin)"
                />
                <span class="fgs-track" />
              </label>
            </div>
            <div class="fgs-actrow">
              <span class="fgs-tico sm" :class="selected.tfa_enabled ? 'ok' : 'warn'"><SettingsIcon name="shield" /></span>
              <b>{{ t('gui.settings.users.col_tfa') }}</b>
              <span class="fgs-spacer" />
              <span v-if="selected.tfa_enabled" class="tfa ok"><SettingsIcon name="shieldok" />{{ t('gui.settings.users.tfa_on') }}</span>
              <span v-else class="tfa no"><SettingsIcon name="shieldno" />{{ t('gui.settings.users.tfa_off') }}</span>
              <button type="button" class="fgs-btn sm" :disabled="busy || !selected.tfa_enabled" data-testid="drawer-tfa-reset" @click="askTfaReset">
                <SettingsIcon name="reset" size="sm" />{{ t('gui.settings.users.reset') }}
              </button>
            </div>
            <div class="fgs-actrow">
              <span class="fgs-tico sm"><SettingsIcon name="clock" /></span>
              <b>{{ t('gui.settings.users.col_last_login') }}</b>
              <span class="fgs-spacer" />
              <span class="fgs-muted">{{ relativeTime(selected.last_login_at, locale) }}</span>
            </div>
            <div class="fgs-actrow">
              <span class="fgs-tico sm"><SettingsIcon name="user" /></span>
              <b>{{ t('gui.settings.users.created_at') }}</b>
              <span class="fgs-spacer" />
              <span class="fgs-muted">{{ shortDate(selected.created_at) }}</span>
            </div>
          </div>
        </div>
        <div class="dr-ft">
          <button v-if="selected.state === 'invited'" type="button" class="fgs-btn sm" :disabled="busy" data-testid="drawer-resend" @click="resend">
            <SettingsIcon name="mail" size="sm" />{{ t('gui.settings.users.resend_invite') }}
          </button>
          <button v-else type="button" class="fgs-btn sm" :disabled="busy || selected.state === 'disabled'" data-testid="drawer-password" @click="askPassword">
            <SettingsIcon name="pw" size="sm" />{{ t('gui.settings.users.reset_password') }}
          </button>
          <span class="fgs-spacer" />
          <button
            v-if="selected.state === 'disabled'"
            type="button"
            class="fgs-btn sm pri"
            :disabled="busy"
            data-testid="drawer-enable"
            @click="enable"
          ><SettingsIcon name="power" size="sm" />{{ t('gui.settings.users.enable') }}</button>
          <button
            v-else
            type="button"
            class="fgs-btn sm danger"
            :disabled="busy || isLastAdmin(selected)"
            :title="isLastAdmin(selected) ? t('gui.settings.users.last_admin') : undefined"
            data-testid="drawer-disable"
            @click="askDisable"
          ><SettingsIcon name="power" size="sm" />{{ selected.state === 'invited' ? t('gui.settings.users.cancel_invite') : t('gui.settings.users.disable') }}</button>
        </div>
      </template>
    </aside>

    <!-- 확인: 결과를 아이콘 칩으로 -->
    <ModalShell :open="!!pending" size="sm" :aria-label="pending?.title" @close="pending = null">
      <div v-if="pending" class="confirm">
        <div class="conf-ico" :class="pending.tone"><SettingsIcon :name="pending.icon" size="xl" /></div>
        <div class="conf-who">{{ pending.title }}</div>
        <div class="effects">
          <span v-for="effect in pending.effects" :key="effect.text" class="effect" :class="effect.tone"><SettingsIcon :name="effect.icon" size="sm" />{{ effect.text }}</span>
        </div>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" @click="pending = null">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn" :class="pending?.tone === 'danger' ? 'danger' : 'pri'" :disabled="busy" data-testid="confirm-ok" @click="runPending">{{ pending?.label }}</button>
        </div>
      </template>
    </ModalShell>

    <!-- 사용자 만들기 -->
    <ModalShell :open="createOpen" size="sm" :aria-label="t('gui.settings.users.create')" @close="createOpen = false">
      <template #header><h2 class="fgs-modal-title">{{ t('gui.settings.users.create') }}</h2></template>
      <div class="fgs-formfield">
        <label for="new-user-name">{{ t('gui.settings.users.name') }}</label>
        <input id="new-user-name" v-model="form.name" class="fgs-input" :placeholder="t('gui.settings.users.name_placeholder')" data-testid="new-user-name" />
      </div>
      <div class="fgs-formfield">
        <label for="new-user-email">{{ t('gui.settings.users.email') }}</label>
        <input id="new-user-email" v-model="form.email" type="email" class="fgs-input" placeholder="name@example.com" data-testid="new-user-email" />
      </div>
      <div class="fgs-formfield">
        <span class="label">{{ t('gui.settings.users.start') }}</span>
        <div class="fgs-chips">
          <button type="button" class="fgs-chip" :class="{ on: form.start === 'invite' }" data-start="invite" @click="form.start = 'invite'"><SettingsIcon name="mail" size="sm" />{{ t('gui.settings.users.start_invite') }}</button>
          <button type="button" class="fgs-chip" :class="{ on: form.start === 'password' }" data-start="password" @click="form.start = 'password'"><SettingsIcon name="pw" size="sm" />{{ t('gui.settings.users.start_password') }}</button>
        </div>
      </div>
      <div class="fgs-formfield">
        <span class="label">{{ t('gui.settings.users.col_permission') }}</span>
        <div class="fgs-chips">
          <button type="button" class="fgs-chip" :class="{ on: !form.is_admin }" data-new-role="user" @click="form.is_admin = false"><SettingsIcon name="user" size="sm" />{{ t('gui.settings.users.regular') }}</button>
          <button type="button" class="fgs-chip" :class="{ on: form.is_admin }" data-new-role="admin" @click="form.is_admin = true"><SettingsIcon name="star" size="sm" />{{ t('gui.settings.users.system_admin') }}</button>
        </div>
        <span v-if="form.is_admin" class="fgs-hint"><SettingsIcon name="shield" size="sm" />{{ t('gui.settings.users.admin_tfa_hint') }}</span>
      </div>
      <p v-if="formError" class="fgs-error" role="alert">{{ formError }}</p>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" @click="createOpen = false">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="busy || !form.name.trim() || !form.email.trim()" data-testid="user-create-confirm" @click="submitCreate">
            {{ t('gui.settings.users.create_confirm') }}
          </button>
        </div>
      </template>
    </ModalShell>

    <!-- 전달할 자격 (초대 링크 / 임시 비밀번호) -->
    <ModalShell :open="!!credential" size="sm" :aria-label="t('gui.settings.users.credential_title')" @close="credential = null">
      <template #header><h2 class="fgs-modal-title">{{ t('gui.settings.users.credential_title') }}</h2></template>
      <template v-if="credential">
        <p class="fgs-muted">{{ credential.invite_path ? t('gui.settings.users.invite_hint', { name: credential.user.username }) : t('gui.settings.users.password_hint', { name: credential.user.username }) }}</p>
        <div ref="credentialValueRef" class="fgs-secret" data-testid="credential-value">{{ credentialValue }}</div>
        <button type="button" class="fgs-link" style="margin-top: 10px" @click="copyCredential"><SettingsIcon name="check" size="sm" />{{ t('gui.settings.users.copy') }}</button>
      </template>
      <template #footer>
        <div class="fgs-modal-actions"><button type="button" class="fgs-btn pri" @click="credential = null">{{ t('gui.common.close') }}</button></div>
      </template>
    </ModalShell>

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </SettingsShell>
</template>

<script setup lang="ts">
// 시안 13 시스템 › 사용자: every system user with state, 2FA and last sign-in; filters and summary
// tiles; a detail drawer for system-admin on/off (the last administrator cannot lose it), 2FA
// reset, password reset / new invitation, disable / cancel invitation / enable; and user creation
// by invitation link or temporary password. The server has no mail transport, so the one-time link
// or password is shown here for the administrator to hand over.
//
// flowgate.v02.0076 T#3 (D §2.7): only the existing /system/users routes. Workspace is gone from
// this screen — no tile, filter, column, drawer chips or create-time picker — and the create body
// carries no workspace_id (not even an empty one). The server still reports memberships and keeps
// them; nothing here removes one. The disabled "활동 기록 · 준비 중" row is gone as well. The
// last-administrator and self-disable guards stay the server's (409 last_admin /
// cannot_disable_self); the drawer's lock is only a hint.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { copyToClipboard } from '@/core/interaction/clipboard'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useSettingsToasts } from '../components/useSettingsToasts'
import { useSystemNav } from '../components/systemNav'
import type { SettingsIconName } from '../icons'
import {
  createSystemUser,
  disableSystemUser,
  enableSystemUser,
  listSystemUsers,
  resendInvite,
  resetPassword,
  resetTfa,
  setSystemAdmin,
} from '../api'
import { downloadCsv, initials, relativeTime, shortDate, toCsv } from '../format'
import type { CreateUserInput, SystemUser, SystemUsersResponse, UserCredentialResult, UserState } from '../types'

const { t, locale } = useI18n()
const route = useRoute()
const systemNav = useSystemNav()
const { toasts, pushToast, dismissToast } = useSettingsToasts()

const crumbs = computed(() => [
  { label: t('gui.settings.title'), to: '/settings' },
  { label: t('gui.settings.scope.system') },
  { label: t('gui.settings.users.title') },
])

const data = ref<SystemUsersResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const busy = ref(false)

/**
 * GET /system/users. False when it failed: the error is kept (shown above a list already on screen,
 * or instead of the list on first load) and the list on screen is the last one read. Retrying
 * repeats only this GET, never a change.
 */
async function load(): Promise<boolean> {
  loading.value = true
  error.value = null
  try {
    data.value = await listSystemUsers()
    if (selected.value) selected.value = data.value.users.find((u) => u.user_id === selected.value?.user_id) ?? null
    return true
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    return false
  } finally {
    loading.value = false
  }
}

// ── filters ──
const query = ref('')
const stateFilter = ref<'' | UserState>('')
const adminOnly = ref(false)
const tfaMissingOnly = ref(false)

const stateChips = computed(() => [
  { value: '' as const, label: t('gui.settings.filter_all'), dot: '' },
  { value: 'active' as const, label: t('gui.settings.state.active'), dot: 'fgs-c-ok' },
  { value: 'invited' as const, label: t('gui.settings.state.invited_short'), dot: 'fgs-c-warn' },
  { value: 'disabled' as const, label: t('gui.settings.state.disabled'), dot: 'fgs-c-off' },
])

const users = computed(() => data.value?.users ?? [])
const admins = computed(() => users.value.filter((u) => u.is_admin && u.state !== 'disabled'))

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return users.value.filter((u) =>
    (!q || `${u.username} ${u.email ?? ''}`.toLowerCase().includes(q))
    && (!stateFilter.value || u.state === stateFilter.value)
    && (!adminOnly.value || u.is_admin)
    && (!tfaMissingOnly.value || (!u.tfa_enabled && u.state !== 'disabled')),
  )
})

function pct(n: number): string {
  const total = data.value?.summary.total ?? 0
  return total ? `${(n / total) * 100}%` : '0%'
}

const tfaPct = computed(() => {
  const s = data.value?.summary
  if (!s) return 0
  const all = s.tfa_enabled + s.tfa_missing
  return all ? Math.round((s.tfa_enabled / all) * 100) : 0
})

const stateDot = (state: UserState) => (state === 'active' ? 'fgs-c-ok' : state === 'invited' ? 'fgs-c-warn' : 'fgs-c-off')
const statePill = (state: UserState) => (state === 'active' ? 'ok' : state === 'invited' ? 'warn' : 'off')

// 시스템 › 보안 links here with #2fa=none.
function applyHash() {
  if (/2fa=none/.test(route.hash)) tfaMissingOnly.value = true
}
watch(() => route.hash, applyHash)

function exportUsers() {
  const csv = toCsv(
    ['name', 'email', 'system_admin', 'state', 'two_factor', 'last_login_at', 'created_at'],
    filtered.value.map((u) => [
      u.username, u.email, u.is_admin ? 'yes' : 'no', u.state, u.tfa_enabled ? 'on' : 'off',
      u.last_login_at, u.created_at,
    ]),
  )
  downloadCsv('users.csv', csv)
}

// ── drawer ──
const selected = ref<SystemUser | null>(null)

function openDrawer(row: SystemUser) {
  selected.value = row
}

function closeDrawer() {
  selected.value = null
}

function isLastAdmin(row: SystemUser): boolean {
  return row.is_admin && row.state !== 'disabled' && admins.value.length <= 1
}

interface Effect { icon: SettingsIconName; text: string; tone?: 'bad' | 'good' | 'warn' }
interface Pending { icon: SettingsIconName; tone: '' | 'warn' | 'danger'; title: string; effects: Effect[]; label: string; run: () => Promise<void> }
const pending = ref<Pending | null>(null)

async function runPending() {
  const task = pending.value
  if (!task) return
  busy.value = true
  try {
    await task.run()
    pending.value = null
  } catch (err) {
    pushToast(err instanceof Error ? err.message : String(err), 'danger')
  } finally {
    busy.value = false
  }
}

/**
 * Re-read the list after a change. A change is reported done only when this re-read succeeds; when
 * it fails the outcome is unconfirmed: no success notice, a warning instead, the drawer (showing
 * the old state) closes and the re-read error stays above the list.
 */
async function reloadAfterChange(): Promise<boolean> {
  if (await load()) return true
  selected.value = null
  pushToast(t('gui.settings.users.refresh_unconfirmed'), 'warning')
  return false
}

async function refreshWith(row: SystemUser | UserCredentialResult): Promise<boolean> {
  if (!(await reloadAfterChange())) return false
  const id = 'user' in row ? row.user.user_id : row.user_id
  selected.value = data.value?.users.find((u) => u.user_id === id) ?? null
  return true
}

function askAdmin(next: boolean) {
  const row = selected.value
  if (!row) return
  pending.value = {
    icon: 'star',
    tone: '',
    title: `${row.username} → ${next ? t('gui.settings.users.system_admin') : t('gui.settings.users.regular')}`,
    effects: next
      ? [{ icon: 'globe', text: t('gui.settings.users.effect_manage_all') }, { icon: 'shield', text: t('gui.settings.users.admin_tfa_hint'), tone: 'warn' }]
      : [{ icon: 'user', text: t('gui.settings.users.effect_regular') }],
    label: next ? t('gui.settings.users.assign') : t('gui.settings.users.unassign'),
    run: async () => {
      if (await refreshWith(await setSystemAdmin(row.user_id, next))) pushToast(t('gui.settings.users.admin_changed'))
    },
  }
}

function askTfaReset() {
  const row = selected.value
  if (!row) return
  pending.value = {
    icon: 'reset',
    tone: 'warn',
    title: `${row.username} · ${t('gui.settings.users.tfa_reset')}`,
    effects: [
      { icon: 'x', text: t('gui.settings.users.effect_tfa_deleted'), tone: 'bad' },
      { icon: 'shield', text: t('gui.settings.users.effect_tfa_again'), tone: 'warn' },
    ],
    label: t('gui.settings.users.reset'),
    run: async () => {
      if (await refreshWith(await resetTfa(row.user_id))) pushToast(t('gui.settings.users.tfa_reset_done'))
    },
  }
}

function askDisable() {
  const row = selected.value
  if (!row) return
  const invite = row.state === 'invited'
  pending.value = {
    icon: 'power',
    tone: 'danger',
    title: `${row.username} · ${invite ? t('gui.settings.users.cancel_invite') : t('gui.settings.users.disable')}`,
    effects: [
      { icon: 'lock', text: t('gui.settings.users.effect_login_blocked'), tone: 'bad' },
      { icon: 'logout', text: t('gui.settings.users.effect_sessions_end'), tone: 'bad' },
      { icon: 'check', text: t('gui.settings.users.effect_data_kept'), tone: 'good' },
    ],
    label: invite ? t('gui.settings.users.cancel_invite') : t('gui.settings.users.disable'),
    run: async () => {
      if (await refreshWith(await disableSystemUser(row.user_id))) pushToast(t('gui.settings.users.disabled_done'))
    },
  }
}

function askPassword() {
  const row = selected.value
  if (!row) return
  pending.value = {
    icon: 'pw',
    tone: 'warn',
    title: `${row.username} · ${t('gui.settings.users.reset_password')}`,
    effects: [
      { icon: 'key', text: t('gui.settings.users.effect_temp_password') },
      { icon: 'logout', text: t('gui.settings.users.effect_sessions_end'), tone: 'bad' },
    ],
    label: t('gui.settings.users.reset'),
    run: async () => {
      const result = await resetPassword(row.user_id)
      credential.value = result
      await refreshWith(result)
    },
  }
}

async function enable() {
  const row = selected.value
  if (!row) return
  busy.value = true
  try {
    if (await refreshWith(await enableSystemUser(row.user_id))) pushToast(t('gui.settings.users.enabled_done'))
  } catch (err) {
    pushToast(err instanceof Error ? err.message : String(err), 'danger')
  } finally {
    busy.value = false
  }
}

async function resend() {
  const row = selected.value
  if (!row) return
  busy.value = true
  try {
    const result = await resendInvite(row.user_id)
    credential.value = result
    await refreshWith(result)
  } catch (err) {
    pushToast(err instanceof Error ? err.message : String(err), 'danger')
  } finally {
    busy.value = false
  }
}

// ── create ──
const createOpen = ref(false)
const formError = ref<string | null>(null)
const form = reactive<CreateUserInput>({ name: '', email: '', start: 'invite', is_admin: false })

function openCreate() {
  Object.assign(form, { name: '', email: '', start: 'invite', is_admin: false })
  formError.value = null
  createOpen.value = true
}

async function submitCreate() {
  busy.value = true
  formError.value = null
  try {
    const result = await createSystemUser({ name: form.name.trim(), email: form.email.trim(), start: form.start, is_admin: form.is_admin })
    createOpen.value = false
    // The invite link / temporary password is shown once whatever the re-read does: it is the
    // server's answer and cannot be fetched again. The "created" notice waits for the re-read.
    credential.value = result
    if (await reloadAfterChange()) pushToast(t('gui.settings.users.created', { name: result.user.username }))
  } catch (err) {
    formError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

// ── credential hand-over ──
const credential = ref<UserCredentialResult | null>(null)
const credentialValueRef = ref<HTMLElement | null>(null)
const credentialValue = computed(() => {
  const value = credential.value
  if (!value) return ''
  return value.invite_path ? `${window.location.origin}${value.invite_path}` : value.temp_password ?? ''
})

async function copyCredential() {
  const surface = credentialValueRef.value?.closest('.ui-modal-surface') as HTMLElement | null
  if (await copyToClipboard(credentialValue.value, surface)) {
    pushToast(t('gui.settings.users.copied'))
  } else {
    pushToast(t('gui.settings.users.copy_failed'), 'warning')
  }
}

onMounted(async () => {
  applyHash()
  await load()
  if (route.query.create === '1') openCreate()
})
</script>

<style scoped>
.fgs-refresh-error {
  margin-bottom: 12px;
}

.avstack {
  display: flex;
}

.avstack .fgs-av {
  border: 2px solid #0e1e31;
  margin-left: -8px;
}

.avstack .fgs-av:first-child {
  margin-left: 0;
}

.ring {
  --p: 0;
  width: 58px;
  height: 58px;
  border-radius: 50%;
  background: conic-gradient(#33d69f calc(var(--p) * 1%), #2a3a4f 0);
  display: grid;
  place-items: center;
  flex: 0 0 58px;
}

.ring::before {
  content: '';
  grid-area: 1 / 1;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: #0e1e31;
}

.ring span {
  grid-area: 1 / 1;
  font-size: 12px;
  font-weight: 800;
  position: relative;
}

.adm {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #8fc1ff;
  font-size: 12px;
  font-weight: 700;
}

.tfa {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  white-space: nowrap;
}

.tfa.ok { color: #71ddb2; }
.tfa.no { color: #e2c76f; }
.tfa.off { color: #5d6b7e; }

.drawerback {
  position: fixed;
  inset: 0;
  background: #0006;
  opacity: 0;
  pointer-events: none;
  transition: 0.18s;
  z-index: 70;
}

.drawerback.on {
  opacity: 1;
  pointer-events: auto;
}

.drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(440px, 100vw);
  background: #0b1929;
  border-left: 1px solid var(--fg2-line-strong);
  box-shadow: -24px 0 60px #0009;
  transform: translateX(100%);
  transition: 0.2s ease;
  z-index: 75;
  display: flex;
  flex-direction: column;
}

.drawer.on {
  transform: none;
}

.dr-hd {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 22px;
  border-bottom: 1px solid var(--fg2-line);
}

.fgs-av.lg {
  width: 52px;
  height: 52px;
  font-size: 16px;
}

.dr-title {
  flex: 1;
  min-width: 0;
}

.dr-title h3 {
  margin: 0 0 4px;
  font-size: 18px;
}

.dr-title p {
  margin: 0 0 8px;
  color: #7d92a8;
  font-size: 12px;
}

.dr-bd {
  flex: 1;
  overflow-y: auto;
  padding: 10px 22px;
}

.dr-ft {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 22px;
  border-top: 1px solid var(--fg2-line);
}

.confirm {
  display: grid;
  justify-items: center;
  gap: 12px;
  padding: 10px 0 4px;
  text-align: center;
}

.conf-ico {
  width: 64px;
  height: 64px;
  border-radius: 18px;
  display: grid;
  place-items: center;
  background: #11284a;
  color: #7fb4ff;
}

.conf-ico.warn { background: #2a210e; color: #e2c76f; }
.conf-ico.danger { background: #2a1419; color: #ff8c94; }

.conf-who {
  font-size: 15px;
  font-weight: 700;
}

.effects {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
}

.effect {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 999px;
  padding: 5px 10px;
  font-size: 12px;
  color: #b9c9da;
}

.effect.bad { border-color: #74313a; color: #ff8c94; background: #2a1419; }
.effect.good { border-color: #246c53; color: #71ddb2; background: #0c2a21; }
.effect.warn { border-color: #6a571f; color: #e2c76f; background: #2a210e; }
</style>
