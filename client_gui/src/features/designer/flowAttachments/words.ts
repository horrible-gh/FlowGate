// Words of the Flow attachment window (final mockup). The window shows no text buttons
// for delete / undo: those words are only tooltips and accessible names.
type Origin = 'ai' | 'upload' | 'bug' | 'requirement' | 'chat' | 'card'
type ErrorKind = 'delete' | 'upload' | 'download' | 'read'
type OriginChip = 'all' | 'ai' | 'upload'
type SortName = 'newest' | 'oldest' | 'name'
// 0074 TR0014: why a dragged file cannot be uploaded here (no open Flow, archived Flow, read-only).
type DropBlock = 'noFlow' | 'archived' | 'readOnly'

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
  // Red lines kept for a Flow (R3): what failed, then the server's reason or the generic `failed`.
  errorLead: { delete: n => `삭제하지 못한 파일: ${n}`, upload: n => `업로드하지 못한 파일: ${n}`, download: n => `다운로드하지 못한 파일: ${n}`, read: () => '목록을 불러오지 못했습니다' } as Record<ErrorKind, (names: string) => string>,
  cancelledNames: (names: string) => `이름이 같아 취소된 파일: ${names}`, closeError: '오류 닫기', more: (n: number) => `외 ${n}개`,
  origin: { ai: 'AI 첨부 승인', upload: '직접 업로드', bug: '버그 카드', requirement: '요구사항 카드', chat: '대화', card: '카드' } as Record<Origin, string>,
  // 0074: origin chips (counts follow the search), the order menu and the bar that comes up under the list.
  chips: { all: '전체', ai: 'AI 첨부', upload: '직접 업로드' } as Record<OriginChip, string>, chipsLabel: '출처로 거르기',
  sorts: { newest: '최신 순', oldest: '오래된 순', name: '이름 순' } as Record<SortName, string>, sortLabel: '정렬',
  barLabel: '선택한 파일 동작', clearSelection: '선택 해제',
  // 0074 TR0014: full-screen drop that cannot take the files, and a drop held while the archive state is being read.
  dropBlocked: (reason: string) => `여기에는 올릴 수 없음: ${reason}`, dropRefused: (reason: string) => `파일을 올리지 않았습니다: ${reason}`,
  dropBlockReason: { noFlow: '열린 플로우가 없습니다', archived: '보관된 플로우입니다', readOnly: '읽기 전용입니다' } as Record<DropBlock, string>,
  dropChecking: '보관 여부를 확인하는 중 · 확인되면 올립니다', dismiss: '알림 닫기',
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
  errorLead: { delete: n => `Could not delete: ${n}`, upload: n => `Could not upload: ${n}`, download: n => `Could not download: ${n}`, read: () => 'Could not load the list' },
  cancelledNames: names => `Cancelled because the name is in use: ${names}`, closeError: 'Close error', more: n => `and ${n} more`,
  origin: { ai: 'AI approval', upload: 'Uploaded', bug: 'Bug card', requirement: 'Requirement card', chat: 'Chat', card: 'Card' },
  chips: { all: 'All', ai: 'AI attached', upload: 'Uploaded' }, chipsLabel: 'Filter by origin',
  sorts: { newest: 'Newest first', oldest: 'Oldest first', name: 'By name' }, sortLabel: 'Sort',
  barLabel: 'Selected file actions', clearSelection: 'Clear selection',
  dropBlocked: reason => `Cannot upload here: ${reason}`, dropRefused: reason => `Files were not uploaded: ${reason}`,
  dropBlockReason: { noFlow: 'No Flow is open', archived: 'This Flow is archived', readOnly: 'Read-only' },
  dropChecking: 'Checking whether the Flow is archived · uploads once confirmed', dismiss: 'Dismiss',
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
  errorLead: { delete: n => `削除できなかったファイル: ${n}`, upload: n => `アップロードできなかったファイル: ${n}`, download: n => `ダウンロードできなかったファイル: ${n}`, read: () => '一覧を読み込めませんでした' },
  cancelledNames: names => `名前が重複したためキャンセルされたファイル: ${names}`, closeError: 'エラーを閉じる', more: n => `ほか${n}件`,
  origin: { ai: 'AI 添付承認', upload: '直接アップロード', bug: 'バグカード', requirement: '要件カード', chat: '会話', card: 'カード' },
  chips: { all: 'すべて', ai: 'AI 添付', upload: '直接アップロード' }, chipsLabel: '出どころで絞り込み',
  sorts: { newest: '新しい順', oldest: '古い順', name: '名前順' }, sortLabel: '並べ替え',
  barLabel: '選択したファイルの操作', clearSelection: '選択解除',
  dropBlocked: reason => `ここにはアップロードできません: ${reason}`, dropRefused: reason => `ファイルをアップロードしませんでした: ${reason}`,
  dropBlockReason: { noFlow: '開いているフローがありません', archived: 'アーカイブされたフローです', readOnly: '読み取り専用です' },
  dropChecking: 'アーカイブ状態を確認中 · 確認後にアップロードします', dismiss: '通知を閉じる',
}

export const words: Record<'ko' | 'en' | 'ja', Words> = { ko, en, ja }
export function wordsFor(locale: string): Words {
  return words[locale as keyof typeof words] ?? words.en
}
export type { DropBlock, Origin, Words }

