"""使用真实临时明细库验证未知海域掉落不漏计，且不猜测侵蚀等级。"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.statistics import azurstats
from module.statistics.azurstats import AzurStats
from tests.opsi_test_support import install_store


class MeowLootMonthlyTotalsTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        install_store(self, self.directory)
        self.enterContext(patch.object(AzurStats, 'LOCAL_DB', str(Path(self.directory) / 'config' / 'azurstats_local.db')))
        self.enterContext(patch.object(azurstats, 'get_device_id', return_value='fixture-device'))

    def insert(self, hazard, item, amount=1, *, moment=None, device='fixture-device',
               instance='fixture', genre='opsi_meowfficer_farming'):
        moment = moment or datetime(2026, 10, 8, 22, 53, 4)
        return AzurStats._insert_local_opsi_items([{
            'imgid': f'{hazard}-{item}-{moment}-{device}-{instance}-{genre}',
            'server': 'cn', 'zone': '', 'zone_type': 'UNKNOWN', 'zone_id': 0,
            'hazard_level': hazard, 'item': item, 'amount': amount, 'tag': 'scan',
            'device_id': device, 'instance': instance, 'genre': genre,
            'combat_count': 18, 'created_at': int(moment.timestamp()),
        }])

    def totals(self, **kwargs):
        return AzurStats.get_meow_loot_monthly_totals(year=2026, month=10, **kwargs)

    def test_unknown_zone_keeps_confirmed_paper_plate_and_coordinate_in_separate_bucket(self):
        for item in ('GearDesignPlanTorpedoT5', 'PlateAntiAirT4', 'CoordinateObscure'):
            self.assertEqual(self.insert(0, item), 1)
        totals = self.totals()
        self.assertEqual(totals[0]['GearDesignPlanT5'], 1)
        self.assertEqual(totals[0]['Plate'], 1)
        self.assertEqual(totals[0]['CoordinateObscure'], 1)
        self.assertTrue(all(not any(totals[h].values()) for h in range(1, 7)))
        self.assertEqual(AzurStats.meow_loot_display_levels(totals), [3, 5, 0])

    def test_other_levels_show_up_without_changing_month_device_genre_and_instance_scope(self):
        self.insert(6, 'GearDesignPlanGunT5', 2)
        self.insert(3, 'PlateGunT4', 4)
        self.insert(5, 'PlateGeneralT4', 3)
        self.insert(0, 'GearDesignPlanTorpedoT5', 1, instance='other-fixture')
        self.insert(0, 'GearDesignPlanTorpedoT5', 7, device='other-device')
        self.insert(0, 'GearDesignPlanTorpedoT5', 8, genre='opsi_obscure')
        self.insert(0, 'GearDesignPlanTorpedoT5', 9, moment=datetime(2026, 9, 30, 12))
        totals = self.totals()
        self.assertEqual(totals[6]['GearDesignPlanT5'], 2)
        self.assertEqual(totals[0]['GearDesignPlanT5'], 1)
        self.assertEqual(totals[3]['Plate'], 4)
        self.assertEqual(totals[5]['Plate'], 3)
        self.assertEqual(AzurStats.meow_loot_display_levels(totals), [3, 5, 6, 0])
        self.assertEqual(self.totals(instance='fixture')[0]['GearDesignPlanT5'], 0)

    def test_missing_or_invalid_level_remains_unknown_and_lower_rarity_does_not_count(self):
        self.insert(None, 'GearDesignPlanPlaneT5', 2)
        self.insert(9, 'GearDesignPlanAntiAirT5', 1)
        self.insert(0, 'GearDesignPlanGunT4', 10)
        self.assertEqual(self.totals()[0]['GearDesignPlanT5'], 3)

    def test_month_picker_and_totals_use_the_same_local_month_at_month_start(self):
        self.insert(0, 'GearDesignPlanTorpedoT5', moment=datetime(2026, 10, 1, 0, 30))
        self.assertEqual(AzurStats.get_meow_loot_available_months(), [(2026, 10)])
        self.assertEqual(self.totals()[0]['GearDesignPlanT5'], 1)


if __name__ == '__main__':
    unittest.main()
