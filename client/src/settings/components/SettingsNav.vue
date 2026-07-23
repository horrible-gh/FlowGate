<template><nav class="settings-nav">
<div class="nav-group">{{ t('settings.nav.account') }}</div><RouterLink to="/settings/security" class="nav-item" active-class="active"><AppIcon name="shield-checkered" /> {{ t('settings.nav.security') }}</RouterLink>
<template v-if="auth.user?.is_admin"><div class="nav-divider"></div><div class="nav-group">{{ t('settings.nav.system') }}</div>
<RouterLink to="/settings/system" class="nav-item" active-class="active"><AppIcon name="sliders-horizontal" /> {{ t('settings.system.title') }}</RouterLink>
<RouterLink to="/settings/system/env-vars" class="nav-item" active-class="active"><AppIcon name="code" /> {{ t('settings.system.env_variables.title') }}</RouterLink>
<RouterLink to="/settings/system/commands" class="nav-item" active-class="active"><AppIcon name="terminal" /> {{ t('settings.system.commands.title') }}</RouterLink>
<RouterLink to="/settings/system/ai" class="nav-item" active-class="active"><AppIcon name="robot" /> {{ t('settings.system.ai.title') }}</RouterLink>
<RouterLink to="/settings/projects" class="nav-item" active-class="active"><AppIcon name="grid-four" /> {{ t('main.nav.project_list') }}</RouterLink></template>
<template v-if="auth.can('system.user.read') || auth.can('project.settings.read')"><div class="nav-divider"></div><div class="nav-group">{{ t('settings.nav.users') }}</div>
<RouterLink to="/settings/project" class="nav-item" exact-active-class="active"><AppIcon name="wrench" /> {{ t('settings.project.title') }}</RouterLink>
<RouterLink to="/settings/project/groups" class="nav-item" active-class="active"><AppIcon name="stack" /> {{ t('settings.project.group_management') }}</RouterLink>
<RouterLink to="/settings/users" class="nav-item" active-class="active"><AppIcon name="users" /> {{ t('settings.users.title') }}</RouterLink></template></nav></template>
<script setup>import {useI18n} from 'vue-i18n';import {RouterLink} from 'vue-router';import {useAuthStore} from '../stores/auth.js';import AppIcon from '@shared/AppIcon.vue';const {t}=useI18n();const auth=useAuthStore();</script>
