"""共享船坞的副本、只读兼容及分析隔离回归。"""
import copy
import unittest

from module.api.protocol import MindShip
from module.config.deep import deep_set
from module.runtime import dock_data as dock
from module.runtime import fleet_tech as tech
from module.runtime.mind_calculator import calculate


def ship(gid=10102, level=105, **extra):
    return dict(group_type=gid, name='测试舰', level=level, rarity='R', base_rarity='R',
                source='夹具', review=False, **extra)


def snapshot(rows, **extra):
    fields = dict(min_level=1, max_level=125, reached_bottom=True, filters_clear=True,
                  physical_count=len(rows), expected_count=len(rows), updated_at='2026-10-25T12:00:00')
    fields.update(extra)
    return dock.build_snapshot(rows, **fields)


class DockDataTests(unittest.TestCase):
    def test_物理副本保留且输入不变(self):
        rows = [ship(level=105), ship(level=120)]
        before = copy.deepcopy(rows)
        result = snapshot(rows)
        self.assertEqual(rows, before)
        self.assertEqual(len(result['ships']), 2)
        self.assertEqual([row['slot'] for row in result['ships']], [0, 1])
        self.assertEqual(result['history']['current'], {'10102': 120})
        self.assertTrue(result['complete'])
        self.assertEqual(set(result['history']), {'observed', 'current', 'absent'})

    def test_范围和缺失证明不能降低历史(self):
        previous = snapshot([ship(level=125)])
        for extra in [dict(min_level=95), dict(filters_clear=False), dict(reached_bottom=False),
                      dict(expected_count=None), dict(physical_count=2)]:
            with self.subTest(extra=extra):
                result = snapshot([ship(level=105)], previous=previous, **extra)
                self.assertFalse(result['complete'])
                self.assertEqual(result['history']['current']['10102'], 125)
        result = snapshot([ship(level=105)], previous=previous)
        self.assertEqual(result['history']['current']['10102'], 105)
        self.assertEqual(result['history']['observed']['10102'], 125)

    def test_空模板不是快照而显式空清单优先(self):
        data = {}
        deep_set(data, dock.RESULT_PATH, {})
        deep_set(data, 'MindCalculatorScan.MindCalculator.Result',
                 dict(ships=[ship()], updated_at='2026-10-24T12:00:00'))
        before = copy.deepcopy(data)
        self.assertFalse(dock.has_snapshot(data))
        self.assertEqual(dock.read_snapshot(data)['source'], 'legacy_mind')
        self.assertEqual(data, before)
        deep_set(data, dock.RESULT_PATH, snapshot([]))
        self.assertTrue(dock.has_snapshot(data))
        self.assertEqual(dock.report_snapshot(data)['ships'], [])
        self.assertEqual(dock.report_snapshot(data)['source'], 'scan')

    def test_旧来源仅选择较新可信日期且从不冒充全量(self):
        data = {}
        deep_set(data, 'MindCalculatorScan.MindCalculator.Result',
                 dict(ships=[ship()], updated_at='不是日期'))
        deep_set(data, tech.RECORD_PATH,
                 dict(observed={'10102': 125}, manual={'10102': {'owned': False}},
                      last_scan=dict(ships=[[30507, 120]], updated_at='2026-10-25T12:00:00', complete=True)))
        before = copy.deepcopy(data)
        result = dock.read_snapshot(data)
        self.assertEqual(result['source'], 'legacy_tech')
        self.assertEqual(len(result['ships']), 1)
        self.assertEqual(result['ships'][0]['name'], '加贺')
        self.assertFalse(result['complete'])
        self.assertEqual(result['history']['observed'], {'10102': 125})
        self.assertEqual(data, before)

    def test_心智限定名及特殊船排除不改变共享源(self):
        result = snapshot([ship(30507), ship(30702), ship(10220)])
        before = copy.deepcopy(result)
        rows = dock.mind_ships(result)
        for row in rows:
            MindShip.model_validate(row)
        self.assertEqual([row['name'] for row in rows[:2]], ['加贺（战列）', '加贺（正航）'])
        report = calculate(rows)
        self.assertEqual((report['included'], report['excluded']), (2, 1))
        self.assertEqual(result, before)

    def test_科技合并保留人工阵营和旧历史且只读(self):
        data = {}
        old = dict(observed={'10103': 125}, manual={'10102': {'owned': False}}, nations={'1': 3})
        deep_set(data, tech.RECORD_PATH, old)
        deep_set(data, dock.RESULT_PATH, snapshot([ship()], min_level=95))
        before = copy.deepcopy(data)
        record = dock.technology_record(data)
        self.assertEqual(record['manual'], old['manual'])
        self.assertEqual(record['nations'], old['nations'])
        self.assertEqual(record['observed']['10103'], 125)
        self.assertFalse(tech.progress(record, 10102)['owned'])
        self.assertEqual(data, before)

    def test_无共享数据不影响旧科技且默认全范围(self):
        data = {}
        deep_set(data, tech.RECORD_PATH, {'manual': {'10102': {'owned': True}}})
        self.assertEqual(dock.technology_record(data), tech.read_record(data))
        report = dock.report_snapshot({})
        self.assertEqual((report['min_level'], report['max_level'], report['source']), (1, 125, 'empty'))

    def test_旧科技历史继承不携带人工字段且局部扫描不降级(self):
        data = {}
        deep_set(data, tech.RECORD_PATH, dict(observed={'10102': 125}, current={'10102': 125},
                                            manual={'10102': {'owned': False}}, nations={'1': 3}))
        legacy = dock.read_snapshot(data)
        result = snapshot([ship(level=105)], min_level=95, previous=legacy)
        self.assertEqual(result['history']['current']['10102'], 125)
        self.assertFalse({'manual', 'nations', 'last_scan'} & result['history'].keys())
        deep_set(data, dock.RESULT_PATH, result)
        self.assertEqual(dock.technology_record(data)['current']['10102'], 125)

    def test_心智合并确认副本而疑点卡位保持独立(self):
        rows = [ship(level=105), ship(level=120), ship(None, 0), ship(None, 0)]
        rows[2]['review'] = rows[3]['review'] = True
        result = snapshot(rows)
        projected = dock.mind_ships(result)
        self.assertEqual(len(result['ships']), 4)
        self.assertEqual(len(projected), 3)
        self.assertEqual(projected[0]['level'], 120)
        self.assertEqual(sum(row['review'] for row in projected), 2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['review_count'], 2)


if __name__ == '__main__':
    unittest.main()
