import { createRouter,createWebHistory } from 'vue-router';
import { useAuthStore } from '../stores/auth.js';
import SettingsLayout from '../views/SettingsLayout.vue';
import SystemSettingsView from '../views/system/SystemSettingsView.vue';
import EnvVariablesView from '../views/system/EnvVariablesView.vue';
import CommandsView from '../views/system/CommandsView.vue';
import AiSettingsView from '../views/system/AiSettingsView.vue';
import UsersView from '../views/users/UsersView.vue';
import ProjectSettingsView from '../views/project/ProjectSettingsView.vue';
import ProjectsView from '../views/projects/ProjectsView.vue';
import GroupManagementView from '../views/project/GroupManagementView.vue';
import SecuritySessionsView from '../views/SecuritySessionsView.vue';
const routes=[{path:'/settings',component:SettingsLayout,meta:{requiresAuth:true},children:[
{path:'',redirect:'/settings/security'},{path:'security',component:SecuritySessionsView},
{path:'system',component:SystemSettingsView,meta:{permission:'system.settings.manage'}},{path:'system/env-vars',component:EnvVariablesView,meta:{permission:'project.settings.read'}},{path:'system/commands',component:CommandsView,meta:{permission:'project.settings.read'}},{path:'system/ai',component:AiSettingsView,meta:{permission:'system.settings.manage'}},{path:'projects',component:ProjectsView,meta:{permission:'system.settings.manage'}},{path:'users',component:UsersView,meta:{permission:'system.user.read'}},{path:'project/types',redirect:{path:'/settings/project'}},{path:'project/path',redirect:{path:'/settings/project',query:{tab:'paths'}}},{path:'project/numbering',redirect:{path:'/settings/project',query:{tab:'numbering'}}},{path:'project/templates',redirect:{path:'/settings/project'}},{path:'project',component:ProjectSettingsView,meta:{permission:'project.settings.read'}},{path:'project/groups',component:GroupManagementView,meta:{permission:'project.settings.read'}}]},{path:'/:pathMatch(.*)*',redirect:'/settings'}];
const router=createRouter({history:createWebHistory(),routes});router.beforeEach(to=>{const auth=useAuthStore();if(to.meta.requiresAuth&&!auth.isAuthenticated){window.location.href='/index.html';return false}if(to.meta.permission&&!auth.can(to.meta.permission))return {path:'/settings/security'};return true});export default router;
