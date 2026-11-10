import type {Language} from '../i18n'

const zhCN = {
  title: '当前库存进度', completed: '已完成', stock: '实际库存', inProduction: '在制',
  unknownStock: '库存待确认', unknownProduction: '在制待确认', missing: '尚未盘点', unset: '未设置目标',
  invalidTarget: '目标待修正', historical: '历史库存', historicalProduction: '历史在制',
  invalidated: '库存记录已失效，待重新盘点', loading: '正在读取库存记录…',
  unavailable: '暂时无法读取库存记录', updatedAt: '记录更新',
  help: '按最近一次库存记录计算完成量，在制数量单独列出，不计入已完成。这里不会触发游戏盘点。',
}
const enUS: typeof zhCN = {
  title: 'Current stock progress', completed: 'Completed', stock: 'Actual stock', inProduction: 'In production',
  unknownStock: 'Stock needs confirmation', unknownProduction: 'Production quantity unknown', missing: 'Not inventoried yet', unset: 'No target set',
  invalidTarget: 'Correct the target', historical: 'Previous stock', historicalProduction: 'Previous production',
  invalidated: 'Stock record is no longer valid; awaiting a new inventory check', loading: 'Reading stock records…',
  unavailable: 'Stock records are temporarily unavailable', updatedAt: 'Record updated',
  help: 'Completed quantities use the latest stock record. Items in production are shown separately and do not count as completed. This view does not start an inventory check in the game.',
}
const zhTW: typeof zhCN = {
  title: '目前庫存進度', completed: '已完成', stock: '實際庫存', inProduction: '製作中',
  unknownStock: '庫存待確認', unknownProduction: '製作中數量待確認', missing: '尚未盤點', unset: '未設定目標',
  invalidTarget: '目標待修正', historical: '歷史庫存', historicalProduction: '歷史製作中數量',
  invalidated: '庫存紀錄已失效，待重新盤點', loading: '正在讀取庫存紀錄…',
  unavailable: '暫時無法讀取庫存紀錄', updatedAt: '紀錄更新',
  help: '依最近一次庫存紀錄計算完成量，製作中數量另外列出，不計入已完成。此處不會觸發遊戲盤點。',
}
const jaJP: typeof zhCN = {
  title: '現在の在庫進捗', completed: '完了', stock: '実際の在庫', inProduction: '生産中',
  unknownStock: '在庫の確認待ち', unknownProduction: '生産中数量の確認待ち', missing: '在庫未確認', unset: '目標未設定',
  invalidTarget: '目標を修正してください', historical: '以前の在庫', historicalProduction: '以前の生産中数量',
  invalidated: '在庫記録は無効です。再確認を待っています', loading: '在庫記録を読み込み中…',
  unavailable: '在庫記録を取得できません', updatedAt: '記録更新',
  help: '最新の在庫記録から完了数量を計算します。生産中の数量は別に表示し、完了数量には含めません。この表示からゲーム内の在庫確認は実行しません。',
}

export const islandProgressText = (language: Language) => ({'zh-CN': zhCN, 'zh-MIAO': zhCN, 'zh-TW': zhTW, 'en-US': enUS, 'ja-JP': jaJP})[language]
