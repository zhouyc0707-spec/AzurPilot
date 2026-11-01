# 本轮上游更新清单

比较 `348b88cfd4f191714e8664de615cd7483848f830` 与 `fdfda0227df85569dbb1c10acbcb5e24bf9bbc40`。上游dev正常向前增加18个可达提交（含合并和master squash祖先登记），没有分支强制重写。最终文件树净变化54个路径：16新增、37修改、1删除；功能以最终树为准，不把祖先登记标题计成重复功能。

| 更新组 | 最终行为及主要入口 |
|---|---|
| 舰队与后宅修复 | 修复经验弹窗、楼层确认、跨滚动重复选船、卡面及人数确认；主力/先锋同筛选前七排；同队详情复用与切队等待。`module/dorm/fleet_rotation.py`、`module/retire/fleet_detail.py`、`fleet_management.py` |
| 常用候补 | 每队新增可选常用候补；普通候补不足时使用，排除占用/身份不确定/已毕业船，独立记录待确认操作。默认不启用、当前仅CN。`fleet_favourite.py`、运行时规则与前端舰队管理 |
| 培养达标停止 | `FleetTraining.StopOnComplete`默认false；本轮所有培养位实际达标后关闭当前练级任务，已达标位保留原船。`module/campaign/fleet_training.py`及前端培养页面 |
| 培养通知 | `FleetTraining.Notify`默认false；阶段、达标、换船完成与后宅/装备/换船失败通知，复用现有读数和全局渠道。新增`fleet_training_notify.py` |
| 地图回退 | 最终仅回退通用地图相机定位的前轮优化；随后修正撤回了中间对短猫扫描的扩大回退。按最终树合入，不复活已被撤销的短猫重复扫描。删除对应旧通用相机优化测试 |
| 奖励截图 | 科研一行/两行与委托物品弹窗等待渲染并重新截图，降低空白奖励截图；委托各类确认后等待短暂切换。`module/research/ui.py`、`module/commission/commission.py` |
| 智能开荒让位 | 开荒未完成时临时延后相关大世界日常任务，并对直接任务入口补保护；队列排序调整不改写原磁盘NextRun。`module/config/config.py`、`module/os/tasks/smart_explore.py`、`module/campaign/os_run.py` |

证据：[18个提交及父提交](2026-11-01-upstream-commits.tsv)、[54个净变化路径](2026-11-01-upstream-files.tsv)、[17个双方交集](2026-11-01-overlap-files.tsv)。本次没有依赖声明或锁文件变更。
