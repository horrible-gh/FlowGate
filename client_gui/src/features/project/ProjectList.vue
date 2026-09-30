<template>
  <div class="project-list-wrap">
    <LoadingState v-if="loading" label="프로젝트를 불러오는 중입니다..." />
    <ErrorState
      v-else-if="error"
      title="프로젝트를 불러오지 못했습니다."
      :message="error"
    >
      <template #action>
        <Button variant="secondary" size="sm" @click="emit('retry')">다시 시도</Button>
      </template>
    </ErrorState>
    <EmptyState
      v-else-if="projects.length === 0 && !hasAnyProjects"
      title="프로젝트가 없습니다."
      description="관리자에게 새 프로젝트 생성을 요청하세요."
    />
    <EmptyState
      v-else-if="projects.length === 0"
      title="일치하는 프로젝트가 없습니다."
      description="다른 검색어로 다시 시도해 보세요."
    />
    <ul v-else class="project-list" aria-label="프로젝트 목록">
      <ProjectRow
        v-for="project in projects"
        :key="project.id"
        :project="project"
        :selected="project.id === selectedId"
        :current="project.id === currentId"
        @select="emit('select', $event)"
        @open="emit('open', $event)"
      />
    </ul>
  </div>
</template>

<script setup lang="ts">
// Active Project list surface (WP0005 T#2 §2.1/§3.1). Owns only which visual state to
// render for the list it is handed -- loading/error/empty(no projects)/empty(no search
// results)/rows -- using the existing domain-free LoadingState/ErrorState/EmptyState
// (D0004 §1.3/§4.2). `hasAnyProjects` lets ProjectDialog distinguish "no Project exists
// at all" from "search matched nothing" with two different EmptyState messages (D0004
// §3.5, T#2 §4) without this component knowing about search itself -- ProjectDialog
// already filtered `projects` before handing it here.
import Button from '../../components/ui/Button.vue'
import EmptyState from '../../components/ui/EmptyState.vue'
import ErrorState from '../../components/ui/ErrorState.vue'
import LoadingState from '../../components/ui/LoadingState.vue'
import ProjectRow from './ProjectRow.vue'
import type { Project } from './types'

defineProps<{
  projects: Project[]
  loading: boolean
  error: string | null
  hasAnyProjects: boolean
  selectedId: string | null
  currentId: string | null
}>()

const emit = defineEmits<{ retry: []; select: [project: Project]; open: [project: Project] }>()
</script>

<style scoped>
.project-list-wrap {
  /* Header/Footer stay fixed (rendered outside ModalShell's scroll container); only this
     region scrolls internally, capped by viewport height rather than the mockup's fixed
     680px (D0004 §2.2, T#2 §2). `flex: 1` also lets it fill available space whenever an
     ancestor happens to be a flex column with room to give. */
  flex: 1;
  min-height: 0;
  max-height: min(50vh, 440px);
  overflow: auto;
}

.project-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0 2px 2px 0;
  list-style: none;
}
</style>
