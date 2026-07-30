import pytest

from tests.helpers import ScenarioFactory, make_scenario


@pytest.fixture
def scenario_factory() -> ScenarioFactory:
    return make_scenario
