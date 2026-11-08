"""单次读取季节任务并合并库存目标；不提交任务、不运行生产规划器。"""

from module.exception import RequestHumanTakeover
from module.island_planner.season_tasks import IslandSeasonTaskScanner, current_season_context
from module.logger import logger
from module.ui.page import page_island


class IslandSeasonTargetScan(IslandSeasonTaskScanner):
    """季节物品生产页的单次导入入口。

    Pages:
        in: 任意页面
        out: page_island
    """

    def run(self):
        from module.island.seasonal_task_targets import merge_imported_targets

        logger.hr('读取季节任务并导入库存目标', level=1)
        context = current_season_context()
        if context is None:
            raise RequestHumanTakeover('当前服务器开发季没有唯一且已核实的任务数据，原目标未修改')
        expected_season = context[1]
        configured_season = self.config.cross_get('IslandPlan.IslandPlan.Season', default='spring')
        if configured_season != expected_season:
            raise RequestHumanTakeover(
                f'全局季节配置为 {configured_season}，当前开发季对应 {expected_season}，请核对后重新扫描；原目标未修改')

        logger.info('[岛屿-季节目标] 只读取未提交任务，包括已满足但尚未提交的任务，不领取或提交物资')
        targets = self.scan_season_task_targets(claim_rewards=False, strict=True)
        self.ui_ensure(page_island)
        if targets is None:
            raise RequestHumanTakeover('季节任务未完整识别，原目标未修改，请查看上述识别日志后重试')
        if current_season_context() != context:
            raise RequestHumanTakeover('扫描期间开发季发生变化，原目标未修改，请重新扫描')

        try:
            changed = merge_imported_targets(self.config, targets, expected_season)
        except ValueError as exc:
            logger.error(f'[岛屿-季节目标] 导入未完成：{exc}')
            raise RequestHumanTakeover(f'季节目标导入失败，原目标未修改：{exc}') from exc
        logger.info(f'[岛屿-季节目标] 完整读取 {len(targets)} 种物品目标，更新 {len(changed)} 项设置')
        return changed
