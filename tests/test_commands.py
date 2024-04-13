"""Test our tools themselves to make sure they adhere to certain flags.

SPDX-FileCopyrightText: © 2023 Brian S. Stephan <bss@incorporeal.org>
SPDX-License-Identifier: GPL-3.0-or-later
"""
import json
import os
import sys
from subprocess import run

from decorator import decorator

from gp2040ce_bintools import __version__

HERE = os.path.dirname(os.path.abspath(__file__))


@decorator
def with_pb2s(test, *args, **kwargs):
    """Wrap a test with precompiled pb2 files on the path."""
    proto_path = os.path.join(HERE, 'test-files', 'pb2-files')
    sys.path.append(proto_path)

    test(*args, **kwargs)

    sys.path.pop()
    del sys.modules['config_pb2']


def test_version_flag():
    """Test that tools report the version."""
    result = run(['visualize-config', '-v'], capture_output=True, encoding='utf8')
    assert __version__ in result.stdout


def test_help_flag():
    """Test that tools report the usage information."""
    result = run(['visualize-config', '-h'], capture_output=True, encoding='utf8')
    assert 'usage: visualize-config' in result.stdout
    assert 'Read the configuration section from a dump of a GP2040-CE board' in result.stdout


def test_concatenate_invocation(tmpdir):
    """Test that a normal invocation against a dump works."""
    out_filename = os.path.join(tmpdir, 'out.bin')
    _ = run(['concatenate', 'tests/test-files/test-firmware.bin', '--binary-user-config-filename',
             'tests/test-files/test-storage-area.bin', '--new-filename', out_filename])
    with open(out_filename, 'rb') as out_file, open('tests/test-files/test-storage-area.bin', 'rb') as storage_file:
        out = out_file.read()
        storage = storage_file.read()
    assert out[2080768:2097152] == storage


def test_concatenate_invocation_json(tmpdir):
    """Test that a normal invocation with a firmware and a JSON file works."""
    out_filename = os.path.join(tmpdir, 'out.bin')
    _ = run(['concatenate', '-P', 'tests/test-files/proto-files', 'tests/test-files/test-firmware.bin',
             '--json-user-config-filename', 'tests/test-files/test-config.json', '--new-filename',
             out_filename])
    with open(out_filename, 'rb') as out_file, open('tests/test-files/test-binary-source-of-json-config.bin',
                                                    'rb') as storage_file:
        out = out_file.read()
        storage = storage_file.read()
    assert out[2093382:2097152] == storage


def test_summarize_invocation(tmpdir):
    """Test that we can get some summary information."""
    result = run(['summarize-gp2040ce', '--filename', 'tests/test-files/test-firmware.bin'],
                 capture_output=True, encoding='utf8')
    assert 'detected GP2040-CE version:     v0.7.5' in result.stdout


def test_storage_dump_invocation():
    """Test that a normal invocation against a dump works."""
    result = run(['visualize-config', '-P', 'tests/test-files/proto-files',
                  '--filename', 'tests/test-files/test-storage-area.bin'],
                 capture_output=True, encoding='utf8')
    assert 'boardVersion: "v0.7.5"' in result.stdout


def test_debug_storage_dump_invocation():
    """Test that a normal invocation against a dump works."""
    result = run(['visualize-config', '-d', '-P', 'tests/test-files/proto-files',
                  '--filename', 'tests/test-files/test-storage-area.bin'],
                 capture_output=True, encoding='utf8')
    assert 'boardVersion: "v0.7.5"' in result.stdout
    assert 'length of content to look for footer in: 16384' in result.stderr


def test_storage_dump_json_invocation():
    """Test that a normal invocation against a dump works."""
    result = run(['visualize-config', '-P', 'tests/test-files/proto-files', '--json',
                  '--filename', 'tests/test-files/test-storage-area.bin'],
                 capture_output=True, encoding='utf8')
    to_dict = json.loads(result.stdout)
    assert to_dict['boardVersion'] == 'v0.7.5'
