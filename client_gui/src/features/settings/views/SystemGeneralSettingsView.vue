<template>
  <SettingsShell :crumbs="crumbs" scope="system">
    <template #nav>
      <SettingsSubnav :title="t('gui.settings.scope.system')" :items="systemNav" active="general" />
    </template>

    <section class="fgs-panel" data-panel="system-general">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="gear" size="lg" /></span>
        <h2>{{ t('gui.settings.system_nav.general') }}</h2>
      </div>

      <div class="fgs-panel-body">
        <!-- 저장 Root: GET /system/storage-root, 읽기 전용 -->
        <div class="fgs-card" data-card="storage-root">
          <div class="fgs-card-h">
            <span class="fgs-tico sm"><SettingsIcon name="storage" /></span>
            <b>{{ t('gui.settings.general.root.title') }}</b>
          </div>
          <div v-if="rootLoading" class="fgs-line"><LoadingState inline size="sm" :label="t('gui.common.loading')" /></div>
          <div v-else-if="rootError" class="fgs-line">
            <ErrorState compact :title="t('gui.settings.general.errors.root_failed')" :message="rootError" data-testid="root-error">
              <template #action>
                <Button variant="secondary" @click="loadRoot">{{ t('gui.common.retry') }}</Button>
              </template>
            </ErrorState>
          </div>
          <template v-else-if="root">
            <div class="fgs-line">
              <div class="k">{{ t('gui.settings.general.root.configured') }}</div>
              <div class="v mono" data-testid="root-configured">{{ root.configured_value ?? t('gui.settings.general.root.unset') }}</div>
            </div>
            <div class="fgs-line">
              <div class="k">{{ t('gui.settings.general.root.effective') }}</div>
              <div class="v mono" data-testid="root-effective">{{ root.effective_value }}</div>
            </div>
            <div class="fgs-line">
              <div class="k">{{ t('gui.settings.general.root.source') }}</div>
              <div class="v" data-testid="root-source">{{ sourceLabel(root.effective_source) }}</div>
            </div>
            <div v-if="root.env_override" class="fgs-line">
              <span class="fgs-hint" data-testid="root-env-override"><SettingsIcon name="info" size="sm" />{{ t('gui.settings.general.root.env_override') }}</span>
            </div>
            <div class="fgs-line">
              <span class="fgs-muted">{{ t('gui.settings.general.root.readonly_hint') }}</span>
            </div>
          </template>
        </div>

        <div v-if="settingsLoading" class="fgs-card"><LoadingState inline size="sm" :label="t('gui.common.loading')" /></div>
        <div v-else-if="settingsError" class="fgs-card">
          <ErrorState compact :title="t('gui.settings.general.errors.settings_failed')" :message="settingsError" data-testid="settings-error">
            <template #action>
              <Button variant="secondary" @click="loadSettings">{{ t('gui.common.retry') }}</Button>
            </template>
          </ErrorState>
        </div>
        <template v-else>
          <!-- Scratch 보존 기간: scratch_ttl_value + scratch_ttl_unit, PATCH 한 건에 둘 다 -->
          <div class="fgs-card" data-card="scratch">
            <div class="fgs-card-h">
              <span class="fgs-tico sm"><SettingsIcon name="timer" /></span>
              <b>{{ t('gui.settings.general.scratch.title') }}</b>
            </div>
            <div class="fgs-line">
              <label class="k" for="sys-scratch-value">{{ t('gui.settings.general.scratch.label') }}</label>
              <div class="v row">
                <input
                  id="sys-scratch-value"
                  v-model="scratchValue"
                  class="fgs-input num"
                  inputmode="numeric"
                  autocomplete="off"
                  :disabled="scratchSaving"
                  data-testid="scratch-value"
                />
                <select v-model="scratchUnit" class="fgs-input unit" :aria-label="t('gui.settings.general.scratch.unit')" :disabled="scratchSaving" data-testid="scratch-unit">
                  <option v-for="unit in SCRATCH_UNITS" :key="unit" :value="unit">{{ t(`gui.settings.general.scratch.units.${unit}`) }}</option>
                </select>
                <button
                  type="button"
                  class="fgs-btn pri"
                  :disabled="!scratchCanSave"
                  data-testid="scratch-save"
                  @click="saveScratch"
                >{{ scratchSaving ? t('gui.settings.saving') : t('gui.settings.general.save') }}</button>
              </div>
            </div>
            <div v-if="scratchDirty && scratchProblem" class="fgs-line">
              <span class="fgs-error" data-testid="scratch-invalid">{{ scratchProblem }}</span>
            </div>
            <div v-if="scratchError" class="fgs-line">
              <span class="fgs-error" role="alert" data-testid="scratch-error">{{ scratchError }}</span>
            </div>
            <div v-else-if="scratchSaved && !scratchDirty" class="fgs-line">
              <span class="fgs-muted" data-testid="scratch-saved">{{ t('gui.settings.saved') }}</span>
            </div>
            <div class="fgs-line">
              <span class="fgs-muted">{{ t('gui.settings.general.scratch.hint') }}</span>
            </div>
          </div>

          <!-- AI 반복 상한: ai_repeat_count_max 하나만 -->
          <div class="fgs-card" data-card="repeat">
            <div class="fgs-card-h">
              <span class="fgs-tico sm"><SettingsIcon name="reset" /></span>
              <b>{{ t('gui.settings.general.repeat.title') }}</b>
            </div>
            <div class="fgs-line">
              <label class="k" for="sys-repeat-max">{{ t('gui.settings.general.repeat.label') }}</label>
              <div class="v row">
                <input
                  id="sys-repeat-max"
                  v-model="repeatValue"
                  class="fgs-input num"
                  inputmode="numeric"
                  autocomplete="off"
                  :disabled="repeatSaving"
                  data-testid="repeat-value"
                />
                <button
                  type="button"
                  class="fgs-btn pri"
                  :disabled="!repeatCanSave"
                  data-testid="repeat-save"
                  @click="askRepeat"
                >{{ repeatSaving ? t('gui.settings.saving') : t('gui.settings.general.save') }}</button>
              </div>
            </div>
            <div v-if="repeatDirty && repeatProblem" class="fgs-line">
              <span class="fgs-error" data-testid="repeat-invalid">{{ repeatProblem }}</span>
            </div>
            <div v-if="repeatLowering" class="fgs-line">
              <span class="fgs-hint" data-testid="repeat-lower-hint"><SettingsIcon name="alert" size="sm" />{{ t('gui.settings.general.repeat.lower_impact') }}</span>
            </div>
            <div v-if="repeatError" class="fgs-line">
              <span class="fgs-error" role="alert" data-testid="repeat-error">{{ repeatError }}</span>
            </div>
            <div v-else-if="repeatSaved && !repeatDirty" class="fgs-line">
              <span class="fgs-muted" data-testid="repeat-saved">{{ t('gui.settings.saved') }}</span>
            </div>
            <div class="fgs-line">
              <span class="fgs-muted">{{ t('gui.settings.general.repeat.hint') }}</span>
            </div>
          </div>
        </template>
      </div>
    </section>

    <!-- 반복 상한 하향 확인: 취소하면 요청 0건 -->
    <ModalShell :open="lowerConfirmOpen" size="sm" :aria-label="t('gui.settings.general.repeat.lower_title')" @close="lowerConfirmOpen = false">
      <div class="confirm" data-testid="repeat-lower-confirm">
        <div class="conf-ico warn"><SettingsIcon name="alert" size="xl" /></div>
        <div class="conf-who">{{ t('gui.settings.general.repeat.lower_title_values', { from: repeatConfirmed, to: repeatValue.trim() }) }}</div>
        <p class="conf-text">{{ t('gui.settings.general.repeat.lower_impact') }}</p>
        <p class="conf-text fgs-muted">{{ t('gui.settings.general.repeat.lower_no_rewrite') }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" data-testid="repeat-lower-cancel" @click="lowerConfirmOpen = false">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="repeatSaving" data-testid="repeat-lower-ok" @click="confirmLower">{{ t('gui.settings.general.save') }}</button>
        </div>
      </template>
    </ModalShell>

    <!-- 미저장 이탈 보호: 계속 편집하면 이동 취소, 요청 0건 -->
    <ModalShell :open="leaveOpen" size="sm" :aria-label="t('gui.settings.general.leave.title')" @close="answerLeave(false)">
      <div class="confirm" data-testid="leave-confirm">
        <div class="conf-ico warn"><SettingsIcon name="alert" size="xl" /></div>
        <div class="conf-who">{{ t('gui.settings.general.leave.title') }}</div>
        <p class="conf-text">{{ t('gui.settings.general.leave.unsaved') }}</p>
        <p v-if="scratchSaving || repeatSaving" class="conf-text fgs-muted" data-testid="leave-saving">{{ t('gui.settings.general.leave.saving') }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" data-testid="leave-stay" @click="answerLeave(false)">{{ t('gui.settings.general.leave.stay') }}</button>
          <button type="button" class="fgs-btn danger" data-testid="leave-discard" @click="answerLeave(true)">{{ t('gui.settings.general.leave.discard') }}</button>
        </div>
      </template>
    </ModalShell>
  </SettingsShell>
</template>

<script setup lang="ts">
// 시스템 › 일반 (flowgate.v02.0076 T#3, D §2.5). Only the existing contracts:
//
// - 저장 Root: GET /system/storage-root — the stored value, the effective Root, where it comes from
//   and whether FLOWGATE_STORAGE_DIR overrides it. Read only: no input, no save, no move, and
//   storage_root never goes into a PATCH. /system/settings/storage-root (a different contract with
//   other field names) is not used, not even as a fallback.
// - Scratch 보존 기간 and AI 반복 상한 come from the rows of GET /system/settings and are two
//   separate saves on PATCH /system/settings:
//   · Scratch: scratch_ttl_value and scratch_ttl_unit together, as strings, in ONE request — the
//     server validates the pair and writes it in one store transaction (V13 checked the deployment
//     store rolls both keys back). Never split into two PATCHes, never converted to minutes.
//   · 반복 상한: ai_repeat_count_max alone, 1~30. Lowering it first shows the impact on stored and
//     paused runs (re-evaluated against the current ceiling on run/resume); this screen never edits
//     a run or resumes one.
// A save is complete only when the PATCH answer carries exactly the sent value(s) AND a re-read
// GET agrees; otherwise the input stays as typed and an error is shown. Invalid input is never sent.
// The 저장되었습니다 notice belongs to the value that was saved: editing again takes it away.
// 미저장 이탈 보호 (D §3.2): while an editor holds an unsaved input or a save is still out, leaving
// by the app (another route) asks first and a refresh / tab close gets the browser's own question.
// It never holds the screen once the session or the administrator role is gone (sign-out, a lost
// token, the role taken away): those moves go through at once.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useSystemNav } from '../components/systemNav'
import { currentSettingsAccess } from '../access'
import { getSystemSettings, getSystemStorageRoot, saveRepeatCountMax, saveScratchRetention } from '../api'
import type { ScratchUnit, SystemSettingRow, SystemStorageRoot } from '../types'

const SCRATCH_UNITS: readonly ScratchUnit[] = ['minute', 'hour', 'day']
const MINUTES_PER_UNIT: Record<ScratchUnit, bigint> = { minute: 1n, hour: 60n, day: 1440n }
// The server turns the pair into a Python timedelta(minutes=…); beyond 999999999 days it overflows
// and answers 422 "too large". The same bound here keeps such a pair from being sent at all.
const MAX_SCRATCH_MINUTES = 999999999n * 1440n + 1439n
const REPEAT_MIN = 1
const REPEAT_MAX = 30

const { t } = useI18n()
const route = useRoute()
const systemNav = useSystemNav()

const crumbs = computed(() => [
  { label: t('gui.settings.title'), to: '/settings' },
  { label: t('gui.settings.scope.system') },
  { label: t('gui.settings.system_nav.general') },
])

// ── 저장 Root ──
const root = ref<SystemStorageRoot | null>(null)
const rootLoading = ref(true)
const rootError = ref('')

function isRoot(body: unknown): body is SystemStorageRoot {
  const b = body as Partial<SystemStorageRoot> | null
  return !!b && typeof b.effective_value === 'string' && typeof b.effective_source === 'string'
    && typeof b.env_override === 'boolean' && (b.configured_value === null || typeof b.configured_value === 'string')
}

async function loadRoot() {
  rootLoading.value = true
  rootError.value = ''
  try {
    const body = await getSystemStorageRoot()
    if (!isRoot(body)) throw new Error(t('gui.settings.general.errors.bad_response'))
    root.value = body
  } catch (err) {
    root.value = null
    rootError.value = err instanceof Error ? err.message : String(err)
  } finally {
    rootLoading.value = false
  }
}

function sourceLabel(source: string): string {
  const key = `gui.settings.general.root.sources.${source}`
  return ['env', 'project_override', 'system_setting', 'default'].includes(source) ? t(key) : source
}

// ── settings rows ──
const settingsLoading = ref(true)
const settingsError = ref('')

interface Pair { value: string; unit: string }

function rowValue(rows: SystemSettingRow[] | undefined, key: string): string | null {
  const row = Array.isArray(rows) ? rows.find((r) => r?.setting_key === key) : undefined
  return row && typeof row.setting_value === 'string' ? row.setting_value : null
}

function scratchIn(rows: SystemSettingRow[] | undefined): Pair | null {
  const value = rowValue(rows, 'scratch_ttl_value')
  const unit = rowValue(rows, 'scratch_ttl_unit')
  return value === null || unit === null ? null : { value, unit }
}

/** Load both editors from one GET; a missing row is a failed load, not a default. */
async function loadSettings() {
  settingsLoading.value = true
  settingsError.value = ''
  try {
    const rows = await getSystemSettings()
    const scratch = scratchIn(rows)
    const repeat = rowValue(rows, 'ai_repeat_count_max')
    if (!scratch || repeat === null) throw new Error(t('gui.settings.general.errors.bad_response'))
    scratchConfirmed.value = scratch
    scratchValue.value = scratch.value
    scratchUnit.value = scratch.unit
    repeatConfirmed.value = repeat
    repeatValue.value = repeat
  } catch (err) {
    settingsError.value = err instanceof Error ? err.message : String(err)
  } finally {
    settingsLoading.value = false
  }
}

// ── Scratch 보존 기간 ──
const scratchConfirmed = ref<Pair | null>(null)
const scratchValue = ref('')
const scratchUnit = ref('')
const scratchSaving = ref(false)
const scratchError = ref('')
const scratchSaved = ref(false)

const scratchDirty = computed(() =>
  !!scratchConfirmed.value
  && (scratchValue.value.trim() !== scratchConfirmed.value.value || scratchUnit.value !== scratchConfirmed.value.unit))

/** The server's own rule (scratch_retention.parse): a positive integer, minute/hour/day, no overflow. */
const scratchProblem = computed(() => {
  const value = scratchValue.value.trim()
  if (!/^[1-9][0-9]*$/.test(value)) return t('gui.settings.general.scratch.invalid_value')
  if (!(SCRATCH_UNITS as readonly string[]).includes(scratchUnit.value)) return t('gui.settings.general.scratch.invalid_unit')
  if (BigInt(value) * MINUTES_PER_UNIT[scratchUnit.value as ScratchUnit] > MAX_SCRATCH_MINUTES) {
    return t('gui.settings.general.scratch.too_large')
  }
  return ''
})

const scratchCanSave = computed(() => scratchDirty.value && !scratchProblem.value && !scratchSaving.value)

async function saveScratch() {
  if (!scratchCanSave.value) return
  const value = scratchValue.value.trim()
  const unit = scratchUnit.value as ScratchUnit
  scratchSaving.value = true
  scratchError.value = ''
  scratchSaved.value = false
  try {
    const answered = scratchIn(await saveScratchRetention(value, unit))
    if (!answered || answered.value !== value || answered.unit !== unit) {
      scratchError.value = t('gui.settings.general.errors.unconfirmed')
      await rereadScratch()
      return
    }
    const reread = await rereadScratch()
    if (!reread || reread.value !== value || reread.unit !== unit) {
      scratchError.value = t('gui.settings.general.errors.reread_mismatch')
      return
    }
    scratchSaved.value = true
  } catch (err) {
    scratchError.value = err instanceof Error ? err.message : String(err)
  } finally {
    scratchSaving.value = false
  }
}

/** Re-read after a save; the stored pair becomes the confirmed one, the input is left as typed. */
async function rereadScratch(): Promise<Pair | null> {
  try {
    const pair = scratchIn(await getSystemSettings())
    if (pair) scratchConfirmed.value = pair
    return pair
  } catch {
    return null
  }
}

// ── AI 반복 상한 ──
const repeatConfirmed = ref<string | null>(null)
const repeatValue = ref('')
const repeatSaving = ref(false)
const repeatError = ref('')
const repeatSaved = ref(false)
const lowerConfirmOpen = ref(false)

const repeatDirty = computed(() => repeatConfirmed.value !== null && repeatValue.value.trim() !== repeatConfirmed.value)

const repeatProblem = computed(() => {
  const value = repeatValue.value.trim()
  const n = /^[0-9]+$/.test(value) ? Number(value) : NaN
  return Number.isInteger(n) && n >= REPEAT_MIN && n <= REPEAT_MAX
    ? ''
    : t('gui.settings.general.repeat.invalid', { min: REPEAT_MIN, max: REPEAT_MAX })
})

const repeatCanSave = computed(() => repeatDirty.value && !repeatProblem.value && !repeatSaving.value)

/** A valid new ceiling below the confirmed one (a confirmed value that is not a number counts as no). */
const repeatLowering = computed(() => {
  if (!repeatDirty.value || repeatProblem.value) return false
  const before = Number(repeatConfirmed.value)
  return Number.isInteger(before) && Number(repeatValue.value.trim()) < before
})

function askRepeat() {
  if (!repeatCanSave.value) return
  if (repeatLowering.value) {
    lowerConfirmOpen.value = true
    return
  }
  void saveRepeat()
}

function confirmLower() {
  if (repeatSaving.value) return
  void saveRepeat()
}

async function saveRepeat() {
  if (!repeatCanSave.value) return
  const value = String(Number(repeatValue.value.trim()))
  repeatSaving.value = true
  repeatError.value = ''
  repeatSaved.value = false
  try {
    const answered = rowValue(await saveRepeatCountMax(value), 'ai_repeat_count_max')
    lowerConfirmOpen.value = false
    if (answered !== value) {
      repeatError.value = t('gui.settings.general.errors.unconfirmed')
      await rereadRepeat()
      return
    }
    const reread = await rereadRepeat()
    if (reread !== value) {
      repeatError.value = t('gui.settings.general.errors.reread_mismatch')
      return
    }
    repeatSaved.value = true
  } catch (err) {
    lowerConfirmOpen.value = false
    repeatError.value = err instanceof Error ? err.message : String(err)
  } finally {
    repeatSaving.value = false
  }
}

async function rereadRepeat(): Promise<string | null> {
  try {
    const value = rowValue(await getSystemSettings(), 'ai_repeat_count_max')
    if (value !== null) repeatConfirmed.value = value
    return value
  } catch {
    return null
  }
}

// A success notice is for the value that was saved, not for what is typed afterwards.
watch([scratchValue, scratchUnit], () => {
  scratchSaved.value = false
})
watch(repeatValue, () => {
  repeatSaved.value = false
})

// ── 미저장 이탈 보호 ──
const leaveOpen = ref(false)
let leaveAnswer: ((leave: boolean) => void) | null = null

const unsaved = computed(() => scratchDirty.value || repeatDirty.value || scratchSaving.value || repeatSaving.value)

// The same token check the auth guard makes (core/router/guard).
const hasToken = () => !!(window.__accessToken__ || sessionStorage.getItem('fg_access_token'))

/** Hold a move only while this administrator screen is still theirs and something is unsaved. */
function holdsLeave(at: Parameters<typeof currentSettingsAccess>[0]): boolean {
  return unsaved.value && hasToken() && currentSettingsAccess(at).kind === 'allow'
}

function answerLeave(leave: boolean) {
  leaveOpen.value = false
  const answer = leaveAnswer
  leaveAnswer = null
  answer?.(leave)
}

onBeforeRouteLeave((_to, from) => {
  if (!holdsLeave(from)) return true
  answerLeave(false) // an older, unanswered move is superseded by this one
  leaveOpen.value = true
  return new Promise<boolean>((resolve) => {
    leaveAnswer = resolve
  })
})

// The session or the role went away while the question is open: let the move go at once.
watch(() => currentSettingsAccess(route).kind, (kind) => {
  if (kind !== 'allow') answerLeave(true)
})

function onBeforeUnload(event: BeforeUnloadEvent) {
  if (!holdsLeave(route)) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
  void loadRoot()
  void loadSettings()
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
  answerLeave(true) // the view is going anyway (e.g. the layout hid it); never leave a move hanging
})
</script>

<style scoped>
.fgs-line .k {
  min-width: 150px;
}

.v.mono {
  font-family: var(--fg2-font-mono, ui-monospace, monospace);
  word-break: break-all;
}

.v.row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.fgs-input.num {
  width: 120px;
}

.fgs-input.unit {
  width: 110px;
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
}

.conf-ico.warn { background: #2a210e; color: #e2c76f; }

.conf-who {
  font-size: 15px;
  font-weight: 700;
}

.conf-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
}
</style>
