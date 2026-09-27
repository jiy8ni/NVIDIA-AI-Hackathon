import json
import jsonschema
from handoff.contracts import Evidence, RetrievalResponse
from handoff.providers import ROOT

def test_generated_json_schemas_match_runtime_models():
    for name, model in [('evidence', Evidence), ('retrieval-response', RetrievalResponse)]:
        saved = json.loads((ROOT / 'packages/contracts' / (name + '.schema.json')).read_text(encoding='utf-8'))
        assert saved == model.model_json_schema()
        jsonschema.Draft202012Validator.check_schema(saved)

def test_every_fixture_matches_handoff_schema():
    schema = Evidence.model_json_schema()
    records = json.loads((ROOT / 'fixtures/evidence.json').read_text(encoding='utf-8'))
    for record in records:
        jsonschema.Draft202012Validator(schema).validate(record)
