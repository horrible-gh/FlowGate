<template>
  <nav class="settings-nav">
    <template v-if="auth.user?.is_admin">
      <div class="nav-group">{{ t('settings.nav.system') }}</div>
      <RouterLink to="/settings/system" class="nav-item" active-class="active">
        <i class="fa-solid fa-sliders"></i> {{ t('settings.system.title') }}
      </RouterLink>
      <RouterLink to="/settings/system/env-vars" class="nav-item" active-class="active">
        <i class="fa-solid fa-code"></i> {{ t('settings.system.env_variables.title') }}
      </RouterLink>
      <RouterLink to="/settings/system/commands" class="nav-item" active-class="active">
        <i class="fa-solid fa-terminal"></i> {{ t('settings.system.commands.title') }}
      </RouterLink>
      <RouterLink to="/settings/projects" class="nav-item" active-class="active">
        <i class="fa-solid fa-table-cells-large"></i> {{ t('main.nav.project_list') }}
      </RouterLink>
    </template>

    <template v-if="auth.can('system.user.read') || auth.can('project.settings.read')">
      <div class="nav-divider"></div>
      <div class="nav-group">{{ t('settings.nav.users') }}</div>
      <RouterLink to="/settings/project" class="nav-item" active-class="" exact-active-class="active">
        <i class="fa-solid fa-screwdriver-wrench"></i> {{ t('settings.project.title') }}
      </RouterLink>
      <RouterLink to="/settings/project/groups" class="nav-item" active-class="active">
        <i class="fa-solid fa-layer-group"></i> {{ t('settings.project.group_management') }}
      </RouterLink>
      <RouterLink to="/settings/users" class="nav-item" active-class="active">
        <i class="fa-solid fa-users"></i> {{ t('settings.users.title') }}
      </RouterLink>
    </template>
  </nav>
</template>

<script setup>
import { useI18n } from 'vue-i18n';
import { RouterLink } from 'vue-router';
import { useAuthStore } from '../stores/auth.js';

const { t } = useI18n();
const auth = useAuthStore();
</script>
