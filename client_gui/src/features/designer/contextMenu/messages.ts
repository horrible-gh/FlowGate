// flowgate.v02.0060 — display strings of the context menu (0006-D rev2 §2.4): owned by this folder and
// used through a local vue-i18n scope that follows the app locale, so the shared catalogs stay as
// they are. Labels follow the mockup; card labels come with the supplied palette definitions.
// flowgate.v02.0060 T#1 (Q item 459): a disabled item not connected says why in its tooltip and accessible
// description only — 연결 예정 (planned) or 미구현 (unimplemented).

export const contextMenuMessages = {
  ko: {
    baseBranch: '베이스 브랜치',
    menu: '메뉴', newGroup: '새 그룹', projectInfo: '프로젝트 정보', newFlow: '새 플로우',
    rename: '이름 변경', info: '정보', delete: '삭제', open: '열기', newFolder: '새 폴더',
    newFile: '새 파일', download: '다운로드', upload: '업로드', git: 'Git', changes: '수정 확인',
    revert: '되돌리기', history: '이력', close: '닫기', closeOthers: '다른 탭 닫기', closeAll: '전체 닫기',
    addCard: '카드 추가', paste: '붙여넣기', selectAll: '전체 선택', arrangeLayout: '정렬 · 배치',
    detail: '상세 보기', copy: '복사', editSettings: '공통설정 편집', arrange: '정렬', disconnect: '연결 해제',
    reasons: { unlinked: 'Git 미연동', unavailable: 'Git 상태를 읽을 수 없음', 'no-worktree': '워크트리 없음', denied: 'Git 읽기 권한 없음', planned: '연결 예정', unimplemented: '미구현' },
  },
  en: {
    baseBranch: 'Base branch',
    menu: 'Menu', newGroup: 'New group', projectInfo: 'Project information', newFlow: 'New flow',
    rename: 'Rename', info: 'Information', delete: 'Delete', open: 'Open', newFolder: 'New folder',
    newFile: 'New file', download: 'Download', upload: 'Upload', git: 'Git', changes: 'View changes',
    revert: 'Revert', history: 'History', close: 'Close', closeOthers: 'Close other tabs', closeAll: 'Close all tabs',
    addCard: 'Add card', paste: 'Paste', selectAll: 'Select all', arrangeLayout: 'Arrange · Layout',
    detail: 'View details', copy: 'Copy', editSettings: 'Edit shared settings', arrange: 'Arrange', disconnect: 'Disconnect',
    reasons: { unlinked: 'Git not connected', unavailable: 'Git status unavailable', 'no-worktree': 'Worktree missing', denied: 'Git read permission denied', planned: 'Coming soon', unimplemented: 'Not implemented' },
  },
  ja: {
    baseBranch: 'ベースブランチ',
    menu: 'メニュー', newGroup: '新規グループ', projectInfo: 'プロジェクト情報', newFlow: '新規フロー',
    rename: '名前を変更', info: '情報', delete: '削除', open: '開く', newFolder: '新規フォルダー',
    newFile: '新規ファイル', download: 'ダウンロード', upload: 'アップロード', git: 'Git', changes: '変更を確認',
    revert: '元に戻す', history: '履歴', close: '閉じる', closeOthers: '他のタブを閉じる', closeAll: 'すべて閉じる',
    addCard: 'カードを追加', paste: '貼り付け', selectAll: 'すべて選択', arrangeLayout: '整列 · 配置',
    detail: '詳細を表示', copy: 'コピー', editSettings: '共通設定を編集', arrange: '整列', disconnect: '接続を解除',
    reasons: { unlinked: 'Git未接続', unavailable: 'Git状態を取得できません', 'no-worktree': 'ワークツリーなし', denied: 'Git読み取り権限なし', planned: '接続予定', unimplemented: '未実装' },
  },
}

