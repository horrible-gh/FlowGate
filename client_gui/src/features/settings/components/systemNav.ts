import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { SubnavItem } from './SettingsSubnav.vue'

/** 시스템 sub-navigation (시안 13/14): 사용자 and 보안 are this step; the other system settings
 *  (저장소, 작업 환경, 로그 / 감사, 시스템 정보) are later steps and stay inert. */
export function useSystemNav() {
  const { t } = useI18n()
  return computed<SubnavItem[]>(() => [
    { key: 'g-manage', group: t('gui.settings.system_nav.manage') },
    { key: 'users', label: t('gui.settings.users.title'), icon: 'users', to: '/settings/system/users' },
    { key: 'g-settings', group: t('gui.settings.system_nav.settings') },
    { key: 'storage', label: t('gui.settings.system_nav.storage'), icon: 'storage', disabled: true },
    { key: 'environment', label: t('gui.settings.system_nav.environment'), icon: 'swap', disabled: true },
    { key: 'logs', label: t('gui.settings.system_nav.logs'), icon: 'log', disabled: true },
    { key: 'security', label: t('gui.settings.security.title'), icon: 'shield', to: '/settings/system/security' },
    { key: 'info', label: t('gui.settings.system_nav.info'), icon: 'info', disabled: true },
  ])
}
