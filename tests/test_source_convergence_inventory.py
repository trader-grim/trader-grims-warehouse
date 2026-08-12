import json
from pathlib import Path

from tgw.stranded_work import (
    inspect_release_generations,
    inspect_runtime_bindings,
    scan_consumer_references,
)


def test_survivor_inventory_separates_evidence_dimensions_and_holds_cleanup():
    inventory = json.loads(Path('config/plan/survivor-admission-inventory-v1.json').read_text())
    assert inventory['schema'] == 'tgw-source-convergence-inventory/v1'
    assert inventory['cleanup_authorized'] is False
    assert inventory['retirement_gate']['status'] == 'HELD'
    required = {'designed', 'implemented', 'tested', 'reviewed', 'admitted', 'deployed', 'live'}
    assert inventory['providers']
    assert all(set(provider['state']) == required for provider in inventory['providers'])
    graph = next(item for item in inventory['providers'] if item['capability'] == 'plan.graph-query@1')
    assert graph['survivor'] == 'src/tgw/plan_graph'
    assert 'source-commit:edb452e' in graph['evidence']
    provision = next(item for item in inventory['providers'] if item['capability'] == 'coding.governed-provision@1')
    assert provision['state']['deployed'] is True
    assert provision['state']['admitted'] is False
    capabilities = {item['capability'] for item in inventory['providers']}
    assert {
        'plan.graph-query@1',
        'promptcraft.receiver-profiles@1',
        'plan.capability-resolution@2',
        'workflow.plan-solution-runtime-bridge@1',
        'authority.plan-effects@1',
        'coding.harness-role-selection@1',
        'coding.agent-service-materialization@1',
        'workflow.condition-derived-convergence@1',
    } <= capabilities


def test_release_inventory_records_manifest_and_never_authorizes_cleanup(tmp_path):
    root = tmp_path / 'releases'
    release = root / 'generation-a'
    release.mkdir(parents=True)
    (release / '.release-manifest.json').write_text('{"source":"abc"}\n')
    result = inspect_release_generations([root])
    assert result[0]['name'] == 'generation-a'
    assert result[0]['manifest']['sha256']
    assert result[0]['cleanup_authorized'] is False


def test_runtime_binding_and_stale_consumer_scans_are_bounded_and_hashed(tmp_path):
    units = tmp_path / 'units'
    units.mkdir()
    service = units / 'worker.service'
    service.write_text('ExecStart=/opt/TGW/tgw-lib/releases/old/bin/worker\n')
    bindings = inspect_runtime_bindings([units])
    assert bindings == [{
        'path': str(service),
        'sha256': bindings[0]['sha256'],
        'references': ['/opt/TGW/tgw-lib/releases/old/bin/worker'],
    }]
    findings = scan_consumer_references([units], ['/opt/TGW/tgw-lib/releases/old'])
    assert findings[0]['classification'] == 'STALE-CONSUMER-REFERENCE'
    assert findings[0]['cleanup_authorized'] is False
    assert findings[0]['line'] == 1


def test_scanners_do_not_mutate_inputs(tmp_path):
    root = tmp_path / 'bindings'
    root.mkdir()
    source = root / 'config'
    source.write_text('/opt/TGW/example\n')
    before = source.read_bytes()
    inspect_runtime_bindings([root])
    scan_consumer_references([root], ['/opt/TGW/example'])
    assert source.read_bytes() == before
