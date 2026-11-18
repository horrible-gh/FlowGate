// Words of the Flow attachment window (flowgate.v02gui.0030 final mockup). The window shows no text buttons
// for delete / undo: those words are only tooltips and accessible names.
type Origin = 'ai' | 'upload' | 'bug' | 'requirement' | 'chat' | 'card'

const ko = {
  uploadProgress: (name: string, pending: number) => `업로드 중: ${name} · 대기 ${pending}개`,
  title: '첨부파일', search: '파일 검색', add: '파일 추가', all: '전체 선택', allLabel: '플로우 첨부파일 전체 선택',
  count: (n: number) => `${n}개`, selected: (n: number) => `${n}개 선택`, pick: (name: string) => `${name} 선택`,
  zip: 'ZIP 다운로드', zipTitle: '선택 파일 ZIP 다운로드', preparing: '준비 중…',
  download: '다운로드', remove: '삭제', removeSelected: '선택한 파일 삭제',
  undo: '되돌리기 (Ctrl+Z)', undoMany: (n: number) => `${n}개 되돌리기 (Ctrl+Z)`,
  close: '창 닫기', empty: '파일 없음', noResult: '검색 결과 없음', loading: '불러오는 중',
  conflictTitle: '이름 중복', cancel: '취소', keep: '양쪽 보존', overwrite: '덮어쓰기',
  dropTitle: '파일 놓기', failed: '작업을 완료하지 못했습니다. 다시 시도해 주세요.', yesterday: '어제',
  origin: { ai: 'AI 첨부 승인', upload: '직접 업로드', bug: '버그 카드', requirement: '요구사항 카드', chat: '대화', card: '카드' } as Record<Origin, string>,
}
type Words = typeof ko

const en: Words = {
  uploadProgress: (name, pending) => `Uploading: ${name} · ${pending} waiting`,
  title: 'Attachments', search: 'Search files', add: 'Add files', all: 'Select all', allLabel: 'Select all Flow attachments',
  count: n => `${n} ${n === 1 ? 'file' : 'files'}`, selected: n => `${n} selected`, pick: name => `Select ${name}`,
  zip: 'ZIP download', zipTitle: 'Download the selected files as ZIP', preparing: 'Preparing…',
  download: 'Download', remove: 'Delete', removeSelected: 'Delete the selected files',
  undo: 'Undo (Ctrl+Z)', undoMany: n => `Undo ${n} (Ctrl+Z)`,
  close: 'Close window', empty: 'No files', noResult: 'No results', loading: 'Loading',
  conflictTitle: 'Name in use', cancel: 'Cancel', keep: 'Keep both', overwrite: 'Overwrite',
  dropTitle: 'Drop files', failed: 'Could not complete the operation. Please try again.', yesterday: 'Yesterday',
  origin: { ai: 'AI approval', upload: 'Uploaded', bug: 'Bug card', requirement: 'Requirement card', chat: 'Chat', card: 'Card' },
}

const ja: Words = {
  uploadProgress: (name, pending) => `アップロード中: ${name} · 待機 ${pending}件`,
  title: '添付ファイル', search: 'ファイル検索', add: 'ファイル追加', all: 'すべて選択', allLabel: 'フローの添付ファイルをすべて選択',
  count: n => `${n}件`, selected: n => `${n}件選択`, pick: name => `${name} を選択`,
  zip: 'ZIP ダウンロード', zipTitle: '選択ファイルを ZIP でダウンロード', preparing: '準備中…',
  download: 'ダウンロード', remove: '削除', removeSelected: '選択したファイルを削除',
  undo: '元に戻す (Ctrl+Z)', undoMany: n => `${n}件を元に戻す (Ctrl+Z)`,
  close: 'ウィンドウを閉じる', empty: 'ファイルなし', noResult: '検索結果なし', loading: '読み込み中',
  conflictTitle: '名前の重複', cancel: 'キャンセル', keep: '両方を保持', overwrite: '上書き',
  dropTitle: 'ファイルをドロップ', failed: '操作を完了できませんでした。再試行してください。', yesterday: '昨日',
  origin: { ai: 'AI 添付承認', upload: '直接アップロード', bug: 'バグカード', requirement: '要件カード', chat: '会話', card: 'カード' },
}

export const words: Record<'ko' | 'en' | 'ja', Words> = { ko, en, ja }
export function wordsFor(locale: string): Words {
  return words[locale as keyof typeof words] ?? words.en
}
export type { Origin, Words }
