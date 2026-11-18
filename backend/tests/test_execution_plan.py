from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.dr.config import TSNEConfig
from app.schemas.execution_plan import ExecutionPlan
from app.schemas.grid import GridPreviewRequest
from app.services.grids import expand_job_grid


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


def test_grid_and_saved_plan_accept_200_configurations() -> None:
    request = GridPreviewRequest.model_validate({
        "dataset_id": str(uuid4()),
        "algorithms": {
            "tsne": {"perplexity": {"start": "1", "stop": "20.9", "step": "0.1"}}
        },
    })
    configs = expand_job_grid(request, n_samples=100)
    assert len(configs) == 200
    plan = ExecutionPlan(
        dataset_id=request.dataset_id, observation_count=100,
        n_components=2, random_seed=42, configs=tuple(configs),
    )
    assert ExecutionPlan.model_validate_json(plan.model_dump_json()) == plan
    with pytest.raises(ValidationError):
        ExecutionPlan(
            dataset_id=request.dataset_id, observation_count=100,
            n_components=2, random_seed=42, configs=(*configs, configs[0]),
        )