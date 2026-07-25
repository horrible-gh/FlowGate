import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getRequest } from '@shared/api'

export interface Project {
  project_id: string
  project_name: string
  description?: string
  color?: string
  is_active: number
  created_at?: string
  updated_at?: string
}

type ProjectStatus = 'active' | 'all'

export const useProjectStore = defineStore('project', () => {
  const currentProjectId = ref<string | null>(
    localStorage.getItem('fg_current_project_id') || null,
  )
  const currentBranch = ref<string>(
    localStorage.getItem('fg_current_branch') || 'main',
  )
  const projects = ref<Project[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const loadedStatus = ref<ProjectStatus | null>(null)

  const activeProjects = computed(() => projects.value.filter((p) => p.is_active === 1))

  const currentProject = computed(() => {
    if (!currentProjectId.value) return null
    return projects.value.find((p) => p.project_id === currentProjectId.value) || null
  })

  async function loadProjects(status: ProjectStatus, force = false): Promise<Project[]> {
    if (!force && loadedStatus.value === status && projects.value.length > 0) return projects.value
    loading.value = true
    error.value = null
    try {
      const res = await getRequest<Project[] | { projects: Project[] }>('/api/v1/projects', { status })
      const raw = Array.isArray(res.data) ? res.data : res.data.projects || []
      projects.value = raw.filter((p) => p.project_id !== '__SYSTEM__')
      loadedStatus.value = status
      if (activeProjects.value.length > 0 && !currentProjectId.value) {
        setCurrentProject(activeProjects.value[0].project_id)
      }
      return projects.value
    } catch (e) {
      error.value = 'Failed to load projects'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchProjects(force = false): Promise<Project[]> {
    return loadProjects('active', force)
  }

  async function fetchAllProjects(force = false): Promise<Project[]> {
    return loadProjects('all', force)
  }

  function setCurrentProject(projectId: string) {
    currentProjectId.value = projectId
    currentBranch.value = 'main'
    localStorage.setItem('fg_current_project_id', projectId)
    localStorage.setItem('fg_current_branch', 'main')
  }

  function setCurrentBranch(branch: string) {
    currentBranch.value = branch
    localStorage.setItem('fg_current_branch', branch)
  }

  return {
    currentProjectId,
    currentBranch,
    projects,
    activeProjects,
    loading,
    error,
    currentProject,
    fetchProjects,
    fetchAllProjects,
    setCurrentProject,
    setCurrentBranch,
  }
})
