import type {Language} from '../i18n'

const zhCN = {
  read: '自动读取季节目标', reading: '正在读取季节目标…', ended: '读取进程已结束，已刷新目标；请查看日志确认识别结果。',
  failed: '读取进程异常结束，已刷新配置；请查看日志。', idle: '只读取游戏季节任务，不领取奖励；已备齐但待提交的任务也保留。导入取现有与读取数量的较大值，不自动清零。',
  running: '请先停止当前实例的任务，再读取季节目标。', disconnected: '连接恢复后可以读取季节目标。',
  invalid: '每项数量必须为非负整数。', malformed: '扫描目标格式不正确，请修正下方内容后重试。', empty: '尚未导入常规物品目标。',
}
const enUS: typeof zhCN = {
  read: 'Read seasonal targets', reading: 'Reading seasonal targets…', ended: 'The reader has stopped and targets have been refreshed. Check the log for recognition results.',
  failed: 'The reader ended with an error. Configuration has been refreshed; check the log.', idle: 'Reads seasonal tasks without claiming rewards, including items ready to submit. Imports keep the higher of the existing and scanned targets; targets are not cleared automatically.',
  running: 'Stop the current instance task before reading seasonal targets.', disconnected: 'Reconnect before reading seasonal targets.',
  invalid: 'Each target must be a non-negative integer.', malformed: 'Imported targets have an invalid format. Correct the content below and retry.', empty: 'No regular item targets have been imported yet.',
}
const zhTW: typeof zhCN = {
  read: '自動讀取季節目標', reading: '正在讀取季節目標…', ended: '讀取程序已結束，已重新載入目標；請查看日誌確認辨識結果。',
  failed: '讀取程序異常結束，已重新載入設定；請查看日誌。', idle: '只讀取遊戲季節任務，不領取獎勵；已備齊但待提交的任務也保留。匯入時保留原有與讀取數量的較大值，不自動清零。',
  running: '請先停止目前實例的任務，再讀取季節目標。', disconnected: '連線恢復後可以讀取季節目標。',
  invalid: '每項數量必須為非負整數。', malformed: '掃描目標格式不正確，請修正下方內容後重試。', empty: '尚未匯入一般物品目標。',
}
const jaJP: typeof zhCN = {
  read: '季節目標を自動読取', reading: '季節目標を読み取り中…', ended: '読取処理が終了し、目標を再読み込みしました。認識結果はログを確認してください。',
  failed: '読取処理が異常終了しました。設定を再読み込みしましたので、ログを確認してください。', idle: '報酬を受け取らずに季節任務を読み取ります。納品可能な未提出の任務も含みます。既存値と読取値の大きい方を保存し、自動で0には戻しません。',
  running: '現在のインスタンスのタスクを停止してから読み取ってください。', disconnected: '再接続後に季節目標を読み取れます。',
  invalid: '各数量は0以上の整数にしてください。', malformed: '読み込んだ目標の形式が不正です。下の内容を修正してください。', empty: '通常アイテムの目標はまだ取り込まれていません。',
}

export const islandSeasonText = (language: Language) => ({'zh-CN': zhCN, 'zh-MIAO': zhCN, 'zh-TW': zhTW, 'en-US': enUS, 'ja-JP': jaJP})[language]
