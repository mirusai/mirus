"""Check actual distribution contents and isolated serving imports, not just extras."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

import pytest


def assert_current_sources(wheel, packages):
    """Reject obsolete files and cached code from an earlier package build."""
    repository = Path(__file__).resolve().parents[2]
    expected = {path.relative_to(repository).as_posix()
                for package in packages for path in (repository / package).glob("*.py")}
    actual = {name for name in wheel.namelist() if name.endswith(".py")}
    assert actual == expected
    for name in expected:
        assert wheel.read(name) == (repository / name).read_bytes(), f"stale wheel source: {name}"


@pytest.fixture
def wheels():
    directory = os.getenv("MIRUS_WHEEL_DIR")
    if not directory:
        pytest.skip("set MIRUS_WHEEL_DIR to locally built mirus, backtest and validation wheels")
    root = Path(directory)
    return (next(root.glob("mirus-*.whl")), next(root.glob("mirus_backtest-*.whl")),
            next(root.glob("mirus_validation-*.whl")))


def test_serving_wheel_excludes_offline_files_and_runs_without_site_packages(wheels, tmp_path):
    serving, _, _ = wheels
    with ZipFile(serving) as wheel:
        assert_current_sources(wheel, ("mirus", "mirus/features", "mirus/payload", "mirus/serving"))
        names = wheel.namelist()
        metadata = wheel.read(next(name for name in names if name.endswith("/METADATA"))).decode()
        assert "Name: mirus\n" in metadata
        assert "mirus-backtest[spark]" in metadata
        wheel.extractall(tmp_path)
    subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys, importlib.util\n"
         "from pathlib import Path\n"
         "import mirus\n"
         "assert Path(mirus.__file__).resolve().parent == Path.cwd() / 'mirus'\n"
         "from mirus.features.decorators import feature\n"
         "from mirus.features.compute import prepare_features, compute_features\n"
         "assert not hasattr(mirus, 'feature')\n"
         "assert not hasattr(mirus.features, 'compute_features')\n"
         "from mirus.payload import Payload, Field, PayloadSection\n"
         "from mirus.serving import OnlineFetcher\n"
         "assert importlib.util.find_spec('mirus.backtest') is None\n"
         "assert importlib.util.find_spec('mirus.validation') is None\n"
         "@feature(source='loans')\n"
         "def count(rows): return len(rows)\n"
         "catalog = prepare_features()\n"
         "assert compute_features({'loans': [{}]}, catalog=catalog) == {'count': 1}\n"
         "assert not {'pyspark', 'pandas', 'pyarrow', 'pymysql', 'yaml'} & sys.modules.keys()"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)}, check=True,
        capture_output=True, text=True,
    )


def test_core_wheel_runs_feature_suite_without_optional_packages(wheels, tmp_path):
    serving, _, _ = wheels
    repository = Path(__file__).resolve().parents[2]
    with ZipFile(serving) as wheel:
        wheel.extractall(tmp_path)
    shutil.copytree(repository / "tests/features", tmp_path / "tests/features",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(repository / "tests/conftest.py", tmp_path / "tests/conftest.py")
    # Load pytest's dependencies without processing editable-install .pth files.
    test_dependencies = [path for path in sys.path
                         if Path(path).name in {"site-packages", "dist-packages"}]
    subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys, importlib.util\n"
         "from pathlib import Path\n"
         f"sys.path.extend({test_dependencies!r})\n"
         "import mirus, pytest\n"
         "assert Path(mirus.__file__).resolve().parent == Path.cwd() / 'mirus'\n"
         "assert importlib.util.find_spec('mirus.backtest') is None\n"
         "assert importlib.util.find_spec('mirus.validation') is None\n"
         "raise SystemExit(pytest.main(['tests/features', '-q', '-p', 'no:cacheprovider']))\n"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        check=True, capture_output=True, text=True,
    )


def test_offline_wheel_adds_only_offline_package_and_keeps_spark_lazy(wheels, tmp_path):
    serving, offline, _ = wheels
    with ZipFile(serving) as wheel:
        wheel.extractall(tmp_path)
    with ZipFile(offline) as wheel:
        assert_current_sources(wheel, ("mirus/backtest", "mirus/backtest/spark"))
        metadata = wheel.read(next(name for name in wheel.namelist() if name.endswith("/METADATA"))).decode()
        assert "Name: mirus-backtest\n" in metadata
        assert "Requires-Dist:mirus==0.1.0" in metadata.replace(" ", "")
        wheel.extractall(tmp_path)
    subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys\n"
         "from mirus.backtest import Backtest, compile_offline\n"
         "assert Backtest.available_backends == ('spark', 'spark.pandas')\n"
         "assert compile_offline().feature_names == ()\n"
         "assert 'pyspark' not in sys.modules"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)}, check=True,
        capture_output=True, text=True,
    )


def test_validation_wheel_is_optional_and_independent_of_backtest(wheels, tmp_path):
    serving, _, validation = wheels
    with ZipFile(serving) as wheel:
        wheel.extractall(tmp_path)
    with ZipFile(validation) as wheel:
        assert_current_sources(wheel, ("mirus/validation",))
        metadata = wheel.read(next(name for name in wheel.namelist() if name.endswith("/METADATA"))).decode()
        assert "Name: mirus-validation\n" in metadata
        assert "Requires-Dist:mirus==0.1.0" in metadata.replace(" ", "")
        wheel.extractall(tmp_path)
    subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys, importlib.util\n"
         "from mirus.payload import Field, JoinKey, Payload, PayloadSection, Relationship\n"
         "from mirus.features.decorators import feature\n"
         "from mirus.validation import validate_payload, validate_features, validate_project\n"
         "section = PayloadSection('loans', {'id': Field('string', primary_key=True),\n"
         "    'createdat': Field('timestamp', available_at=True)}, db_table='loans',\n"
         "    relationship=Relationship('one-to-many', [JoinKey('id', 'id')]))\n"
         "root = PayloadSection('payload', {'id': Field('string', primary_key=True),\n"
         "    'as_of': Field('timestamp', observation_time=True)},\n"
         "    source='request', children={'loans': section})\n"
         "payload = Payload('test', 1, root)\n"
         "@feature(source='loans')\n"
         "def count(rows) -> int: raise AssertionError('Feature executed')\n"
         "assert validate_payload(payload) is payload\n"
         "validate_features(payload)\n"
         "assert importlib.util.find_spec('mirus.backtest') is None\n"
         "assert 'mirus.features.compute' not in sys.modules\n"
         "assert not {'pyspark', 'pandas', 'pymysql', 'yaml'} & sys.modules.keys()"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)}, check=True,
        capture_output=True, text=True,
    )
