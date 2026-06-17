import { createApp } from 'vue'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import '@shared/variables.css'
import '@shared/login.css'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.use(i18n)
app.mount('#app')
