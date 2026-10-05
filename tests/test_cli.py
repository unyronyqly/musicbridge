import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from zelenochka.cli import main

FIXTURES = Path(__file__).parent / 'fixtures'


def test_cli_summary_and_json(capsys):
    assert main(['plan', '--fixture', str(FIXTURES / 'basic')]) == 0
    output = capsys.readouterr().out
    assert 'ADD_LIKE: 1' in output
    assert 'No remote writes.' in output
    report = json.loads(output.partition('JSON plan\n')[2])
    assert report['unresolved'] == [{'provider': 'yandex', 'id': 'missing'}]
    assert any(a['kind'] == 'REMOVE_PLAYLIST_ITEM' for a in report['actions'])


def test_cli_is_byte_deterministic():
    cmd = [sys.executable, '-m', 'zelenochka', 'plan', '--fixture', str(FIXTURES / 'basic')]
    # Installed package entrypoint is separately exercised from a clean checkout.
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')}
    first = subprocess.run(cmd, env=env, text=True, capture_output=True, check=True)
    second = subprocess.run(cmd, env=env, text=True, capture_output=True, check=True)
    assert first.stdout == second.stdout


def test_missing_fixture_fails_cleanly(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(['plan', '--fixture', str(tmp_path)])
    assert error.value.code == 2
    assert 'Fixture error:' in capsys.readouterr().err


def test_no_apply_or_live_command(capsys):
    with pytest.raises(SystemExit) as error:
        main(['apply'])
    assert error.value.code == 2
