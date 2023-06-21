"""Create the test fixtures and other data."""
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def storage_dump():
    """Read in a test storage dump file (101FE000-10200000) of a GP2040-CE board."""
    filename = os.path.join(HERE, 'test-files', 'test-storage-area.bin')
    with open(filename, 'rb') as file:
        content = file.read()

    yield content
