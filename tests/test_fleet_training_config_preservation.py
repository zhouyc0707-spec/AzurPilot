"""新增培养开关默认关闭，并保留已有本地保存与调度选择。"""

import unittest

from module.config.config_updater import ConfigUpdater


class TestFleetTrainingConfigPreservation(unittest.TestCase):
    def test_new_flags_do_not_change_existing_settings(self):
        old = {
            "Alas": {"DropRecord": {"MeowfficerTalent": "save", "CloudUpload": False,
                                     "ResearchRecord": "upload", "OpsiDaily": "upload"}},
            "FleetInfo": {"FleetTraining": {"CheckIntervalHours": 0.75}},
            "OpsiShop": {"OpsiShop": {"MonthEndOnly": True}},
        }
        updated = ConfigUpdater().config_update(old)
        for name in ("StopOnComplete", "Notify"):
            self.assertIs(updated["FleetInfo"]["FleetTraining"][name], False)
        self.assertEqual(updated["FleetInfo"]["FleetTraining"]["CheckIntervalHours"], 0.75)
        for key, value in old["Alas"]["DropRecord"].items():
            self.assertEqual(updated["Alas"]["DropRecord"][key], value)
        self.assertIs(updated["OpsiShop"]["OpsiShop"]["MonthEndOnly"], True)


if __name__ == "__main__":
    unittest.main()
