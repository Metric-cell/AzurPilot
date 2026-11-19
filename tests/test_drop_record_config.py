"""Local drop recording and legacy configuration migration."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from module.config.config_generated import GeneratedConfig
from module.config.config_updater import ConfigUpdater
from module.statistics.azurstats import AzurStats


def migrate(record):
    return ConfigUpdater().config_update({'Alas': {'DropRecord': record}})['Alas']['DropRecord']


@pytest.mark.parametrize('old, expected', [
    ('local', 'local'), ('save', 'save'), ('upload', 'local'),
    ('save_and_upload', 'save'), ('do_not', 'do_not'),
])
def test_research_and_commission_modes_migrate(old, expected):
    result = migrate({'ResearchRecord': old, 'CommissionRecord': old, 'CloudUpload': False})
    assert result['ResearchRecord'] == result['CommissionRecord'] == expected
    assert 'CloudUpload' not in result
    assert migrate(result) == result


def test_defaults_and_removed_settings_match_generated_schema():
    schema = ConfigUpdater().args['Alas']['DropRecord']
    template = json.loads(Path('config/template.json').read_text(encoding='utf-8'))['Alas']['DropRecord']
    migrated = migrate({'API': 'cn_gz_reverse_proxy', 'AzurStatsID': 'obsolete'})
    assert 'API' not in migrated and 'AzurStatsID' not in migrated
    for arg in ('ResearchRecord', 'CommissionRecord'):
        assert set(schema[arg]['option']) == {'local', 'save', 'do_not'}
        assert schema[arg]['value'] == template[arg] == migrated[arg] == 'local'
        assert getattr(GeneratedConfig, f'DropRecord_{arg}') == 'local'


@pytest.mark.parametrize('scene', ['research', 'commission'])
@pytest.mark.parametrize('method, save, analyze', [
    ('local', False, True), ('save', True, True), ('do_not', False, False),
    ('upload', False, True), ('save_and_upload', True, True),
])
def test_local_collection_modes(scene, method, save, analyze):
    config = SimpleNamespace(config_name='isolated')
    with patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'):
        drop = AzurStats(config).new(scene, method=method)
    assert drop.save is save
    assert drop.analyze is analyze
    assert bool(drop) is (save or analyze)


def test_research_local_mode_commits_local_rewards():
    config = SimpleNamespace(config_name='isolated')
    with patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'), \
            patch('module.statistics.research_drop.record_research_drop') as parse:
        with AzurStats(config).new('research', method='local') as drop:
            drop.add(np.zeros((720, 1280, 3), np.uint8))
    parse.assert_called_once()
    assert parse.call_args.kwargs['instance'] == 'isolated'
