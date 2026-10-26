export const rotationZhCN = {
  'rotation.title': '智能换船-心情控制', 'rotation.enable': '启用智能换船-心情控制', 'rotation.save': '保存规则',
  'rotation.selectedOnly': '仅计算更换舰船心情',
  'rotation.emotionHelp': "默认关闭。开启后只按勾选舰位心情决定等待或换船，可确认固定舰船带来的红脸；关闭后按全队最低心情等待。两种模式都计算培养舰船，沿用任务的保持经验加成、防绿脸等要求换船。",
  'rotation.help': '仅联动普通主线和活动图。切换任务后扫描舰队，等级降序只准备一次。先核实实际心情，充足时只校正记录；候补按整队最低心情降序、同值按队号升序。在当前选船页按同号舰位互换，换后只更新对应排。同一任务连续刷图不重复入口扫描。',
  'rotation.duplicates': '舰队扫描和智能换船暂不支持区分多艘同名舰船，请避免在参与舰队中放入同名舰船；身份无法唯一确认时会停止换船。',
  'rotation.slots': '舰位设置', 'rotation.swap': '换船', 'rotation.keep': '不变', 'rotation.sources': '候补舰队（多选）',
  'rotation.source': '候补舰队 {number}', 'rotation.last': '上轮来源：第 {number} 舰队',
  'rotation.inProgress': '正在完成第 {number} 舰队的轮换', 'rotation.none': '尚未轮换',
  'rotation.uncertain': '上轮未完成，下次运行会自动清理旧记录并重扫实际编队，无需手动清空。', 'rotation.reset': '清空轮换记录', 'rotation.saved': '换船规则已保存',
}
export const rotationZhTW: typeof rotationZhCN = {
  'rotation.title': '智慧換船-心情控制', 'rotation.enable': '啟用智慧換船-心情控制', 'rotation.save': '儲存規則',
  'rotation.selectedOnly': '僅計算更換艦船心情',
  'rotation.emotionHelp': "預設關閉。開啟後只以勾選艦位心情決定等待或交替，可確認固定艦船造成的紅臉；關閉後以全隊最低心情等待。兩種模式均計算培養艦船，沿用任務的經驗加成、防綠臉等要求交替。",
  'rotation.help': '僅連動普通主線與活動關卡。切換任務後掃描艦隊，等級降冪只設定一次。先確認實際心情，足夠時只校正紀錄；候補依整隊最低心情降冪、同值依隊號升冪。在目前選船頁依相同艦位互換，換後只更新對應列。同一任務連續出擊不重複入口掃描。',
  'rotation.duplicates': '艦隊掃描與智慧換船暫不支援區分多艘同名艦船，請避免在參與艦隊中放入同名艦船；身分無法唯一確認時會停止換船。',
  'rotation.slots': '艦位設定', 'rotation.swap': '換船', 'rotation.keep': '不變', 'rotation.sources': '候補艦隊（多選）',
  'rotation.source': '候補艦隊 {number}', 'rotation.last': '上輪來源：第 {number} 艦隊',
  'rotation.inProgress': '正在完成第 {number} 艦隊的輪換', 'rotation.none': '尚未輪換',
  'rotation.uncertain': '上輪未完成，下次執行會自動清除舊紀錄並重新掃描實際編隊，無須手動清除。', 'rotation.reset': '清空輪換記錄', 'rotation.saved': '換船規則已儲存',
}
export const rotationEnUS: typeof rotationZhCN = {
  'rotation.title': 'Smart rotation - morale control', 'rotation.enable': 'Enable smart rotation - morale control', 'rotation.save': 'Save rules',
  'rotation.selectedOnly': 'Calculate morale for rotating ships only',
  'rotation.emotionHelp': "Off by default. When enabled, only selected slots control morale waiting and rotation; warnings from fixed ships may be confirmed. When disabled, wait for the whole fleet minimum. Both modes track training ships and retain task morale controls for rotation.",
  'rotation.help': 'Normal main and event stages only. Scan on task changes and prepare descending level order once. Verify actual morale, rank reserves by descending fleet minimum morale then fleet number, and swap matching slots from the current picker. Refresh only the changed row. Consecutive runs do not repeat the entry scan.',
  'rotation.duplicates': 'Fleet scanning and smart rotation do not distinguish multiple ships with the same name. Avoid duplicate names in participating fleets; an ambiguous identity stops rotation.',
  'rotation.slots': 'Fleet slots', 'rotation.swap': 'Rotate', 'rotation.keep': 'Keep', 'rotation.sources': 'Reserve fleets (multiple)',
  'rotation.source': 'Reserve fleet {number}', 'rotation.last': 'Last reserve: Fleet {number}',
  'rotation.inProgress': 'Completing rotation from Fleet {number}', 'rotation.none': 'No rotation yet',
  'rotation.uncertain': 'The previous round was incomplete. The next run automatically clears old progress and rescans the actual formation; no manual reset is needed.', 'rotation.reset': 'Clear rotation history', 'rotation.saved': 'Rotation rules saved',
}
export const rotationJaJP: typeof rotationZhCN = {
  'rotation.title': '自動艦船交代-コンディション管理', 'rotation.enable': '自動艦船交代-コンディション管理を有効化', 'rotation.save': 'ルールを保存',
  'rotation.selectedOnly': '交代対象艦のみコンディション計算',
  'rotation.emotionHelp': "初期設定は無効。有効時は選択配置のみで待機と交代を判定し、固定艦の低下警告は承認可能です。無効時は全艦隊の最低値で待機します。どちらも育成艦を計算し、交代基準はタスク設定を維持します。",
  'rotation.help': '通常のメイン・イベント海域のみ。タスク切替時にスキャンし、レベル降順は一度だけ設定します。実際のコンディションを確認し、候補は艦隊内の最低値が高い順、同値は艦隊番号順に選びます。現在の選択画面で同じ配置番号を交代し、変更した列のみ更新します。同じタスクの連続周回では入口スキャンを繰り返しません。',
  'rotation.duplicates': '艦隊スキャンと自動交代は複数の同名艦を区別できません。対象艦隊への同名艦の配置を避けてください。個体を一意に確認できない場合は交代を停止します。',
  'rotation.slots': '配置設定', 'rotation.swap': '交代', 'rotation.keep': '維持', 'rotation.sources': '候補艦隊（複数選択）',
  'rotation.source': '候補艦隊 {number}', 'rotation.last': '前回の候補：第 {number} 艦隊',
  'rotation.inProgress': '第 {number} 艦隊から交代中', 'rotation.none': '交代履歴なし',
  'rotation.uncertain': '前回の処理が未完了です。次回実行時に古い記録を自動で消去し、実際の編成を再スキャンします。手動消去は不要です。', 'rotation.reset': '交代履歴を消去', 'rotation.saved': '交代ルールを保存しました',
}
