// Router-facing surface of features/settings (flowgate.v02.0076 T#1): the layout every
// /app/settings route renders in and the access guard. Kept apart from ./index so the app
// router can load each settings view lazily — ./index imports the views eagerly.
export { default as SettingsLayout } from './components/SettingsLayout.vue'
export { installSettingsAccessGuard, SETTINGS_UNKNOWN_ROUTE } from './access'
