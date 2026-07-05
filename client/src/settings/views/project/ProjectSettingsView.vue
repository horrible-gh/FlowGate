<template>
  <div>
    <div class="flex justify-between items-center" style="margin-bottom:20px;">
      <div>
        <h1 class="s-page-title">{{ $t('settings.project.title') }}</h1>
        <p class="s-page-sub" style="margin-bottom:0;">{{ $t('settings.project.project_settings_view.subtitle_6') }}</p>
      </div>
      <div class="flex items-center gap-2">
        <label class="text-sm text-s">{{ $t('settings.project.project_settings_view.label_9') }}</label>
        <select
          class="form-ctrl"
          style="width:180px;"
          :value="settings.currentProjectId"
          @change="settings.setCurrentProject($event.target.value)"
        >
          <option v-for="p in settings.projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
        </select>
      </div>
    </div>

    <div class="tab-nav">
      <div
        v-for="tab in tabs"
        :key="tab.id"
        class="tab-nav-item"
        :class="{ active: activeTab === tab.id }"
        @click="selectTab(tab.id)"
      >
        <i :class="`fa-solid ${tab.icon}`"></i> {{ tab.label }}
      </div>
    </div>

    <component :is="activeComponent" v-if="settings.currentProjectId" />
    <div v-else class="alert alert-info">
      <i class="fa-solid fa-circle-info"></i> {{ $t('settings.project.project_settings_view.text_35') }}
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { useSettingsStore } from '../../stores/settings.js';
import PathSettingsView from './PathSettingsView.vue';
import NumberingSettingsView from './NumberingSettingsView.vue';
import MessagesView from './MessagesView.vue';
import SourceModeSettingsView from './SourceModeSettingsView.vue';
import GitSettingsView from './GitSettingsView.vue';

const { t } = useI18n();
const settings = useSettingsStore();
const route = useRoute();
const router = useRouter();

const tabs = computed(() => [
  { id: 'paths', label: t('settings.project.path'), icon: 'fa-folder-tree', component: PathSettingsView },
  { id: 'source-mode', label: t('settings.project.source_mode.tab'), icon: 'fa-plug', component: SourceModeSettingsView },
  { id: 'numbering', label: t('settings.project.project_settings_view.text_56'), icon: 'fa-hashtag', component: NumberingSettingsView },
  { id: 'messages', label: t('settings.project.messages'), icon: 'fa-comment-dots', component: MessagesView },
  { id: 'git', label: t('settings.project.git.tab'), icon: 'fa-code-branch', component: GitSettingsView },
]);

const validTabIds = new Set(['paths', 'source-mode', 'numbering', 'messages', 'git']);
const defaultTab = 'paths';
const activeTab = ref(validTabIds.has(route.query.tab) ? route.query.tab : defaultTab);
const activeComponent = computed(() => tabs.value.find((tab) => tab.id === activeTab.value)?.component || PathSettingsView);

function selectTab(tabId) {
  activeTab.value = tabId;
  router.replace({ path: '/settings/project', query: tabId === defaultTab ? {} : { tab: tabId } });
}

watch(() => route.query.tab, (tabId) => {
  activeTab.value = validTabIds.has(tabId) ? tabId : defaultTab;
});
</script>
