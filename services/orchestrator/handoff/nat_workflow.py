"""Optional NAT 1.5 integration. Install .[nat] in a supported Python 3.11-3.13 environment."""
import json
from nat.builder.builder import Builder
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import Field
from .contracts import RunRequest, AgentError
from .engine import Orchestrator


class HandoffConfig(FunctionBaseConfig, name='handoffos'):
    user_id: str = 'kim-juhyung'
    team_id: str = 'atlas'
    sources: list[str] = Field(default_factory=lambda: ['slack', 'notion', 'drive'])


@register_function(config_type=HandoffConfig)
async def handoffos(config: HandoffConfig, builder: Builder):
    async def invoke(message: str) -> str:
        request = RunRequest.model_validate_json(message)
        if request.scope.userId != config.user_id or request.scope.teamId != config.team_id or not set(request.scope.sources).issubset(config.sources):
            raise AgentError('SCOPE_DENIED', 'NAT workflow의 서버 권한 범위를 벗어났습니다.', 403)
        result = await Orchestrator().run(request)
        return json.dumps(result, ensure_ascii=False)
    yield FunctionInfo.from_fn(invoke, description='HandoffOS bounded read-only retrieval and evidence synthesis')


async def run_in_nat(request):
    from nat.builder.workflow_builder import WorkflowBuilder
    async with WorkflowBuilder() as builder:
        config = HandoffConfig(user_id=request.scope.userId, team_id=request.scope.teamId, sources=request.scope.sources)
        function = await builder.add_function(name='handoffos', config=config)
        return json.loads(await function.ainvoke(request.model_dump_json()))
