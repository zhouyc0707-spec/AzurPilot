/** 旧版统计页的操作文案。 */
export const legacyStatsZhCN = {
  'legacyStats.sectionOrder': '板块排序',
  'legacyStats.apTrend': '体力变化',
  'legacyStats.openScreenshotFolder': '打开{item}截图文件夹',
  'legacyStats.openScreenshotFolderHint': '在脚本所在电脑打开 {month} 的{item}截图文件夹',
  'legacyStats.screenshotMonthMissing': '{month} 暂无{item}截图，已打开分类文件夹：{path}',
  'legacyStats.screenshotFolderMissing': '暂无{item}截图文件夹：{path}',
} as const

type LegacyStatsKeys = keyof typeof legacyStatsZhCN

export const legacyStatsEnUS: Record<LegacyStatsKeys, string> = {
  'legacyStats.sectionOrder': 'Reorder sections',
  'legacyStats.apTrend': 'Action point trend',
  'legacyStats.openScreenshotFolder': 'Open {item} screenshot folder',
  'legacyStats.openScreenshotFolderHint': 'Open {item} screenshots for {month} on the computer running the script',
  'legacyStats.screenshotMonthMissing': 'No {item} screenshots for {month}; opened the category folder: {path}',
  'legacyStats.screenshotFolderMissing': 'No {item} screenshot folder: {path}',
}

export const legacyStatsJaJP: Record<LegacyStatsKeys, string> = {
  'legacyStats.sectionOrder': 'セクションの並べ替え',
  'legacyStats.apTrend': '行動力の推移',
  'legacyStats.openScreenshotFolder': '{item}のスクリーンショットフォルダーを開く',
  'legacyStats.openScreenshotFolderHint': 'スクリプトを実行しているパソコンで {month} の{item}スクリーンショットフォルダーを開く',
  'legacyStats.screenshotMonthMissing': '{month} の{item}スクリーンショットがないため、分類フォルダーを開きました：{path}',
  'legacyStats.screenshotFolderMissing': '{item}のスクリーンショットフォルダーがありません：{path}',
}

export const legacyStatsZhTW: Record<LegacyStatsKeys, string> = {
  'legacyStats.sectionOrder': '調整區塊順序',
  'legacyStats.apTrend': '體力變化',
  'legacyStats.openScreenshotFolder': '開啟{item}截圖資料夾',
  'legacyStats.openScreenshotFolderHint': '在執行腳本的電腦上開啟 {month} 的{item}截圖資料夾',
  'legacyStats.screenshotMonthMissing': '{month} 暫無{item}截圖，已開啟分類資料夾：{path}',
  'legacyStats.screenshotFolderMissing': '暫無{item}截圖資料夾：{path}',
}

export const legacyStatsZhMiao: Record<LegacyStatsKeys, string> = {
  ...legacyStatsZhCN,
  'legacyStats.sectionOrder': '板块排序喵',
  'legacyStats.openScreenshotFolder': '打开{item}截图文件夹喵',
  'legacyStats.openScreenshotFolderHint': '在脚本所在电脑打开 {month} 的{item}截图文件夹喵',
}
