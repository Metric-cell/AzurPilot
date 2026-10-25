"""Browser mock helpers exercise the independent database used by real services."""
import base64
import json
import unittest

from dev_tools.mind_calculator_mock import dispatch
from module.runtime import dock_data
from module.api.protocol import ApiError
from module.config.deep import deep_get
from module.config.fleet_legacy import LEGACY_PATHS


class MindCalculatorMockTest(unittest.TestCase):
    def test_technology_patch_roundtrip_and_sync_are_independent_of_mind_list(self):
        request = dict(instance='fixture', record={}, ships=[dict(name='杜威', level=125)])
        initial = dispatch(dict(request, action='tech.report'))
        self.assertFalse(next(row for row in initial['result']['ships'] if row['group_type'] == 10102)['owned'])
        saved = dispatch(dict(request, action='tech.patch', revision=initial['result']['revision'],
                              changes=[dict(group_type=10102, level=105, full_star=False)], nations={'1': 2}))
        self.assertEqual(saved['record']['manual']['10102']['level'], 105)
        reloaded = dispatch(dict(request, action='tech.report', record=saved['record']))
        synced = dispatch(dict(request, action='tech.syncMind', record=saved['record']))
        self.assertEqual(reloaded, synced)
        self.assertEqual(reloaded['result']['nation_levels']['1'], 2)
        row = next(row for row in reloaded['result']['ships'] if row['group_type'] == 10102)
        self.assertEqual((row['level'], row['manual'], row['full_star']), (105, True, False))

    def test_scan_fixture_is_seeded_in_database_and_keeps_manual_override(self):
        snapshot = dock_data.build_snapshot([dict(group_type=10102, name='杜威', level=125)],
            min_level=1, max_level=125, reached_bottom=True, filters_clear=True,
            physical_count=1, expected_count=1, updated_at='2026-10-25T12:00:00')
        output = dispatch(dict(action='tech.report', instance='fixture', snapshot=snapshot,
                               record={'manual': {'10102': {'owned': False}}}))
        row = next(row for row in output['result']['ships'] if row['group_type'] == 10102)
        self.assertFalse(row['owned'])
        self.assertTrue(row['manual'])
        self.assertEqual(output['result']['last_scan']['ships'], [(10102, 125)])

    def test_mind_export_reads_manual_database_entries(self):
        exported = dispatch(dict(action='export', instance='fixture', format='json',
                                 ships=[dict(name='杜威', level=105)]))
        content = json.loads(base64.b64decode(exported['content']))
        self.assertEqual([(ship['name'], ship['level']) for ship in content['ships']], [('杜威', 105)])

    def test_explicit_local_import_roundtrips_full_state_and_preserves_current_values(self):
        def snapshot(level):
            return dock_data.build_snapshot([dict(group_type=10102, name='杜威', level=level)],
                min_level=1, max_level=125, reached_bottom=True, filters_clear=True,
                physical_count=1, expected_count=1, updated_at='2026-10-25T12:00:00')

        local = {
            'DockScan': {'Dock': {'MinLevel': 1, 'MaxLevel': 125, 'Result': snapshot(125)}},
            'MindCalculatorScan': {'MindCalculator': {
                'Result': {'ships': [{'name': '杜威', 'level': 110}]},
                'Analysis': {'overrides': {'group:10102': {'excluded': True}}, 'removed': [], 'extra': []},
                'FleetTechRecord': {'observed': {'10103': 120}, 'manual': {'10103': {'full_star': False}}},
            }},
            'FleetTechScan': {'FleetTech': {'Record': {'observed': {'10104': 125},
                'manual': {'10102': {'owned': True}}, 'nations': {'1': 3}}}},
        }
        request = dict(instance='fixture', config=local)
        blank = dispatch(dict(request, action='dock.report'))
        self.assertEqual(blank['result']['ships'], [])
        self.assertFalse(blank['result']['legacy_imported'])
        self.assertEqual(blank['config'], local)
        current = dict(snapshot=snapshot(100), mind={'overrides': {'group:10102': {'level': 115}}},
                       technology={'manual': {'10102': {'owned': False}}, 'nations': {'1': 1}})
        report = dispatch(dict(request, action='dock.report', state=current))
        migrated = dispatch(dict(request, action='dock.importLegacy', state=current,
                                 revision=report['result']['revision']))
        self.assertTrue(migrated['state']['legacy_imported'])
        self.assertEqual(migrated['result']['ships'][0]['level'], 100)
        self.assertTrue(all(deep_get(migrated['config'], path) is None for path in LEGACY_PATHS))
        persisted = dict(instance='fixture', config=migrated['config'], state=migrated['state'])
        mind = dispatch(dict(persisted, action='mind.report'))['result']
        self.assertEqual((mind['ships'][0]['level'], mind['ships'][0]['excluded']), (115, True))
        tech = dispatch(dict(persisted, action='tech.report'))['result']
        rows = {row['group_type']: row for row in tech['ships']}
        self.assertFalse(rows[10102]['owned'])
        self.assertEqual(rows[10103]['observed_level'], 120)
        self.assertEqual(rows[10104]['observed_level'], 125)
        self.assertEqual(tech['nation_levels']['1'], 1)
        with self.assertRaises(ApiError) as error:
            dispatch(dict(persisted, action='dock.importLegacy', revision=migrated['result']['revision']))
        self.assertEqual(error.exception.code, 'ALREADY_IMPORTED')
