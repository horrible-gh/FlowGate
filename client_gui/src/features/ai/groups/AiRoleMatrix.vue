<template>
  <div class="role-profile-settings" :aria-busy="busy">
    <div v-if="error" class="matrix-error" role="alert">
      {{ error }}
      <button v-if="retryRead" type="button" :disabled="busy" @click="load">{{ t('gui.role_profiles.retry') }}</button>
    </div>
    <div class="role-grid-wrap">
      <div class="role-matrix">
        <div class="role-matrix-toolbar">
          <strong>{{ t('gui.role_profiles.title') }}</strong>
          <select class="add-kind-select" :value="''" :aria-label="t('gui.role_profiles.add_aria')"
            :disabled="!canEdit || busy || !loaded || !addable.length" @change="addType">
            <option value="">{{ t(loaded && !addable.length ? 'gui.role_profiles.all_added' : 'gui.role_profiles.add') }}</option>
            <option v-for="type in addable" :key="type.type_key" :value="type.type_key">{{ type.label }}</option>
          </select>
        </div>
        <div class="role-matrix-head">
          <span>{{ t('gui.role_profiles.card_type') }}</span>
          <span v-for="role in PROFILE_ROLES" :key="role">{{ t(`gui.role_profiles.${role}`) }}</span>
        </div>
        <div v-for="row in rows" :key="row.type_key" class="role-matrix-row" :class="{ shared: row.type_key === 'common' }" :data-type="row.type_key">
          <div class="role-kind">
            <span v-if="row.type_key === 'common'" class="role-shared-icon" aria-hidden="true">✧</span>
            <strong>{{ typeLabel(row.type_key) }}</strong>
            <button v-if="row.type_key !== 'common' && canEdit" type="button" class="remove-kind"
              :title="t('gui.role_profiles.remove', { name: typeLabel(row.type_key) })"
              :aria-label="t('gui.role_profiles.remove', { name: typeLabel(row.type_key) })"
              :disabled="busy" @click="mutate((id) => deleteAiRoleProfile(id, row.type_key))">×</button>
          </div>
          <select v-for="role in PROFILE_ROLES" :key="role" class="role-select"
            :class="{ 'role-inherited': row.roles[role].kind === 'inherit' }"
            :data-role="role" :value="profileValueKey(row.roles[role])" :disabled="!canEdit || busy"
            :aria-label="`${typeLabel(row.type_key)} · ${t(`gui.role_profiles.${role}`)}`"
            @change="changeCell($event, row, role)">
            <option v-if="row.type_key === 'common'" value="unset">{{ t('gui.role_profiles.unset') }}</option>
            <option v-else value="inherit">{{ t('gui.role_profiles.inherit', { value: valueLabel(common?.roles[role]) }) }}</option>
            <option value="not_used">{{ t('gui.role_profiles.not_used') }}</option>
            <option v-if="unavailable(row.roles[role])" :value="profileValueKey(row.roles[role])" disabled>{{ valueLabel(row.roles[role]) }}</option>
            <option v-for="provider in options.providers" :key="provider.provider_id" :value="`provider:${provider.provider_id}`">{{ provider.provider_name }}</option>
          </select>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { DEFAULT_PALETTE_ITEMS } from '@/features/designer/palette/paletteCatalog'
import { addAiRoleProfile, deleteAiRoleProfile, getAiRoleProfileOptions, getAiRoleProfiles, setAiRoleProfileCell } from '../api'
import type { AiRoleProfile, AiRoleProfileOptions, AiRoleProfiles, RoleProfileRole, RoleProfileValue } from '../types'
import { inProfileQueue, PROFILE_ROLES, profileValue, profileValueKey } from './roleProfileValues'

const props = defineProps<{ groupId: string; canEdit: boolean }>()
const { t, locale } = useI18n()
const rows = ref<AiRoleProfile[]>([])
const options = ref<AiRoleProfileOptions>({ types: [], providers: [] })
const loaded = ref(false)
const busy = ref(false)
const error = ref('')
const retryRead = ref(false)
let epoch = 0
const common = computed(() => rows.value.find(row => row.type_key === 'common'))
const paletteTypes = computed(() => DEFAULT_PALETTE_ITEMS
  .filter(item => item.docType !== null && options.value.types.some(type => type.type_key === item.docType))
  .map(item => ({ type_key: item.docType!, label: t(`gui.designer.palette.items.${item.labelKey}`) })))
const addable = computed(() => paletteTypes.value.filter(type => !rows.value.some(row => row.type_key === type.type_key)))
function typeLabel(key: string): string {
  if (key === 'common') return t('gui.role_profiles.common')
  const item = DEFAULT_PALETTE_ITEMS.find(item => item.docType === key)
  return item ? t(`gui.designer.palette.items.${item.labelKey}`) : options.value.types.find(type => type.type_key === key)?.label ?? key
}
function unavailable(value: RoleProfileValue): boolean {
  return value.kind === 'assigned' && !options.value.providers.some(p => p.provider_id === value.provider_id)
}
function valueLabel(value?: RoleProfileValue): string {
  if (value?.kind === 'assigned') {
    const name = value.provider_name || options.value.providers.find(p => p.provider_id === value.provider_id)?.provider_name || value.provider_id
    return value.is_available === false ? t('gui.role_profiles.unavailable', { name }) : name
  }
  return t(value?.kind === 'not_used' ? 'gui.role_profiles.not_used' : 'gui.role_profiles.unset')
}
function current(id: string, stamp: number): boolean { return id === props.groupId && stamp === epoch }
function apply(data: AiRoleProfiles, id: string, stamp: number): void {
  if (current(id, stamp) && data.ai_group_id === id) rows.value = data.items
}
async function load(): Promise<void> {
  const id = props.groupId, stamp = epoch, language = locale.value
  busy.value = true
  await inProfileQueue(id, async () => {
    if (!current(id, stamp)) return
    try {
      const [data, choices] = await Promise.all([getAiRoleProfiles(id), getAiRoleProfileOptions(id, language)])
      if (!current(id, stamp)) return
      apply(data, id, stamp)
      options.value = choices
      loaded.value = true
      error.value = ''
      retryRead.value = false
    } catch (err) {
      if (current(id, stamp)) {
        error.value = err instanceof Error ? err.message : t('gui.role_profiles.load_failed')
        retryRead.value = true
      }
    } finally { if (current(id, stamp)) busy.value = false }
  })
}
async function mutate(action: (id: string) => Promise<AiRoleProfiles>): Promise<void> {
  if (!props.canEdit || busy.value || !loaded.value) return
  const id = props.groupId, stamp = epoch
  busy.value = true
  error.value = ''
  retryRead.value = false
  await inProfileQueue(id, async () => {
    if (!current(id, stamp)) return
    let committed = false
    try {
      const saved = await action(id)
      committed = true
      if (!current(id, stamp)) return
      apply(saved, id, stamp)
      // Only read after the successful write. A failed confirmation never replays a mutation.
      apply(await getAiRoleProfiles(id), id, stamp)
    } catch (err) {
      if (current(id, stamp)) {
        error.value = committed ? t('gui.role_profiles.confirm_failed') : (err instanceof Error ? err.message : t('gui.role_profiles.save_failed'))
        retryRead.value = true
      }
    } finally { if (current(id, stamp)) busy.value = false }
  })
}
function changeCell(event: Event, row: AiRoleProfile, role: RoleProfileRole): void {
  const element = event.target as HTMLSelectElement
  const next = profileValue(element.value)
  // Keep the last confirmed value visible until the server commits.
  element.value = profileValueKey(row.roles[role])
  void mutate(id => setAiRoleProfileCell(id, row.type_key, role, next))
}
function addType(event: Event): void {
  const element = event.target as HTMLSelectElement, key = element.value
  element.value = ''
  if (key) void mutate(id => addAiRoleProfile(id, key))
}
watch(() => [props.groupId, locale.value], () => {
  epoch++
  rows.value = []
  options.value = { types: [], providers: [] }
  loaded.value = false
  error.value = ''
  retryRead.value = false
  void load()
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { epoch++ })
</script>

<style scoped>
.role-profile-settings { min-width: 0; }
.role-grid-wrap { border: 1px solid #2a4863; border-radius: 10px; overflow-x: auto; background: #0b1e31; }
.role-matrix { min-width: 745px; }
.role-matrix-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 56px; padding: 9px 16px; border-bottom: 1px solid #2b4b67; background: #0d2339; box-sizing: border-box; }
.role-matrix-toolbar strong { color: #d3e5f7; font-size: 12px; font-weight: 700; }
.role-matrix-head, .role-matrix-row { display: grid; grid-template-columns: 116px repeat(3, minmax(160px, 1fr)); gap: 11px; align-items: center; padding: 0 16px; box-sizing: border-box; }
.role-matrix-head { height: 42px; background: #122b44; color: #92afc9; font-size: 10.5px; font-weight: 720; border-bottom: 1px solid #2b4b67; }
.role-matrix-row { height: 59px; border-bottom: 1px solid #203b53; }
.role-matrix-row:last-child { border-bottom: 0; }
.role-matrix-row.shared { background: linear-gradient(90deg, #173654, #122c44); border-bottom: 1px solid #37618a; }
.role-kind { display: flex; align-items: center; gap: 7px; white-space: nowrap; min-width: 0; }
.role-kind strong { font-size: 12px; font-weight: 670; overflow: hidden; text-overflow: ellipsis; }
.shared .role-kind { color: #a9d6ff; }
.role-shared-icon { font-size: 18px; }
.role-select { width: 100%; min-width: 0; height: 36px; border: 1px solid #35526c; border-radius: 8px; padding: 8px 10px; font-size: 11.4px; background: #0b192a; color: #e4effa; }
.role-select.role-inherited { color: #8ea6bf; border-color: #29465e; background-color: #0b2033; }
.shared .role-select { border-color: #447199; background-color: #102b45; color: #e7f4ff; }
.add-kind-select { width: 180px; height: 34px; border: 1px solid #3a6082; border-radius: 7px; padding: 0 11px; background: #14304c; color: #a9d8ff; font-size: 12px; }
select:disabled { opacity: .65; }
.remove-kind { margin-left: auto; width: 23px; height: 23px; border: 1px solid transparent; background: transparent; border-radius: 6px; color: #6986a2; opacity: 0; padding: 0; cursor: pointer; }
.role-matrix-row:hover .remove-kind, .remove-kind:focus-visible { opacity: 1; }
.remove-kind:hover { background: #422839; border-color: #9a4c5b; color: #ffb0bb; }
.matrix-error { margin-bottom: 10px; color: #fca5a5; font-size: 12px; }
.matrix-error button { margin-left: 8px; color: inherit; background: transparent; border: 1px solid currentColor; border-radius: 4px; }
@media (hover: none) { .remove-kind { opacity: 1; } }
</style>
