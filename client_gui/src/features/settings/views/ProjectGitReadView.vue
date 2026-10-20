<template>
  <section class="fgs-panel" data-panel="project-git">
    <div class="fgs-phead">
      <span class="fgs-tico"><SettingsIcon name="code" size="lg" /></span>
      <h2>{{ t('gui.settings.project_nav.git') }}</h2>
      <div class="fgs-push">
        <button type="button" class="fgs-btn sm" :disabled="loading" data-testid="git-refresh" @click="load">
          <SettingsIcon name="reset" size="sm" />{{ t('gui.settings.projects.refresh') }}
        </button>
      </div>
    </div>

    <div class="fgs-panel-body">
      <LoadingState v-if="loading" :label="t('gui.common.loading')" data-testid="git-loading" />
      <ErrorState v-else-if="error" :title="t(`gui.settings.projects.errors.git_${error.kind}`)" :message="error.message" data-testid="git-error">
        <template #action>
          <Button variant="secondary" data-testid="git-retry" @click="load">{{ t('gui.common.retry') }}</Button>
        </template>
      </ErrorState>
      <template v-else-if="view">
        <EmptyState
          v-if="!view.configured"
          :title="t('gui.settings.projects.git.unconfigured')"
          :description="t('gui.settings.projects.git.unconfigured_hint')"
          data-testid="git-unconfigured"
        />
        <div v-else class="fgs-card" data-card="project-git">
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.projects.git.repo_url') }}</div>
            <div class="v mono" data-testid="git-repo-url">{{ view.config.repo_url }}</div>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.projects.git.provider') }}</div>
            <div class="v" data-testid="git-provider">{{ view.config.provider }}</div>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.projects.git.enabled') }}</div>
            <div class="v" data-testid="git-enabled">
              <span class="fgs-pill" :class="view.config.enabled ? 'ok' : 'off'">{{ t(view.config.enabled ? 'gui.settings.projects.git.on' : 'gui.settings.projects.git.off') }}</span>
            </div>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.projects.git.base_branch') }}</div>
            <div class="v mono" data-testid="git-base-branch">{{ view.config.base_branch }}</div>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.projects.git.finalize_action') }}</div>
            <div class="v" data-testid="git-finalize-action">{{ finalizeLabel(view.config.default_finalize_action) }}</div>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.projects.git.author_name') }}</div>
            <div class="v" data-testid="git-author-name">
              <template v-if="view.config.author_name !== null">{{ view.config.author_name }}</template>
              <span v-else class="fgs-muted">{{ t('gui.settings.projects.git.author_default') }}</span>
            </div>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.projects.git.author_email') }}</div>
            <div class="v" data-testid="git-author-email">
              <template v-if="view.config.author_email !== null">{{ view.config.author_email }}</template>
              <span v-else class="fgs-muted">{{ t('gui.settings.projects.git.author_default') }}</span>
            </div>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.projects.git.secret') }}</div>
            <div class="v" data-testid="git-secret">
              <span class="fgs-pill" :class="view.config.has_secret ? 'ok' : 'off'">{{ t(view.config.has_secret ? 'gui.settings.projects.git.secret_set' : 'gui.settings.projects.git.secret_unset') }}</span>
            </div>
          </div>
          <div class="fgs-line">
            <span class="fgs-muted">{{ t('gui.settings.projects.git.readonly_hint') }}</span>
          </div>
        </div>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
// 프로젝트 › Git (flowgate.v02.0076 T#5, D §2.4): GET /projects/{project_id}/git/config only.
// configured=false with config=null is 미설정 — shown apart from a failed read (401 / 403 / 500 …),
// which is never shown as 미설정. For a configured project: repository, provider, enabled, base
// branch, default finalize action, author name / email and whether a secret is registered. The
// secret itself (or its masked form) is never shown, restored or edited; there is no secret input,
// author save, merge / push / wait edit, disconnect or connection test, and the Designer's
// GitConnectWindow / DesignerGitPanel are not copied here. Git PUT / DELETE / test-connection /
// provision / reconnect / finalize are never sent. Answers of an earlier project or an ended
// session are dropped (the layout remounts this view per project).
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import SettingsIcon from '../SettingsIcon.vue'
import { getProjectGitConfig } from '../api'
import { readErrorOf, type ReadError } from '../components/useProjectList'
import { STALE, useLiveRequests } from '../components/useLiveRequests'
import type { ProjectGitConfig, ProjectGitConfigView, ProjectRow } from '../types'

const props = defineProps<{ project: ProjectRow }>()

const FINALIZE_ACTIONS: readonly string[] = ['merge', 'push', 'wait']

const { t, te } = useI18n()
const view = ref<ProjectGitConfigView | null>(null)
const loading = ref(false)
const error = ref<ReadError | null>(null)

const requests = useLiveRequests(() => {
  view.value = null
  loading.value = false
  error.value = null
})

const nullableText = (value: unknown) => value === null || typeof value === 'string'

function isConfig(value: unknown, projectId: string): value is ProjectGitConfig & { project_id: string } {
  const c = value as (Partial<ProjectGitConfig> & { project_id?: unknown }) | null
  return !!c && c.project_id === projectId && typeof c.repo_url === 'string' && typeof c.provider === 'string'
    && typeof c.enabled === 'boolean' && typeof c.base_branch === 'string' && typeof c.default_finalize_action === 'string'
    && nullableText(c.author_name) && nullableText(c.author_email) && typeof c.has_secret === 'boolean'
}

/** Only the fields this screen shows are kept; the masked secret and the rest stay out of memory. */
function toView(body: unknown, projectId: string): ProjectGitConfigView | null {
  const b = body as { configured?: unknown; config?: unknown } | null
  if (!b || typeof b.configured !== 'boolean') return null
  if (!b.configured) return b.config === null ? { configured: false, config: null } : null
  if (!isConfig(b.config, projectId)) return null
  const c = b.config
  return {
    configured: true,
    config: {
      repo_url: c.repo_url,
      provider: c.provider,
      enabled: c.enabled,
      base_branch: c.base_branch,
      default_finalize_action: c.default_finalize_action,
      author_name: c.author_name,
      author_email: c.author_email,
      has_secret: c.has_secret,
    },
  }
}

function finalizeLabel(action: string): string {
  const key = `gui.settings.projects.git.finalize.${action}`
  return FINALIZE_ACTIONS.includes(action) && te(key) ? `${t(key)} (${action})` : action
}

/** The GET (first load, 다시 불러오기, 다시 시도). It never calls another path. */
async function load() {
  // No token or no administrator access any more: nothing is sent, and every project view is ended.
  const at = requests.begin()
  if (at === null) return
  const projectId = props.project.project_id
  loading.value = true
  error.value = null
  try {
    const next = toView(await requests.live(at, getProjectGitConfig(projectId)), projectId)
    if (!next) throw new Error(t('gui.settings.projects.errors.bad_response'))
    view.value = next
  } catch (err) {
    if (err === STALE) return
    view.value = null
    error.value = readErrorOf(err)
  } finally {
    if (at === requests.current()) loading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.fgs-panel {
  margin-top: 14px;
}

.v.mono {
  font-family: var(--fg2-font-mono, ui-monospace, monospace);
  word-break: break-all;
}
</style>
