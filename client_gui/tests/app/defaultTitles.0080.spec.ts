/**
 * flowgate.v02test.0080: default names (새 플로우 / New Flow / 新規フロー, 새 대화 / New conversation / 新しい会話) are
 * judged in all three languages, so a name made on a screen of another language still counts as the default one.
 */
import { describe, expect, it } from 'vitest'
import { isDefaultChatTitle, isDefaultFlowName, isDefaultMessageTitle } from '@/app/i18n/defaultTitles'
import { isDefaultCardTitle } from '@/features/designer/cardDefaultTitle'

describe('default titles in every language', () => {
  it('treats the provisional Flow name of ko / en / ja as the default', () => {
    for (const name of ['새 플로우', 'New Flow', '新規フロー', '  새 플로우 ']) expect(isDefaultFlowName(name)).toBe(true)
    for (const name of ['README', '새 플로우 2', '', null, undefined]) expect(isDefaultFlowName(name)).toBe(false)
  })

  it('treats the provisional chat title of ko / en / ja as the default', () => {
    for (const title of ['새 대화', 'New conversation', '新しい会話']) expect(isDefaultChatTitle(title)).toBe(true)
    for (const title of ['첨부파일 UI 개선', '새 대화를 시작', '', null]) expect(isDefaultChatTitle(title)).toBe(false)
  })

  it('keeps the card default judgement on the same comparison', () => {
    expect(isDefaultCardTitle('New Flow', 'gui.designer.file_menu.flow_new_title')).toBe(true)
    expect(isDefaultMessageTitle('x', 'gui.no.such.key')).toBe(false)
  })
})
