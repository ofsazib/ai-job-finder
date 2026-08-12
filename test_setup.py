import subprocess
import sys


def test_python_version_gate_accepts_supported_interpreter():
    result = subprocess.run(
        ["make", "check-python", f"PYTHON_BIN={sys.executable}"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Python" in result.stdout
    assert "supported" in result.stdout


def test_python_version_gate_explains_minimum(tmp_path):
    old_python = tmp_path / "python-old"
    old_python.write_text("#!/bin/sh\necho 'Python 3.11.9'\nexit 1\n")
    old_python.chmod(0o755)

    result = subprocess.run(
        ["make", "check-python", f"PYTHON_BIN={old_python}"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Python 3.12 or newer is required" in output
    assert "Python 3.11.9" in output
    assert "PYTHON_BIN=python3.12" in output
