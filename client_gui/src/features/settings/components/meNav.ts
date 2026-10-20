import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsMenu } from '../settingsMenu'
import type { SubnavItem } from './SettingsSubnav.vue'

/** 내 설정 sub-navigation (flowgate.v02.0076 T#2, D §1.1): 계정 · 화면 and 보안 · 세션, taken from
 *  the shared settings menu (../settingsMenu) like the 시스템 one, so both follow the routes. */
export function useMeNav() {
  const { t } = useI18n()
  const menu = useSettingsMenu()
  return computed<SubnavItem[]>(() =>
    menu.value
      .filter((page) => page.scope === 'me')
      .map((page) => ({ key: page.key, label: t(page.labelKey), icon: page.icon, to: page.path })),
  )
}
