// public surface of the open-source license window (F86): App.vue mounts `LicenseInfoWindow` once and every opener calls
// `openLicenseWindow(engineId | LICENSE_PROJECT_TAB)`.
export { default as LicenseInfoWindow } from './LicenseInfoWindow.vue'
export { default as LicenseInfoLink } from './LicenseInfoLink.vue'
export { LICENSE_PROJECT_TAB, closeLicenseWindow, licenseWindowState, openLicenseWindow, selectLicenseTab } from './licenseWindow'
