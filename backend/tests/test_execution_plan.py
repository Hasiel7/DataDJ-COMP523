from uuid import uuid4

from app.dr.config import TSNEConfig
from app.schemas.execution_plan import ExecutionPlan


def test_execution_plan_stores_defaults_and_round_trips() -> None:
    plan = ExecutionPlan(
        dataset_id=uuid4(),
        observation_count=100,
        n_components=2,
        random_seed=42,
        configs=(TSNEConfig(perplexity=5),),
    )

    payload = plan.model_dump(mode="json")
    assert payload["configs"][0]["learning_rate"] == 200.0
    assert payload["configs"][0]["max_iter"] == 1000
    assert ExecutionPlan.model_validate(payload) == plan