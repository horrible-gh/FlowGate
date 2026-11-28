// flowgate.v02test.0053 1-D — the work card's `검증` line strings (시안 v15, http://mirageglass.stg/v/qxzmvtjk/v15/selfcheck.html),
// merged under `gui.inspector.verification`. The line carries no sentences: hints are button tooltips only, except the
// `종료 실패` line's short reason (flowgate.v02test.0084 #13) and the [끝난 것으로 처리] dialog.
export default {
  ko: {
    name: '검증',
    state: { overdue: '시간 초과', waiting: '대기', stopped: '종료됨', cancelled: '취소됨', stop_failed: '종료 실패' },
    button: { force_stop: '강제 종료', retry_stop: '다시 종료', mark_ended: '끝난 것으로 처리', cancel_waiting: '취소' },
    hint: {
      force_stop: '테스트 프로세스를 종료하고 실패로 기록합니다',
      retry_stop: '남은 테스트 프로세스를 다시 종료합니다',
      mark_ended: '프로세스를 더 확인하지 않고 이 검증을 끝난 것으로 기록합니다',
      cancel_waiting: '대기 중인 검증 요청을 취소합니다. 횟수는 차감되지 않습니다',
    },
    reason: {
      host_changed: '서버가 바뀌어 확인할 수 없음',
      processes_remain: '남은 프로세스가 있음',
      owner_alive: '검증 작업이 아직 끝나지 않음',
      unknown: '종료를 확인하지 못함',
    },
    markEnded: {
      title: '끝난 것으로 처리할까요?',
      message: '되돌릴 수 없습니다. 테스트 프로세스가 아직 남아 있을 수 있습니다. 이 검증은 끝난 것으로 기록되고 다음 검증을 시작할 수 있게 됩니다.',
    },
    confirm: '확인',
    dismiss: '취소',
    go: '앞 카드로 이동: {label}',
    ahead: '앞 카드: {label}',
  },
  en: {
    name: 'Verification',
    state: { overdue: 'Timed out', waiting: 'Waiting', stopped: 'Stopped', cancelled: 'Cancelled', stop_failed: 'Stop failed' },
    button: { force_stop: 'Force stop', retry_stop: 'Stop again', mark_ended: 'Mark as ended', cancel_waiting: 'Cancel' },
    hint: {
      force_stop: 'Stops the test processes and records the run as failed',
      retry_stop: 'Stops the remaining test processes again',
      mark_ended: 'Records this verification as ended without checking the processes again',
      cancel_waiting: 'Cancels the waiting verification request. It does not use up a run',
    },
    reason: {
      host_changed: 'Cannot check: the server changed',
      processes_remain: 'Processes remain',
      owner_alive: 'The verification job has not ended',
      unknown: 'Could not confirm the stop',
    },
    markEnded: {
      title: 'Mark as ended?',
      message: 'This cannot be undone. Test processes may still remain. The verification is recorded as ended and the next one can start.',
    },
    confirm: 'Confirm',
    dismiss: 'Back',
    go: 'Go to the card ahead: {label}',
    ahead: 'Card ahead: {label}',
  },
  ja: {
    name: '検証',
    state: { overdue: '時間超過', waiting: '待機中', stopped: '終了済み', cancelled: 'キャンセル済み', stop_failed: '終了失敗' },
    button: { force_stop: '強制終了', retry_stop: '再終了', mark_ended: '終了扱いにする', cancel_waiting: 'キャンセル' },
    hint: {
      force_stop: 'テストプロセスを終了し、失敗として記録します',
      retry_stop: '残っているテストプロセスをもう一度終了します',
      mark_ended: 'プロセスをこれ以上確認せず、この検証を終了として記録します',
      cancel_waiting: '待機中の検証リクエストをキャンセルします。回数は減りません',
    },
    reason: {
      host_changed: 'サーバーが変わったため確認できません',
      processes_remain: 'プロセスが残っています',
      owner_alive: '検証ジョブがまだ終わっていません',
      unknown: '終了を確認できません',
    },
    markEnded: {
      title: '終了扱いにしますか？',
      message: '元に戻せません。テストプロセスがまだ残っている可能性があります。この検証は終了として記録され、次の検証を開始できるようになります。',
    },
    confirm: '確認',
    dismiss: '戻る',
    go: '前のカードへ移動: {label}',
    ahead: '前のカード: {label}',
  },
}
