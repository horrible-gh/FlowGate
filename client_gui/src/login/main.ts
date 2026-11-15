import { createApp } from 'vue'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import './login.css'
import LoginApp from './LoginApp.vue'

// flowgate.v02gui.0024 (R0001 §4.2): the sign-in page is its own entry (login.html), served by
// the server at / and /login. It loads only what signing in needs — no router, no auth guard and
// none of the /app screens — and leaves for /app (or an allow-listed `next`) once signed in.
const app = createApp(LoginApp)
app.use(createPinia())
app.use(i18n)
app.mount('#login')
