import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsMenu } from '../settingsMenu'
import type { SubnavItem } from './SettingsSubnav.vue'

/** 시스템 sub-navigation (시안 ⑩, flowgate.v02.0077; pages of 0076 T#1 D §1.1): 운영 › 일반 ·
 *  실행 자원 › 시스템 Agent · 계정 · 프로젝트 › 사용자 · 전체 프로젝트, each listed once its screen is
 *  registered (../settingsMenu) and headed by its group. The retired Workspace · 저장소 · 작업 환경 ·
 *  로그 · 보안 정책 · 시스템 정보 entries are gone, and nothing is shown as a disabled "준비 중" item. */
export function useSystemNav() {
  const { t } = useI18n()
  const menu = useSettingsMenu()
  return computed<SubnavItem[]>(() => {
    const items: SubnavItem[] = []
    let group: string | undefined
    for (const page of menu.value.filter((p) => p.scope === 'system')) {
      if (page.groupKey && page.groupKey !== group) {
        group = page.groupKey
        items.push({ key: `group:${group}`, group: t(group) })
      }
      items.push({ key: page.key, label: t(page.labelKey), icon: page.icon, to: page.path })
    }
    return items
  })
}
