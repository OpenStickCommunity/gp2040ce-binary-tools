"""Test our tools themselves to make sure they adhere to certain flags."""
from subprocess import run

from gp2040ce_bintools import __version__


def test_version_flag():
    """Test that tools report the version."""
    result = run(['visualize-storage', '-v'], capture_output=True, encoding='utf8')
    assert __version__ in result.stdout


def test_help_flag():
    """Test that tools report the usage information."""
    result = run(['visualize-storage', '-h'], capture_output=True, encoding='utf8')
    assert 'usage: visualize-storage' in result.stdout
    assert 'Read the configuration section from a dump of a GP2040-CE board' in result.stdout
