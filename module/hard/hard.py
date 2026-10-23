"""困难模式战役执行模块。

自动执行碧蓝航线的困难模式关卡。困难模式与普通模式共享同一张地图，
但使用独立的战役入口和额外的限制条件（如每日出击次数、舰队锁定等）。
通过 OCR 识别剩余出击次数，循环执行直到用尽。

配置路径: Hard.HardStage (关卡选择), Hard.HardFleet (舰队选择), Hard.RunAt18 (维护日18点运行)
"""

import importlib
from datetime import timedelta

from campaign.campaign_hard.campaign_hard import Campaign
from module.campaign.run import CampaignRun
from module.config.time_source import now as current_time
from module.config.transaction import config_transaction
from module.config.utils import filepath_config, server_time_offset
from module.handler.fast_forward import to_map_file_name
from module.hard.assets import *
from module.hard.schedule import hard_run_time
from module.logger import logger
from module.ocr.ocr import Digit

OCR_HARD_REMAIN = Digit(OCR_HARD_REMAIN, letter=(123, 227, 66), threshold=128, alphabet='0123')


class CampaignHard(CampaignRun):
    """困难模式战役执行器。

    继承自 CampaignRun，负责执行困难模式关卡。从普通模式战役加载地图数据，
    强制启用舰队锁定和自动搜索，通过 OCR 识别每日剩余出击次数并循环执行。

    Attributes:
        equipment_has_take_on: 装备是否已穿戴（当前未使用）。
        campaign: 战役执行实例，由 CampaignRun 提供。
    """

    equipment_has_take_on = False
    campaign: Campaign

    def _schedule(self, completed=False):
        """短事务内按最新维护设置判断门禁或安排次日，避免覆盖运行中的设置修改。"""
        with config_transaction(filepath_config(self.config.config_name)):
            self.config.update()
            next_run = self.config.cross_get('Hard.Scheduler.NextRun')
            if completed:
                if next_run != self._hard_next_run:
                    logger.info('[困难战役] 执行期间排程已修改，保留最新设置')
                    return False
            else:
                self._hard_next_run = next_run
            weekday = self.config.cross_get(
                'Exercise.Exercise.MaintenanceWeekday', default=self.config.Exercise_MaintenanceWeekday
            )
            offset = server_time_offset()
            server_now = current_time() - offset
            day = server_now + timedelta(days=1) if completed else server_now
            target = hard_run_time(day, self.config.Hard_RunAt18, weekday)
            if completed or server_now < target:
                self.config.task_delay(target=target + offset)
                return True
        return False

    def run(self):
        """执行困难关卡出击主流程。

        流程包括：覆写困难模式配置、加载战役与地图模块、导航至困难关卡界面、
        OCR 识别剩余次数并循环出击，维护星期可延后至18点，其余日期按服务器零点安排。

        Pages:
            in: 任意页面
            out: 延后时保留当前页面，否则为 page_campaign 或关卡选择界面
        """
        logger.hr('困难战役', level=1)
        if self._schedule():
            self.config.task_stop()

        name = to_map_file_name(self.config.Hard_HardStage)
        # Hard.HardFleet 指定出击舰队，另一支在基地待命、不参与战斗。
        # Fleet_FleetOrder 与该选择一一对应，编队准备时会据此只校验出击舰队的困难限制
        # （见 module/map/map_fleet_preparation.py），不再强制要求两支舰队都满足困难限制。
        self.config.override(
            Campaign_Mode='hard',
            Campaign_UseFleetLock=True,
            Campaign_UseAutoSearch=True,
            Fleet_FleetOrder='fleet1_all_fleet2_standby' if self.config.Hard_HardFleet == 1 else 'fleet1_standby_fleet2_all',
            Emotion_Mode='nothing',  # 不计算也不忽略
        )
        # 装备穿戴
        # campaign/campaign_hard/campaign_hard.py Campaign.fleet_preparation()

        # 初始化
        self.load_campaign(name='campaign_hard', folder='campaign_hard')  # 加载战役文件
        module = importlib.import_module('.' + name, 'campaign.campaign_main')  # 从普通模式加载地图
        self.campaign.MAP = module.MAP

        # UI 确认
        self.device.screenshot()
        self.campaign.device.image = self.device.image
        self.campaign.ensure_campaign_ui(
            name=self.config.Hard_HardStage,
            mode='hard'
        )

        # 执行
        remain = OCR_HARD_REMAIN.ocr(self.device.image)
        logger.attr('剩余次数', remain)
        for n in range(remain):
            self.campaign.run()

        self.campaign.ensure_auto_search_exit()
        # self.campaign.equipment_take_off_when_finished()

        # 调度器
        self._schedule(completed=True)
        self.config.task_call('Reward', force_call=False)
