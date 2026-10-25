"""同一底账的真实分析服务、人工覆盖与接口边界回归；只使用临时配置。"""
import json
import tempfile
import unittest
from unittest.mock import Mock

from pydantic import ValidationError

from module.api.config_service import ConfigService
from module.api.dock_service import DockService
from module.api.fleet_tech_service import FleetTechService
from module.api.mind_calculator_service import MindCalculatorService
from module.api.protocol import ApiError, FleetTechChange
from module.api.router import Router
from module.config.deep import deep_get, deep_set
from module.runtime import dock_data
from module.retire.dock_scan import DockScan
from module.exception import MindCalculatorScanError
from tests.test_api import fixture


class SharedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.configs = ConfigService(fixture(self.temp.name))
        self.mind = MindCalculatorService(self.configs)
        self.tech = FleetTechService(self.configs)
        self.dock = DockService(self.configs)

    def write(self, data):
        self.configs.path('testpilot').write_text(json.dumps(data), encoding='utf-8')

    def scan(self, level=105, rows=None, **extra):
        data, _ = self.configs.read('testpilot')
        if rows is None:
            rows = [dict(name='杜威', group_type=10102, level=level, review=False,
                         rarity='R', base_rarity='R', source='自动扫描第 1 页')]
        params = dict(min_level=1, max_level=125, reached_bottom=True, filters_clear=True,
                      physical_count=len(rows), expected_count=len(rows), updated_at=f'fixture-{level}',
                      previous=dock_data.read_snapshot(data))
        params.update(extra)
        snapshot = dock_data.build_snapshot(rows, **params)
        deep_set(data, dock_data.RESULT_PATH, snapshot)
        self.write(data)
        return snapshot

    def test_同一底账自动供两份分析且读取不写盘不操作游戏(self):
        snapshot = self.scan()
        before = self.configs.path('testpilot').read_bytes()
        runtime = Mock()
        router = Router(self.configs, runtime)
        for method in ('dock.report', 'mind.report', 'fleetTech.report'):
            self.assertIsInstance(router.dispatch(method, {'instance': 'testpilot'}), dict)
        self.assertEqual(self.mind.report('testpilot')['ships'][0]['level'], 105)
        row = next(row for row in self.tech.report('testpilot')['ships'] if row['group_type'] == 10102)
        self.assertEqual(row['level'], 105)
        self.assertEqual(self.dock.report('testpilot')['revision'], snapshot['revision'])
        self.assertEqual(self.configs.path('testpilot').read_bytes(), before)
        runtime.start.assert_not_called()

    def test_心智人工排除跨次保留且等级继续更新不污染科技(self):
        self.scan()
        report = self.mind.report('testpilot')
        ships = [{key: value for key, value in ship.items() if key in
                  ('name', 'level', 'rarity', 'base_rarity', 'excluded', 'review', 'source')}
                 for ship in report['ships']]
        ships[0]['excluded'] = True
        saved = self.mind.save('testpilot', report['revision'], ships)
        self.assertEqual(saved['excluded'], 1)
        self.scan(110)
        report = self.mind.report('testpilot')
        self.assertEqual(report['ships'][0]['level'], 110)
        self.assertTrue(report['ships'][0]['excluded'])
        row = next(row for row in self.tech.report('testpilot')['ships'] if row['group_type'] == 10102)
        self.assertEqual(row['level'], 110)
        self.assertTrue(row['owned'])
        data, _ = self.configs.read('testpilot')
        self.assertFalse(deep_get(data, dock_data.RESULT_PATH)['ships'][0].get('excluded', False))

    def test_科技人工校正保留且不复制扫描快照(self):
        snapshot = self.scan()
        report = self.tech.report('testpilot')
        self.tech.patch('testpilot', report['revision'], [FleetTechChange(group_type=10102, level=125)], {'1': 1})
        data, _ = self.configs.read('testpilot')
        self.assertEqual(deep_get(data, dock_data.RESULT_PATH), snapshot)
        stored = deep_get(data, 'FleetTechScan.FleetTech.Record')
        self.assertNotIn('last_scan', stored)
        self.assertNotIn('observed', stored)
        self.scan(110)
        report = self.tech.report('testpilot')
        self.assertEqual(next(row for row in report['ships'] if row['group_type'] == 10102)['level'], 125)
        self.assertEqual(report['nation_levels']['1'], 1)
        self.assertEqual(self.mind.report('testpilot')['ships'][0]['level'], 110)

    def test_扫描更新拒绝旧人工保存且保留新底账(self):
        self.scan()
        mind = self.mind.report('testpilot')
        tech = self.tech.report('testpilot')
        snapshot = self.scan(110)
        with self.assertRaises(ApiError) as error:
            self.mind.save('testpilot', mind['revision'], [])
        self.assertEqual(error.exception.code, 'CONFLICT')
        with self.assertRaises(ApiError) as error:
            self.tech.patch('testpilot', tech['revision'], [FleetTechChange(group_type=10102, level=120)], {})
        self.assertEqual(error.exception.code, 'CONFLICT')
        self.assertEqual(self.dock.report('testpilot')['revision'], snapshot['revision'])

    def test_配置扫描范围不伪造快照覆盖且接口严格验证(self):
        self.scan()
        runtime = Mock()
        router = Router(self.configs, runtime)
        report = router.dispatch('dock.configure', dict(instance='testpilot', min_level=95, max_level=120))
        self.assertEqual((report['min_level'], report['max_level']), (95, 120))
        self.assertEqual(report['scan_range'], dict(min_level=1, max_level=125))
        before = self.configs.path('testpilot').read_bytes()
        with self.assertRaises(ApiError):
            router.dispatch('dock.configure', dict(instance='testpilot', min_level=120, max_level=95))
        with self.assertRaises(ValidationError):
            router.dispatch('dock.configure', dict(instance='testpilot', min_level=True, max_level=125))
        self.assertEqual(self.configs.path('testpilot').read_bytes(), before)
        runtime.start.assert_not_called()

    def test_空扫描不会复活旧清单(self):
        data, _ = self.configs.read('testpilot')
        deep_set(data, 'MindCalculatorScan.MindCalculator.Result', dict(ships=[
            dict(name='杜威', level=105, source='自动扫描第 1 页')]))
        self.write(data)
        self.scan(rows=[])
        self.assertEqual(self.mind.report('testpilot')['ships'], [])
        self.assertEqual(self.tech.report('testpilot')['last_scan']['ships'], [])

    def test_通用确认弹窗停止且不点击(self):
        scanner = object.__new__(DockScan)
        scanner.appear = Mock(return_value=True)
        scanner.device = Mock()
        with self.assertRaises(MindCalculatorScanError):
            scanner.handle_popup_confirm()
        scanner.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
