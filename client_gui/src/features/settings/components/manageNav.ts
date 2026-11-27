import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsMenu } from '../settingsMenu'
import type { SubnavItem } from './SettingsSubnav.vue'

/** 관리 sub-navigation (v02test.0090): 팀 and 워크스페이스 (설정), each listed once the server's capabilities
 *  allow it (../settingsMenu), so a person who may do neither never sees the scope. */
export function useManageNav() {
  const { t } = useI18n()
  const menu = useSettingsMenu()
  return computed<SubnavItem[]>(() =>
    menu.value
      .filter((page) => page.scope === 'manage')
      .map((page) => ({ key: page.key, label: t(page.labelKey), icon: page.icon, to: page.path })),
  )
}
