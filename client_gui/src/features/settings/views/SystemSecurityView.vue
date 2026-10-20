<template>
  <SettingsShell scope="system">
    <template #nav>
      <SettingsSubnav :items="systemNav" active="security" />
    </template>

    <section class="fgs-panel" data-panel="security">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="shield" size="lg" /></span>
        <h2>{{ t('gui.settings.security.title') }}</h2>
        <div class="fgs-push">
          <span class="fgs-autosave" :class="autosave.state.value" data-testid="autosave"><span class="fgs-dot" />{{ autosaveLabel }}</span>
        </div>
      </div>

      <div class="fgs-panel-body">
        <LoadingState v-if="loading && !policy" :label="t('gui.common.loading')" />
        <ErrorState v-else-if="error && !policy" :title="t('gui.settings.errors.load_failed')" :message="error">
          <template #action>
            <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
          </template>
        </ErrorState>

        <template v-else-if="policy">
          <!-- 2단계 인증 -->
          <div class="fgs-card">
            <div class="fgs-card-h"><span class="fgs-tico sm"><SettingsIcon name="shieldok" /></span><b>{{ t('gui.settings.security.tfa') }}</b></div>
            <div class="fgs-line">
              <div class="cover">
                <div class="people">
                  <div
                    v-for="person in people"
                    :key="person.user_id"
                    class="person"
                    :class="[person.tfa_enabled ? 'ok' : 'miss', { out: !covered(person) }]"
                    :title="person.username"
                    :data-person="person.user_id"
                  >
                    <div class="fgs-av">
                      {{ initials(person.username) }}
                      <span class="mk"><SettingsIcon :name="person.tfa_enabled ? 'check' : 'alert'" /></span>
                    </div>
                    {{ person.username.split(' ')[0] }}
                  </div>
                </div>
                <div class="cover-num" :class="{ ok: policy.tfa_scope !== 'off' && missing === 0 }">
                  <div>
                    <div class="big" data-testid="tfa-missing">
                      <SettingsIcon v-if="policy.tfa_scope !== 'off' && missing === 0" name="check" size="xl" />
                      <template v-else>{{ missing }}</template>
                    </div>
                    <div class="lbl">{{ missingLabel }}</div>
                  </div>
                  <RouterLink v-if="people.some((p) => !p.tfa_enabled)" to="/settings/system/users#2fa=none" class="fgs-btn sm" data-testid="tfa-users-link">
                    <SettingsIcon name="users" size="sm" />{{ t('gui.settings.security.see_users') }}
                  </RouterLink>
                </div>
              </div>
            </div>
            <div class="fgs-line">
              <div class="k"><SettingsIcon name="users" />{{ t('gui.settings.security.required_for') }}</div>
              <div class="fgs-choice-grid" role="radiogroup">
                <button
                  v-for="choice in SCOPES"
                  :key="choice.value"
                  type="button"
                  class="fgs-choice"
                  :class="{ on: policy.tfa_scope === choice.value }"
                  :disabled="saving"
                  :data-tfa-scope="choice.value"
                  @click="save({ tfa_scope: choice.value })"
                >
                  <SettingsIcon :name="choice.icon" />
                  <span class="fgs-mini"><i v-for="n in 4" :key="n" :class="{ t: n <= choice.bars }" /></span>
                  <b>{{ t(`gui.settings.security.scope.${choice.value}`) }}</b>
                </button>
              </div>
            </div>
            <div v-if="policy.tfa_scope !== 'off'" class="fgs-line" data-testid="grace-row">
              <div class="k"><SettingsIcon name="timer" />{{ t('gui.settings.security.grace') }}<i class="tip" :title="t('gui.settings.security.grace_tip')">?</i></div>
              <div class="v">
                <div class="fgs-chips">
                  <button
                    v-for="days in GRACE"
                    :key="days"
                    type="button"
                    class="fgs-chip"
                    :class="{ on: policy.tfa_grace_days === days }"
                    :disabled="saving"
                    :data-grace="days"
                    @click="save({ tfa_grace_days: days })"
                  >{{ days === 0 ? t('gui.settings.security.now') : t('gui.settings.security.days', { n: days }) }}</button>
                </div>
              </div>
            </div>
            <div class="fgs-line">
              <div class="k"><SettingsIcon name="key" />{{ t('gui.settings.security.recovery') }}<i class="tip" :title="t('gui.settings.security.recovery_tip')">?</i></div>
              <div class="v">
                <label class="fgs-toggle">
                  <input type="checkbox" :checked="policy.recovery_codes" :disabled="saving" :aria-label="t('gui.settings.security.recovery')" data-testid="recovery" @change="save({ recovery_codes: !policy.recovery_codes })" />
                  <span class="fgs-track" />
                </label>
              </div>
            </div>
          </div>

          <!-- 로그인 -->
          <div class="fgs-card">
            <div class="fgs-card-h"><span class="fgs-tico sm"><SettingsIcon name="lock" /></span><b>{{ t('gui.settings.security.login') }}</b></div>
            <div class="fgs-line">
              <div class="k"><SettingsIcon name="pw" />{{ t('gui.settings.security.password_length') }}</div>
              <div class="v">
                <div class="fgs-chips">
                  <button v-for="n in LENGTHS" :key="n" type="button" class="fgs-chip" :class="{ on: policy.password_min_length === n }" :disabled="saving" :data-pwlen="n" @click="save({ password_min_length: n })">
                    {{ t('gui.settings.security.chars', { n }) }}
                  </button>
                </div>
              </div>
            </div>
            <div class="fgs-line">
              <div class="k"><SettingsIcon name="ban" />{{ t('gui.settings.security.lockout') }}</div>
              <div class="v">
                <div class="fgs-chips">
                  <button v-for="n in ATTEMPTS" :key="n" type="button" class="fgs-chip" :class="{ on: policy.lock_attempts === n }" :disabled="saving" :data-lock-attempts="n" @click="save({ lock_attempts: n })">
                    {{ t('gui.settings.security.times', { n }) }}
                  </button>
                </div>
                <span class="fgs-muted">{{ t('gui.settings.security.failures') }}</span>
                <span class="arrow"><SettingsIcon name="chev" /></span>
                <span class="fgs-tico sm warn"><SettingsIcon name="lock" /></span>
                <div class="fgs-chips">
                  <button v-for="n in LOCK_MINUTES" :key="n" type="button" class="fgs-chip" :class="{ on: policy.lock_minutes === n }" :disabled="saving" :data-lock-minutes="n" @click="save({ lock_minutes: n })">
                    {{ n === 60 ? t('gui.settings.security.hours', { n: 1 }) : t('gui.settings.security.minutes', { n }) }}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- 세션 -->
          <div class="fgs-card">
            <div class="fgs-card-h"><span class="fgs-tico sm"><SettingsIcon name="clock" /></span><b>{{ t('gui.settings.security.session') }}</b></div>
            <div class="fgs-line">
              <div class="k"><SettingsIcon name="logout" />{{ t('gui.settings.security.idle') }}<i class="tip" :title="t('gui.settings.security.idle_tip')">?</i></div>
              <div class="v">
                <div class="fgs-chips">
                  <button v-for="value in IDLE" :key="value" type="button" class="fgs-chip" :class="{ on: policy.idle_timeout === value }" :disabled="saving" :data-idle="value" @click="save({ idle_timeout: value })">
                    {{ t(`gui.settings.security.idle_choice.${value}`) }}
                  </button>
                </div>
              </div>
            </div>
            <div class="fgs-line">
              <div class="k"><SettingsIcon name="shield" />{{ t('gui.settings.security.tighten_logout') }}<i class="tip" :title="t('gui.settings.security.tighten_tip')">?</i></div>
              <div class="v">
                <label class="fgs-toggle">
                  <input type="checkbox" :checked="policy.logout_on_tighten" :disabled="saving" :aria-label="t('gui.settings.security.tighten_logout')" data-testid="tighten" @change="save({ logout_on_tighten: !policy.logout_on_tighten })" />
                  <span class="fgs-track" />
                </label>
              </div>
            </div>
          </div>
        </template>
      </div>
    </section>

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </SettingsShell>
</template>

<script setup lang="ts">
// 시안 14 시스템 › 보안: every choice saves at once (자동 저장). The 2FA row shows who the
// current scope covers and who has not enrolled; the policy itself is enforced by the server at
// sign-in (enrolment after the grace period), on failed sign-ins (lockout), on new passwords
// (length) and on session activity (자동 로그아웃).
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useAutosave, useSettingsToasts } from '../components/useSettingsToasts'
import { useSystemNav } from '../components/systemNav'
import type { SettingsIconName } from '../icons'
import { getSecurity, saveSecurity } from '../api'
import { initials } from '../format'
import type { IdleTimeout, SecurityPerson, SecurityPolicy, TfaScope } from '../types'

const SCOPES: { value: TfaScope; icon: SettingsIconName; bars: number }[] = [
  { value: 'off', icon: 'ban', bars: 0 },
  { value: 'admin', icon: 'star', bars: 2 },
  { value: 'all', icon: 'users', bars: 4 },
]
const GRACE = [0, 7, 14, 30] as const
const LENGTHS = [8, 10, 12] as const
const ATTEMPTS = [3, 5, 10] as const
const LOCK_MINUTES = [5, 15, 60] as const
const IDLE: IdleTimeout[] = ['30m', '2h', '8h', '7d']

const { t } = useI18n()
const systemNav = useSystemNav()
const { toasts, pushToast, dismissToast } = useSettingsToasts()
const autosave = useAutosave()

const policy = ref<SecurityPolicy | null>(null)
const people = ref<SecurityPerson[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const saving = ref(false)

async function load() {
  loading.value = true
  error.value = null
  try {
    const result = await getSecurity()
    policy.value = result.policy
    people.value = result.people
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

function covered(person: SecurityPerson): boolean {
  const scope = policy.value?.tfa_scope
  return scope === 'all' || (scope === 'admin' && person.is_admin)
}

const missing = computed(() =>
  people.value.filter((p) => !p.tfa_enabled && (policy.value?.tfa_scope === 'off' || covered(p))).length,
)

const missingLabel = computed(() => {
  if (policy.value?.tfa_scope === 'off') return t('gui.settings.security.missing_optional')
  return missing.value === 0 ? t('gui.settings.security.all_enrolled') : t('gui.settings.security.missing_in_scope')
})

const autosaveLabel = computed(() => {
  if (autosave.state.value === 'saving') return t('gui.settings.saving')
  if (autosave.state.value === 'error') return t('gui.settings.save_failed')
  return t('gui.settings.autosave')
})

async function save(patch: Partial<SecurityPolicy>) {
  const current = policy.value
  if (!current) return
  const changed = Object.entries(patch).some(([key, value]) => current[key as keyof SecurityPolicy] !== value)
  if (!changed) return
  saving.value = true
  const previous = { ...current }
  policy.value = { ...current, ...patch }
  try {
    const result = await autosave.run(() => saveSecurity(patch))
    policy.value = result.policy
    people.value = result.people
    if (result.signed_out) pushToast(t('gui.settings.security.signed_out', { n: result.signed_out }), 'warning')
  } catch (err) {
    policy.value = previous
    pushToast(err instanceof Error ? err.message : String(err), 'danger')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.cover {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}

.people {
  flex: 1;
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}

.person {
  display: grid;
  justify-items: center;
  gap: 6px;
  font-size: 11px;
  color: #b9c9da;
  min-width: 52px;
}

.person .fgs-av {
  position: relative;
  width: 42px;
  height: 42px;
  box-shadow: 0 0 0 2px #2a3a4f;
}

.person.ok .fgs-av { box-shadow: 0 0 0 2px #33d69f; }
.person.miss .fgs-av { box-shadow: 0 0 0 2px #f5b84b; }
.person.out { opacity: 0.4; }

.mk {
  position: absolute;
  right: -4px;
  bottom: -4px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: #0e1e31;
  color: #71ddb2;
}

.person.miss .mk { color: #e2c76f; }

.mk :deep(svg) {
  width: 12px;
  height: 12px;
  stroke-width: 2.6;
}

.cover-num {
  display: flex;
  align-items: center;
  gap: 16px;
  border-left: 1px solid var(--fg2-line);
  padding-left: 18px;
}

.cover-num .big {
  font-size: 34px;
  font-weight: 800;
  color: #e2c76f;
  line-height: 1;
}

.cover-num.ok .big { color: #71ddb2; }

.cover-num .lbl {
  font-size: 12px;
  color: #8ba0b7;
  margin-top: 6px;
}

.tip {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1px solid var(--fg2-line-strong);
  display: inline-grid;
  place-items: center;
  font-size: 10px;
  font-style: normal;
  color: #8ba0b7;
  cursor: help;
}

.arrow {
  color: #5f7690;
  display: inline-grid;
}
</style>
