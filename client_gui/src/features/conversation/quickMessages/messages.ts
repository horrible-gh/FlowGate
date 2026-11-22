// Screen strings of the chat quick message row (flowgate.v02test.0072), merged under `gui.quickMessages`.
// Card 2: the row itself. Card 3: ＋ 추가 / ✎ 편집 / the add-edit window / delete confirm / failure notices.

const ko = {
  row_label: '퀵메시지',
  empty: '등록된 퀵메시지가 없습니다',
  tip_sticky: '[빨강 · 계속] {text}',
  tip_once: '[파랑 · 1회] {text}',
  add: '＋ 추가',
  edit: '✎ 편집',
  done: '✓ 완료',
  delete_item: '{title} 삭제',
  add_title: '퀵메시지 추가',
  edit_title: '퀵메시지 수정',
  field_title: '제목',
  field_title_hint: '버튼에 보이는 짧은 제목 (12자까지)',
  field_message: '보낼 문구',
  field_message_hint: '채팅을 보낼 때 메시지 뒤에 붙는 문구',
  field_kind: '종류',
  kind_once: '파랑 · 1회',
  kind_sticky: '빨강 · 계속',
  save: '저장',
  cancel: '취소',
  problem_title: '제목을 입력해 주세요.',
  problem_message: '보낼 문구를 입력해 주세요.',
  save_failed: '저장하지 못했습니다. 잠시 뒤 다시 시도해 주세요.',
  delete_heading: '퀵메시지를 삭제할까요?',
  delete_body: '"{title}" 퀵메시지를 삭제합니다. 되돌릴 수 없습니다.',
  delete_confirm: '삭제',
  delete_failed: '삭제하지 못했습니다.',
  reorder_failed: '순서를 저장하지 못해 원래대로 되돌렸습니다.',
  reorder_unrestored: '순서를 저장하지 못했고 원래대로도 되돌리지 못해 서버의 현재 순서를 보여 줍니다.',
}

const en: typeof ko = {
  row_label: 'Quick messages',
  empty: 'No quick messages yet',
  tip_sticky: '[Red · Keep on] {text}',
  tip_once: '[Blue · Once] {text}',
  add: '＋ Add',
  edit: '✎ Edit',
  done: '✓ Done',
  delete_item: 'Delete {title}',
  add_title: 'Add quick message',
  edit_title: 'Edit quick message',
  field_title: 'Title',
  field_title_hint: 'Short title on the button (up to 12 characters)',
  field_message: 'Text to send',
  field_message_hint: 'Added after your message when you send',
  field_kind: 'Type',
  kind_once: 'Blue · Once',
  kind_sticky: 'Red · Keep on',
  save: 'Save',
  cancel: 'Cancel',
  problem_title: 'Enter a title.',
  problem_message: 'Enter the text to send.',
  save_failed: 'Could not save. Please try again in a moment.',
  delete_heading: 'Delete this quick message?',
  delete_body: '"{title}" will be deleted. This cannot be undone.',
  delete_confirm: 'Delete',
  delete_failed: 'Could not delete it.',
  reorder_failed: 'Could not save the order, so it was put back.',
  reorder_unrestored: 'Could not save the order or put it back, so the row shows the order the server has now.',
}

const ja: typeof ko = {
  row_label: 'クイックメッセージ',
  empty: '登録されたクイックメッセージはありません',
  tip_sticky: '[赤 · 継続] {text}',
  tip_once: '[青 · 1回] {text}',
  add: '＋ 追加',
  edit: '✎ 編集',
  done: '✓ 完了',
  delete_item: '{title} を削除',
  add_title: 'クイックメッセージを追加',
  edit_title: 'クイックメッセージを編集',
  field_title: 'タイトル',
  field_title_hint: 'ボタンに表示する短いタイトル (12文字まで)',
  field_message: '送る文言',
  field_message_hint: '送信時にメッセージの後ろに付く文言',
  field_kind: '種類',
  kind_once: '青 · 1回',
  kind_sticky: '赤 · 継続',
  save: '保存',
  cancel: 'キャンセル',
  problem_title: 'タイトルを入力してください。',
  problem_message: '送る文言を入力してください。',
  save_failed: '保存できませんでした。しばらくしてからもう一度お試しください。',
  delete_heading: 'クイックメッセージを削除しますか？',
  delete_body: '「{title}」を削除します。元に戻せません。',
  delete_confirm: '削除',
  delete_failed: '削除できませんでした。',
  reorder_failed: '順序を保存できなかったため、元に戻しました。',
  reorder_unrestored: '順序を保存できず、元にも戻せなかったため、サーバーの現在の順序を表示しています。',
}

export const QUICK_MESSAGES_MESSAGES = { ko, en, ja } as const

/** Merges the quick message strings into the global i18n (merging again is harmless). */
export function installQuickMessages(composer: { mergeLocaleMessage(locale: string, message: Record<string, unknown>): void }): void {
  for (const locale of ['ko', 'en', 'ja'] as const) {
    composer.mergeLocaleMessage(locale, { gui: { quickMessages: QUICK_MESSAGES_MESSAGES[locale] } })
  }
}
