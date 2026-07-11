<template>
  <header class="app-header">
    <a href="/main" class="header-brand" style="min-width:auto;">
      <div class="brand-icon">FG</div>
      <span class="brand-name">FlowGate</span>
      <span class="brand-ver">v0.1</span>
    </a>

    <div class="proj-sw-wrap">
      <button class="proj-sw" type="button" @click.stop="projectMenuOpen = !projectMenuOpen">
        <span class="proj-sw-dot" :style="{ background: currentProjectColor }"></span>
        <span>{{ currentProjectName }}</span>
        <AppIcon name="caret-down" class="proj-sw-caret" :class="{ open: projectMenuOpen }" />
      </button>
      <div v-if="projectMenuOpen" class="proj-dd">
        <div class="proj-dd-hd">{{ t('main.nav.project_menu') }}</div>
        <button
          v-for="project in settings.projects"
          :key="project.project_id"
          type="button"
          class="proj-dd-item"
          :class="{ active: project.project_id === settings.currentProjectId }"
          @click="selectProject(project.project_id)"
        >
          <span class="proj-dd-dot" :style="{ background: projectColor(project) }"></span>
          <span>{{ project.project_name }}</span>
          <AppIcon name="check" v-if="project.project_id === settings.currentProjectId" style="margin-left:auto; font-size:.65rem; opacity:.5;" />
        </button>
        <template v-if="auth.user?.is_admin">
          <div class="proj-dd-div"></div>
          <a class="proj-dd-item" href="/settings/projects">
            <AppIcon name="grid-four" style="width:14px;text-align:center;color:rgba(255,255,255,.4);" />
            <span>{{ t('main.nav.project_list') }}</span>
          </a>
          <a class="proj-dd-item" href="/settings/projects?new=1">
            <AppIcon name="plus" style="width:14px;text-align:center;color:rgba(255,255,255,.4);" />
            <span>{{ t('main.nav.new_project') }}</span>
          </a>
        </template>
      </div>
    </div>

    <div class="header-spacer"></div>
    <nav class="header-nav">
      <div class="lang-sw">
        <button
          v-for="lang in ['ko', 'en', 'ja']"
          :key="lang"
          type="button"
          class="lang-btn"
          :class="{ active: locale === lang }"
          @click="setLocale(lang)"
        >{{ lang.toUpperCase() }}</button>
      </div>
      <div class="hdr-div"></div>
      <a href="/main" class="hdr-btn"><AppIcon name="house" /> {{ t('nav.dashboard') }}</a>
      <a href="/settings" class="hdr-btn active"><AppIcon name="gear" /><span>{{ t('nav.settings') }}</span></a>
      <div class="hdr-div"></div>
      <div class="hdr-user"><div class="user-av">{{ userInitial }}</div>{{ username }}</div>
      <button type="button" class="hdr-btn" @click="logout">
        <AppIcon name="sign-out" /><span>{{ t('common.logout') }}</span>
      </button>
    </nav>
  </header>
</template>

<script setup>
import AppIcon from '@shared/AppIcon.vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAuthStore } from '../stores/auth.js';
import { useSettingsStore } from '../stores/settings.js';
import { serverLogout } from '@shared/api';

const { t, locale } = useI18n();
const auth = useAuthStore();
const settings = useSettingsStore();
const projectMenuOpen = ref(false);
const projectColors = ['#2563eb', '#7c3aed', '#0891b2', '#16a34a', '#d97706'];

const currentProject = computed(() => settings.projects.find((p) => p.project_id === settings.currentProjectId));
const currentProjectName = computed(() => currentProject.value?.project_name || t('main.nav.project_select'));
const currentProjectColor = computed(() => currentProject.value ? projectColor(currentProject.value) : '#2563eb');
const username = computed(() => auth.user?.username || auth.user?.display_name || 'admin');
const userInitial = computed(() => username.value.charAt(0).toUpperCase());

function projectColor(project) {
  const index = settings.projects.findIndex((p) => p.project_id === project.project_id);
  return project.color || projectColors[Math.max(index, 0) % projectColors.length];
}

function selectProject(projectId) {
  settings.setCurrentProject(projectId);
  projectMenuOpen.value = false;
}

function setLocale(lang) {
  locale.value = lang;
  localStorage.setItem('preferred_locale', lang);
}

function closeProjectMenu() {
  projectMenuOpen.value = false;
}

async function logout() {
  try {
    await serverLogout();
  } finally {
    auth.clearAuth();
    window.location.href = '/index.html';
  }
}

onMounted(() => document.addEventListener('click', closeProjectMenu));
onBeforeUnmount(() => document.removeEventListener('click', closeProjectMenu));
</script>
