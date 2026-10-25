"""前端模拟服务调用真实计算器，只处理管道内的夹具数据。"""
import json
import sys
import tempfile
import threading
from pathlib import Path
from contextlib import redirect_stdout
from types import SimpleNamespace


def dispatch(request):
    from module.runtime.mind_calculator import calculate, catalog, revision
    from module.api.mind_calculator_service import MindCalculatorService, import_ships
    action = request['action']
    ships = request.get('ships', [])
    if action.startswith(('tech.', 'dock.', 'mind.')):
        from module.api.dock_service import DockService
        from module.api.fleet_tech_service import FleetTechService
        from module.api.protocol import FleetTechChange
        from module.runtime.fleet_database import FleetDatabase
        from module.runtime.fleet_tech import revision as tech_revision
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            configuration = request.get('config', {'DockScan': {'Dock': {
                'MinLevel': request.get('min_level', 1), 'MaxLevel': request.get('max_level', 125),
            }}})
            path.write_text(json.dumps(configuration), encoding='utf-8')
            database = FleetDatabase(path)
            current = request.get('state') or dict(snapshot=request.get('snapshot'), mind={},
                                                   technology=request.get('record', {}))
            if current.get('snapshot') is not None:
                database.save_scan(current['snapshot'], tech_revision({}))
            if current.get('mind') or current.get('technology') or current.get('legacy_imported'):
                with database.edit(import_legacy=bool(current.get('legacy_imported'))) as state:
                    state['mind'] = current.get('mind', {})
                    state['technology'] = current.get('technology', {})
            configs = SimpleNamespace(path=lambda _: path, lock=threading.RLock(),
                                      read=lambda _: (json.loads(path.read_text(encoding='utf-8')), ''))
            service = FleetTechService(configs)
            if action == 'tech.catalog':
                result = service.catalog(request['instance'])
            elif action == 'tech.report':
                result = service.report(request['instance'])
            elif action == 'tech.syncMind':
                result = service.sync_mind(request['instance'])
            elif action == 'tech.patch':
                result = service.patch(request['instance'], request['revision'],
                                       [FleetTechChange.model_validate(c) for c in request.get('changes', [])], request.get('nations', {}))
            elif action == 'dock.report':
                result = DockService(configs).report(request['instance'])
            elif action == 'dock.importLegacy':
                result = DockService(configs).import_legacy(request['instance'], request['revision'])
            elif action == 'mind.report':
                result = MindCalculatorService(configs).report(request['instance'])
            elif action == 'mind.save':
                result = MindCalculatorService(configs).save(request['instance'], request['revision'], ships)
            elif action == 'mind.export':
                result = MindCalculatorService(configs).export(request['instance'], request.get('format', 'xlsx'))
            else:
                raise ValueError('未知舰船模拟操作')
            stored = database.load(include_import_status=True)
            return dict(result=result, record=stored['technology'], state=stored,
                        config=configs.read('')[0])
    if action == 'catalog':
        data = catalog()
        return dict(updated_at=data['updated_at'], ships=list(data['ships'].values()))
    if action in ('report', 'calculate'):
        return dict(revision=revision(ships), **calculate(ships))
    if action == 'import':
        return {'ships': import_ships(request['filename'], request['content'])}
    if action in ('export', 'recognize'):
        from module.runtime.fleet_database import FleetDatabase
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            path.write_text('{}', encoding='utf-8')
            database = FleetDatabase(path)
            with database.edit() as state:
                state['mind'] = dict(overrides={}, removed=[], extra=ships)
            service = MindCalculatorService(SimpleNamespace(path=lambda _: path, read=lambda _: ({}, '')))
            if action == 'export':
                return service.export(request['instance'], request.get('format', 'xlsx'))
            return service.recognize(request['instance'], request['filename'], request['content'])
    raise ValueError('未知模拟操作')


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            result = dispatch(request)
    except Exception as exc:
        result = {'error': str(exc), 'code': getattr(exc, 'code', 'INVALID_PARAMS')}
    print(json.dumps(result, ensure_ascii=False))
