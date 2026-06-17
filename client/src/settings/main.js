import { createApp } from 'vue';
import { createPinia } from 'pinia';
import i18n from '@shared/i18n';
import '@shared/variables.css';
import '@shared/app.css';
import App from './App.vue';
import router from './router/index.js';
import { useAuthStore } from './stores/auth.js';

async function bootstrap() {
  const app = createApp(App);
  const pinia = createPinia();

  app.use(pinia);
  app.use(i18n);

  const authStore = useAuthStore(pinia);
  await authStore.initialize();

  app.use(router);
  app.mount('#app');
}

bootstrap();
