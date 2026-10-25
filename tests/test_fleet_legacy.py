"""Pure explicit import produces one ledger and preserves SQLite precedence."""
import copy
import unittest

from module.runtime import dock_data, fleet_tech as tech
from module.runtime.fleet_legacy import convert_legacy, merge_legacy
from module.runtime import mind_analysis


def row(name='杜威', level=105, **kwargs):
    return dict(name=name, level=level, rarity='R', base_rarity='R', review=False, source='fixture', **kwargs)


def snapshot(level=105):
    return dock_data.build_snapshot([dict(row(level=level), group_type=10102)], min_level=1, max_level=125,
        reached_bottom=True, filters_clear=True, physical_count=1, expected_count=1, updated_at='2026-10-25T10:00:00')


class FleetLegacyTests(unittest.TestCase):
    def test_sources_merge_into_one_snapshot_with_dock_preferred_and_progress_retained(self):
        dock = snapshot()
        dock['ships'].append(dict(dock['ships'][0], slot=1))
        data = {'DockScan': {'Dock': {'Result': dock}}, 'MindCalculatorScan': {'MindCalculator': {
            'Result': {'ships': [row(level=110)], 'updated_at': '2026-10-25T11:00:00'},
            'FleetTechRecord': {'observed': {'10103': 125}, 'manual': {'10102': {'owned': True, 'level': 125}},
                                'nations': {'1': 3}, 'unrecognized': {'keep': True}}}},
            'FleetTechScan': {'FleetTech': {'Record': {'manual': {'10102': {'owned': False}},
                'current': {'10104': 120}, 'last_scan': {'ships': [[10104, 120]], 'complete': True,
                'reached_bottom': True, 'filters_clear': True, 'updated_at': '2026-10-25T12:00:00'}}}}}
        before = copy.deepcopy(data)
        result = convert_legacy(data)
        self.assertEqual(data, before)
        self.assertEqual(set(result), {'snapshot', 'mind', 'technology'})
        self.assertEqual(result['technology']['manual']['10102'], {'owned': False, 'level': 125})
        self.assertEqual(result['technology']['nations'], {'1': 3})
        active = result['snapshot']
        self.assertEqual(active['import_source'], 'legacy_dock')
        self.assertEqual(len(active['ships']), 2)
        self.assertEqual(active['history']['observed']['10103'], 125)
        self.assertEqual(active['history']['observed']['10104'], 120)
        state = merge_legacy(dict(snapshot=None, mind={}, technology={}), result)
        self.assertEqual(mind_analysis.ships(state)[0]['level'], 110)

    def test_explicit_mind_analysis_wins_over_legacy_result_derivation(self):
        original = snapshot()
        data = {'DockScan': {'Dock': {'Result': original}}, 'MindCalculatorScan': {'MindCalculator': {
            'Result': {'ships': [row(level=110)]},
            'Analysis': {'overrides': {'group:10102': {'excluded': True}}, 'removed': [],
                         'extra': [row(name='未知舰', level=0, excluded=False)],
                         'snapshot_revision': original['revision'], 'opaque_old_data': 'discard me'}}}}
        result = convert_legacy(data)
        state = merge_legacy(dict(snapshot=None, mind={}, technology={}), result)
        rows = mind_analysis.ships(state)
        self.assertNotIn('opaque_old_data', result['mind'])
        self.assertEqual(rows[0]['level'], 105)
        self.assertTrue(rows[0]['excluded'])
        self.assertEqual(rows[1]['name'], '未知舰')

    def test_existing_sqlite_snapshot_and_manual_values_win_but_history_is_unioned(self):
        active = snapshot(100)
        state = dict(snapshot=active, technology={'manual': {'10102': {'owned': False, 'level': None}},
                     'nations': {'1': 0, '2': None}}, mind={'overrides': {'group:10102': {'excluded': False}},
                     'removed': ['group:10103'], 'extra': [row(name='旧额外舰')], 'snapshot_revision': active['revision']})
        converted = convert_legacy({'DockScan': {'Dock': {'Result': snapshot(125)}},
            'FleetTechScan': {'FleetTech': {'Record': {'manual': {'10102': {'owned': True, 'level': 125,
                'full_star': True}}, 'nations': {'1': 3, '2': 2, '3': 0}}}},
            'MindCalculatorScan': {'MindCalculator': {'Analysis': {'overrides': {'group:10102': {'excluded': True,
                'base_rarity': 'SR'}}, 'removed': ['group:10104'], 'extra': [row(name='新额外舰')]}}}})
        before = copy.deepcopy(state)
        merged = merge_legacy(state, converted)
        self.assertEqual(state, before)
        for key in ('ships', 'min_level', 'max_level', 'updated_at', 'complete'):
            self.assertEqual(merged['snapshot'][key], active[key])
        self.assertEqual(merged['snapshot']['history']['current'], active['history']['current'])
        self.assertEqual(merged['snapshot']['history']['absent'], active['history']['absent'])
        self.assertEqual(merged['snapshot']['history']['observed']['10102'], 125)
        self.assertEqual(merged['technology']['manual']['10102'], {'owned': False, 'level': None, 'full_star': True})
        self.assertEqual(merged['technology']['nations'], {'1': 0, '2': None, '3': 0})
        self.assertEqual(merged['mind']['overrides']['group:10102'], {'excluded': False, 'base_rarity': 'SR'})
        self.assertEqual(merged['mind']['removed'], ['group:10103', 'group:10104'])
        self.assertEqual([item['name'] for item in merged['mind']['extra']], ['旧额外舰', '新额外舰'])
        self.assertNotIn('legacy_sources', merged)
        self.assertNotIn('legacy_snapshots', merged)
        self.assertEqual(merged['mind']['snapshot_revision'], merged['snapshot']['revision'])
        self.assertEqual(merged['legacy_snapshot'], merged['snapshot'])

    def test_newest_trustworthy_snapshot_is_preferred_without_dock(self):
        converted = convert_legacy({'MindCalculatorScan': {'MindCalculator': {'Result': {
            'ships': [row()], 'updated_at': 'invalid'}}}, 'FleetTechScan': {'FleetTech': {'Record': {
            'last_scan': {'ships': [[10103, 120]], 'updated_at': '2026-10-25T12:00:00'}}}}})
        self.assertEqual(converted['snapshot']['import_source'], 'legacy_technology')
        self.assertEqual(converted['snapshot']['ships'][0]['group_type'], 10103)

    def test_empty_and_malformed_inputs_are_rejected_without_mutation(self):
        cases = [{}, {'FleetTechScan': {'FleetTech': {'Record': {'manual': {'10102': {}}}}}}, {'DockScan': {'Dock': {'Result': {'ships': 'invalid'}}}},
                 {'FleetTechScan': {'FleetTech': {'Record': {'manual': {'10102': {'owned': 1}}}}}},
                 {'FleetTechScan': {'FleetTech': {'Record': {'observed': {'10102': 126}}}}},
                 {'MindCalculatorScan': {'MindCalculator': {'Analysis': {'overrides': {'x': {'level': True}}}}}}]
        for data in cases:
            before = copy.deepcopy(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                convert_legacy(data)
            self.assertEqual(data, before)

    def test_explicit_empty_saved_list_is_a_valid_current_snapshot(self):
        converted = convert_legacy({'MindCalculatorScan': {'MindCalculator': {'Result': {'ships': []}}}})
        self.assertEqual(converted['snapshot']['ships'], [])
        self.assertFalse(converted['snapshot']['complete'])

    def test_raw_positive_levels_cannot_be_lowered_by_old_history(self):
        dock = snapshot(125)
        dock['history']['observed']['10102'] = 100
        converted = convert_legacy({'DockScan': {'Dock': {'Result': dock}},
            'FleetTechScan': {'FleetTech': {'Record': {'observed': {'10103': 100},
                'last_scan': {'ships': [[10103, 125]]}}}}})
        self.assertEqual(converted['snapshot']['history']['observed']['10102'], 125)
        self.assertEqual(converted['snapshot']['history']['observed']['10103'], 125)

    def test_existing_mind_deletions_and_edits_win_over_imported_opposites(self):
        state = dict(snapshot=None, technology={}, mind={'removed': ['group:10102'],
            'overrides': {'group:10103': {'excluded': False}}, 'extra': [row(name='唐斯')]})
        converted = dict(snapshot=None, technology={}, mind={
            'removed': ['group:10103', 'group:10104'],
            'overrides': {'group:10102': {'excluded': True}}, 'extra': [row()]})
        merged = merge_legacy(state, converted)
        self.assertEqual(merged['mind']['removed'], ['group:10102'])
        self.assertNotIn('group:10102', merged['mind']['overrides'])
        self.assertEqual([item['name'] for item in merged['mind']['extra']], ['唐斯'])

    def test_existing_technology_grade_is_not_combined_with_imported_grade_fields(self):
        converted = convert_legacy({'FleetTechScan': {'FleetTech': {'Record': {'manual': {
            '10102': {'level': 125, 'lv120': True, 'level_toggle': {'low': 105, 'high': 125},
                      'full_star': False}}}}}})
        for grade in ({'level': 100}, {'lv120': False}, {'level_toggle': {'low': 100, 'high': 120}}):
            with self.subTest(grade=grade):
                existing = {'owned': True, **grade}
                merged = merge_legacy(dict(snapshot=None, mind={}, technology={'manual': {'10102': existing}}), converted)
                self.assertEqual(merged['technology']['manual']['10102'], {**existing, 'full_star': False})
        merged = merge_legacy(dict(snapshot=None, mind={}, technology={'manual': {'10102': {'owned': True, 'level': 100}}}), converted)
        self.assertEqual(tech.progress(dock_data.technology_record(merged), 10102)['level'], 100)

    def test_review_settings_stay_bound_to_their_original_snapshot(self):
        original = dock_data.build_snapshot([dict(row(name='未确认舰', level=0), review=True)],
            min_level=1, max_level=125, reached_bottom=True, filters_clear=True,
            physical_count=1, expected_count=1, updated_at='2026-10-25T10:00:00')
        key = next(mind_analysis.keyed(dock_data.mind_ships(original)))[0]
        data = {'DockScan': {'Dock': {'Result': original}}, 'MindCalculatorScan': {'MindCalculator': {
            'Analysis': {'overrides': {key: {'excluded': True}}, 'snapshot_revision': 'stale'}}}}
        stale = convert_legacy(data)
        self.assertEqual(stale['mind']['snapshot_revision'], 'stale')
        merged = merge_legacy(dict(snapshot=None, mind={}, technology={}), stale)
        self.assertFalse(mind_analysis.ships(merged)[0]['excluded'])
        data['MindCalculatorScan']['MindCalculator']['Analysis']['snapshot_revision'] = original['revision']
        matching = convert_legacy(data)
        self.assertEqual(matching['mind']['snapshot_revision'], matching['snapshot']['revision'])
        self.assertTrue(mind_analysis.ships(merge_legacy(dict(snapshot=None, mind={}, technology={}), matching))[0]['excluded'])
        same_source = merge_legacy(dict(snapshot=original, mind={'snapshot_revision': original['revision']}, technology={}), matching)
        self.assertTrue(mind_analysis.ships(same_source)[0]['excluded'])
        active = copy.deepcopy(original)
        active['updated_at'] = '2026-10-25T11:00:00'
        active['revision'] = tech.revision({field: value for field, value in active.items() if field != 'revision'})
        matching['mind']['removed'] = [key]
        matching['mind']['extra'] = [dict(row(name='未确认舰', level=0), review=True)]
        merged = merge_legacy(dict(snapshot=active, mind={'snapshot_revision': active['revision']}, technology={}), matching)
        self.assertNotIn(key, merged['mind'].get('overrides', {}))
        self.assertNotIn(key, merged['mind'].get('removed', []))
        self.assertEqual(len(merged['mind'].get('extra', [])), 1)
        self.assertEqual(len(mind_analysis.ships(merged)), 1)

    def test_current_manual_extra_stays_effective_when_import_adds_automatic_base(self):
        extra = dict(row(level=100), excluded=True)
        state = dict(snapshot=None, mind={'extra': [extra]}, technology={})
        converted = convert_legacy({'DockScan': {'Dock': {'Result': snapshot(125)}}})
        merged = merge_legacy(state, converted)
        rows = mind_analysis.ships(merged)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['level'], 100)
        self.assertTrue(rows[0]['excluded'])
        self.assertEqual(merged['snapshot']['ships'][0]['level'], 125)
        # An automatic base with no current manual extra keeps following scans.
        self.assertNotIn('group:10102', merge_legacy(dict(snapshot=snapshot(105), mind={}, technology={}), converted)['mind'].get('overrides', {}))

    def test_unknown_manual_extra_without_snapshots_remains_visible(self):
        data = {'MindCalculatorScan': {'MindCalculator': {'Analysis': {'extra': [
            dict(row(name='未确认的人工舰', level=0), review=True)]}}}}
        converted = convert_legacy(data)
        self.assertIsNone(converted['snapshot'])
        merged = merge_legacy(dict(snapshot=None, mind={}, technology={}), converted)
        self.assertEqual(mind_analysis.ships(merged)[0]['name'], '未确认的人工舰')
        self.assertTrue(mind_analysis.ships(merged)[0]['review'])

    def test_current_extra_wins_over_imported_override_but_original_override_wins(self):
        data = {'DockScan': {'Dock': {'Result': snapshot(125)}},
            'MindCalculatorScan': {'MindCalculator': {'Analysis': {'overrides': {
                'group:10102': {'level': 120, 'excluded': False}}}}}}
        converted = convert_legacy(data)
        state = dict(snapshot=None, technology={}, mind={'extra': [dict(row(level=100), excluded=True)]})
        rows = mind_analysis.ships(merge_legacy(state, converted))
        self.assertEqual((rows[0]['level'], rows[0]['excluded']), (100, True))
        state['mind']['overrides'] = {'group:10102': {'level': 105}}
        rows = mind_analysis.ships(merge_legacy(state, converted))
        self.assertEqual((rows[0]['level'], rows[0]['excluded']), (105, True))

    def test_empty_analysis_is_not_useful_import_data(self):
        with self.assertRaises(ValueError):
            convert_legacy({'MindCalculatorScan': {'MindCalculator': {'Analysis': {
                'overrides': {}, 'removed': [], 'extra': [], 'updated_at': '2026-10-25T12:00:00'}}}})


if __name__ == '__main__':
    unittest.main()
