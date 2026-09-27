import asyncio
import pytest


def test_real_nat_workflow_matches_native(monkeypatch, tmp_path):
    pytest.importorskip('nat')
    from handoff.engine import Orchestrator
    from handoff.nat_workflow import run_in_nat
    from test_engine import request
    monkeypatch.setenv('HANDOFF_MODEL_MODE', 'offline')
    monkeypatch.setenv('HANDOFF_RETRIEVAL_MODE', 'fixture')
    native = asyncio.run(Orchestrator(trace_dir=tmp_path).run(request()))
    nat = asyncio.run(run_in_nat(request()))
    assert nat['modelMode'] == native['modelMode'] == 'offline'
    assert [s['id'] for s in nat['result']['sections']] == [s['id'] for s in native['result']['sections']]
    assert [s['id'] for s in nat['result']['sources']] == [s['id'] for s in native['result']['sources']]
    def tasks(output):
        return [b['payload'] for s in output['result']['sections'] for b in s['blocks'] if b['type'] == 'job']
    assert tasks(nat) == tasks(native)
