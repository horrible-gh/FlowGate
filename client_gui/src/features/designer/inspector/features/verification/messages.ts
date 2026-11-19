// flowgate.v02test.0053 1-D — the work card's `검증` line strings (시안 v15, http://mirageglass.stg/v/qxzmvtjk/v15/selfcheck.html),
// merged under `gui.inspector.verification`. The line itself carries no sentences: hints are button tooltips only.
export default {
  ko: {
    name: '검증',
    state: { overdue: '시간 초과', waiting: '대기', stopped: '종료됨', cancelled: '취소됨', stop_failed: '종료 실패' },
    button: { force_stop: '강제 종료', retry_stop: '다시 종료', cancel_waiting: '취소' },
    hint: {
      force_stop: '테스트 프로세스를 종료하고 실패로 기록합니다',
      retry_stop: '남은 테스트 프로세스를 다시 종료합니다',
      cancel_waiting: '대기 중인 검증 요청을 취소합니다. 횟수는 차감되지 않습니다',
    },
    confirm: '확인',
    dismiss: '취소',
    go: '앞 카드로 이동: {label}',
    ahead: '앞 카드: {label}',
  },
  en: {
    name: 'Verification',
    state: { overdue: 'Timed out', waiting: 'Waiting', stopped: 'Stopped', cancelled: 'Cancelled', stop_failed: 'Stop failed' },
    button: { force_stop: 'Force stop', retry_stop: 'Stop again', cancel_waiting: 'Cancel' },
    hint: {
      force_stop: 'Stops the test processes and records the run as failed',
      retry_stop: 'Stops the remaining test processes again',
      cancel_waiting: 'Cancels the waiting verification request. It does not use up a run',
    },
    confirm: 'Confirm',
    dismiss: 'Back',
    go: 'Go to the card ahead: {label}',
    ahead: 'Card ahead: {label}',
  },
  ja: {
    name: '検証',
    state: { overdue: '時間超過', waiting: '待機中', stopped: '終了済み', cancelled: 'キャンセル済み', stop_failed: '終了失敗' },
    button: { force_stop: '強制終了', retry_stop: '再終了', cancel_waiting: 'キャンセル' },
    hint: {
      force_stop: 'テストプロセスを終了し、失敗として記録します',
      retry_stop: '残っているテストプロセスをもう一度終了します',
      cancel_waiting: '待機中の検証リクエストをキャンセルします。回数は減りません',
    },
    confirm: '確認',
    dismiss: '戻る',
    go: '前のカードへ移動: {label}',
    ahead: '前のカード: {label}',
  },
}
