from pathlib import Path
import pytest

from zelenochka.fixtures import load_fixture
from zelenochka.repository import Repository

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def repo():
    with Repository() as repository:
        load_fixture(FIXTURES / 'basic', repository)
        yield repository
