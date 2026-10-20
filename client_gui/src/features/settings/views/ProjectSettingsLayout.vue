<template>
  <SettingsShell scope="project">
    <template #nav>
      <SettingsSubnav :items="sectionNav" :active="section ?? ''">
        <template #head>
          <div class="proj-pick">
            <div v-if="projects && projects.length" data-card="project-picker">
              <label for="settings-project-select">{{ t('gui.settings.projects.picker') }}</label>
              <select
                id="settings-project-select"
                class="fgs-input"
                :value="selected ? selected.project_id : ''"
                data-testid="project-select"
                @change="choose(($event.target as HTMLSelectElement).value)"
              >
                <option v-if="!selected" value="" disabled>{{ t('gui.settings.projects.choose') }}</option>
                <option v-for="p in options" :key="p.project_id" :value="p.project_id">{{ optionLabel(p) }}</option>
              </select>
              <label class="fgs-check">
                <input v-model="includeInactive" type="checkbox" data-testid="project-include-inactive" />
                {{ t('gui.settings.projects.include_inactive', { n: inactiveCount }) }}
              </label>
            </div>
            <button type="button" class="fgs-link" :disabled="loading" data-testid="projects-refresh" @click="refresh">
              <SettingsIcon name="reset" size="sm" />{{ t('gui.settings.projects.refresh') }}
            </button>
          </div>
        </template>
      </SettingsSubnav>
    </template>

    <!-- 프로젝트를 고르기 전 · 목록을 못 읽었을 때 · 찾을 수 없을 때만. 고른 뒤에는 그 구역 화면만 보인다. -->
    <section v-if="!selected" class="fgs-panel" data-panel="project-select">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="folder" size="lg" /></span>
        <div class="fgs-ptitle">
          <h2>{{ t('gui.settings.project_nav.select') }}</h2>
          <p>{{ t('gui.settings.page_sub.project_select') }}</p>
        </div>
      </div>

      <div class="fgs-panel-body">
        <LoadingState v-if="loading" :label="t('gui.common.loading')" data-testid="projects-loading" />
        <ErrorState v-else-if="error" :title="errorTitle(error)" :message="error.message" data-testid="projects-error">
          <template #action>
            <Button variant="secondary" data-testid="projects-retry" @click="refresh">{{ t('gui.common.retry') }}</Button>
          </template>
        </ErrorState>
        <Alert v-else-if="lost" tone="warning" data-testid="projects-session-lost">{{ t('gui.settings.projects.session_lost') }}</Alert>
        <template v-else-if="projects">
          <Alert
            v-if="removedId"
            class="project-removed"
            tone="warning"
            dismissible
            :dismiss-label="t('gui.settings.notice_close')"
            data-testid="project-removed"
            @dismiss="removedId = null"
          >
            {{ t('gui.settings.projects.removed', { id: removedId }) }}
          </Alert>
          <EmptyState v-if="!projects.length" :title="t('gui.settings.projects.empty')" data-testid="projects-empty" />
          <EmptyState
            v-else-if="routeProjectId"
            :title="t('gui.settings.projects.not_found')"
            :description="t('gui.settings.projects.not_found_id', { id: routeProjectId })"
            data-testid="project-not-found"
          />
          <template v-else>
            <EmptyState :title="t('gui.settings.projects.select_hint')" data-testid="project-select-hint" />
            <p class="fgs-hintline filter-hint" data-testid="project-filter-hint">{{ t('gui.settings.projects.filter_hint') }}</p>
          </template>
        </template>
      </div>
    </section>

    <RouterView v-else v-slot="{ Component }">
      <component :is="Component" :key="selected.project_id" :project="selected" />
    </RouterView>
  </SettingsShell>
</template>

<script setup lang="ts">
// 프로젝트 선택 영역 (flowgate.v02.0076 T#5, D §1.2 / §2.3): the parent of /settings/project and
// /settings/project/:projectId/{general,paths,git}, shown to system administrators only (the access
// guard and SettingsLayout decide that before this mounts). It reads the project list with the
// existing GET /projects?status=all once per entry (../components/useProjectList) and keeps it
// while the person moves between projects and sections.
//
// - Screen (mockup ⑩, flowgate.v02.0077): the picker and 다시 불러오기 sit in the navigation above
//   the project's sections; the 프로젝트 선택 panel is shown only while no listed project is open
//   (hint, loading, failure, 찾을 수 없음), so a section is no longer stacked under a second panel.
//   The picker names projects by their name; the ID stays in the URL.
// - The picker shows active projects by default; 비활성 포함 adds the inactive ones. This filter is
//   the screen's display rule only — the server's list is not a permission filter (D §0.2).
// - No project in the URL: a selection hint, and no detail GET.
// - A projectId in the URL is checked against the list's IDs. One that is not there is shown as
//   찾을 수 없음 and no detail GET is sent for it. This is navigation / error handling, not a
//   permission check — the server's checks stay what they are.
// - Only a listed project mounts its section view, keyed by the project: switching project unmounts
//   the previous view, so its answers still out are dropped and nothing of it is shown for the next.
// - 다시 불러오기 repeats the list GET only. When the selected project is gone from the new list, the
//   selection and its section view are cleared (replaced onto /settings/project with a notice).
// - A failed list (401 / 403 / 500 …) is shown as such — never as an empty list. Leaving, signing
//   out or losing the administrator role drops the list and every answer still out.
// - A token gone while this screen stays (found by any project view, before a request, on an answer
//   or on the periodic check) drops the list, the picker and the section view together, with a
//   notice; 다시 불러오기 / 다시 시도 / choosing a project then sends nothing (useLiveRequests).
import { computed, onMounted, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Alert from '@/components/ui/Alert.vue'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import type { SubnavItem } from '../components/SettingsSubnav.vue'
import { useProjectList, type ReadError } from '../components/useProjectList'
import type { SettingsIconName } from '../icons'
import type { ProjectRow } from '../types'

type Section = 'general' | 'paths' | 'git'

const SECTIONS: { key: Section; icon: SettingsIconName; labelKey: string }[] = [
  { key: 'general', icon: 'folder', labelKey: 'gui.settings.project_nav.general' },
  { key: 'paths', icon: 'storage', labelKey: 'gui.settings.project_nav.paths' },
  { key: 'git', icon: 'code', labelKey: 'gui.settings.project_nav.git' },
]
const SECTION_OF_ROUTE: Record<string, Section> = {
  'settings-project-general': 'general',
  'settings-project-paths': 'paths',
  'settings-project-git': 'git',
}

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { projects, loading, error, lost, load } = useProjectList()

const includeInactive = ref(false)
const removedId = ref<string | null>(null)

const routeProjectId = computed(() => {
  const id = route.params.projectId
  return typeof id === 'string' && id ? id : null
})
const section = computed<Section | null>(() => SECTION_OF_ROUTE[String(route.name ?? '')] ?? null)
const selected = computed<ProjectRow | null>(() =>
  (routeProjectId.value && projects.value?.find((p) => p.project_id === routeProjectId.value)) || null)

const inactiveCount = computed(() => projects.value?.filter((p) => !p.is_active).length ?? 0)
// The selected project stays in the picker even when the filter hides its state.
const options = computed(() =>
  (projects.value ?? []).filter((p) => includeInactive.value || p.is_active || p.project_id === selected.value?.project_id))

const sectionNav = computed<SubnavItem[]>(() => {
  const project = selected.value
  if (!project) return []
  const base = `/settings/project/${encodeURIComponent(project.project_id)}`
  return SECTIONS.map((s) => ({ key: s.key, label: t(s.labelKey), icon: s.icon, to: `${base}/${s.key}` }))
})

function optionLabel(p: ProjectRow): string {
  return p.is_active ? p.project_name : `${p.project_name} (${t('gui.settings.projects.inactive')})`
}

function errorTitle(err: ReadError): string {
  return t(`gui.settings.projects.errors.${err.kind}`)
}

function choose(id: string) {
  if (!id || id === selected.value?.project_id) return
  removedId.value = null
  void router.push(`/settings/project/${encodeURIComponent(id)}/${section.value ?? 'general'}`)
}

/** The list GET again; a selected project missing from the new list is cleared. */
async function refresh() {
  const had = selected.value?.project_id ?? null
  if (!(await load())) return
  const id = routeProjectId.value
  if (id && id === had && !selected.value) {
    removedId.value = id
    await router.replace('/settings/project')
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.proj-pick {
  display: grid;
  gap: 7px;
  padding: 6px 10px 8px;
}

.proj-pick [data-card='project-picker'] {
  display: grid;
  gap: 7px;
}

.proj-pick label[for] {
  font-size: 10.5px;
  color: #647e99;
  font-weight: 800;
  letter-spacing: 0.5px;
}

.proj-pick .fgs-link {
  justify-self: start;
  padding-left: 0;
}

.fgs-check {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: #9db0c4;
  font-size: 12px;
}

.project-removed {
  margin-bottom: 14px;
}

.filter-hint {
  margin: 10px 0 0;
  text-align: center;
}
</style>
