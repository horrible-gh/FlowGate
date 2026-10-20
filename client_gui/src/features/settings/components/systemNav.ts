import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsMenu } from '../settingsMenu'
import type { SubnavItem } from './SettingsSubnav.vue'

/** 시스템 sub-navigation (시안 13, flowgate.v02.0076 T#1 D §1.1): 일반 · 시스템 Agent · 사용자 ·
 *  전체 프로젝트, each listed once its screen is registered (../settingsMenu). The retired
 *  Workspace · 저장소 · 작업 환경 · 로그 · 보안 정책 · 시스템 정보 entries are gone, and nothing is
 *  shown as a disabled "준비 중" item. */
export function useSystemNav() {
  const { t } = useI18n()
  const menu = useSettingsMenu()
  return computed<SubnavItem[]>(() =>
    menu.value
      .filter((page) => page.scope === 'system')
      .map((page) => ({ key: page.key, label: t(page.labelKey), icon: page.icon, to: page.path })),
  )
}
