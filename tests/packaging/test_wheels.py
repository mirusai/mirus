"""Check actual distribution contents and isolated serving imports, not just extras."""

import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest


@pytest.fixture
def wheels():
    directory = os.getenv("MIRUS_WHEEL_DIR")
    if not directory:
        pytest.skip("set MIRUS_WHEEL_DIR to locally built mirus and offline wheels")
    root = Path(directory)
    return next(root.glob("mirus-*.whl")), next(root.glob("mirus_backtest-*.whl"))


def test_serving_wheel_excludes_offline_files_and_runs_without_site_packages(wheels, tmp_path):
    serving, _ = wheels
    repository = Path(__file__).resolve().parents[2]
    with ZipFile(serving) as wheel:
        names = wheel.namelist()
        assert not any(name.startswith("core/") for name in names)
        metadata = wheel.read(next(name for name in names if name.endswith("/METADATA"))).decode()
        assert "Name: mirus\n" in metadata
        assert "mirus-backtest[spark]" in metadata
        assert "mirus/features/compute.py" in names
        assert "mirus/payload/model.py" in names
        assert "mirus/serving/mysql.py" in names
        assert not any(name.startswith(("mirus/backtest/", "mirus/offline/", "mirus/retrieval/", "mirus/fetcher/", "mirus/features/online/",
                                       "mirus/features/offline/")) for name in names)
        wheel.extractall(tmp_path)
    subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys, importlib.util\n"
         "from pathlib import Path\n"
         "import mirus\n"
         "assert Path(mirus.__file__).resolve().parent == Path.cwd() / 'mirus'\n"
         "from mirus import feature, prepare_features, compute_features\n"
         "from mirus.payload import Payload, Field, PayloadSection\n"
         "from mirus.serving import OnlineFetcher\n"
         "assert importlib.util.find_spec('mirus.backtest') is None\n"
         "assert importlib.util.find_spec('mirus.offline') is None\n"
         "assert importlib.util.find_spec('mirus.retrieval') is None\n"
         "assert importlib.util.find_spec('core') is None\n"
         "@feature(source='loans')\n"
         "def count(rows): return len(rows)\n"
         "catalog = prepare_features()\n"
         "assert compute_features({'loans': [{}]}, ['count'], catalog=catalog) == {'count': 1}\n"
         "from examples.serve_features import score_payload\n"
         "assert score_payload({'loans': [{'principal': 700}]}, ['loan_count']) == {'loan_count': 1}\n"
         "assert not {'pyspark', 'pandas', 'pyarrow', 'pymysql', 'yaml'} & sys.modules.keys()"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": os.pathsep.join((str(tmp_path), str(repository)))}, check=True,
        capture_output=True, text=True,
    )


def test_offline_wheel_adds_only_offline_package_and_keeps_spark_lazy(wheels, tmp_path):
    serving, offline = wheels
    with ZipFile(serving) as wheel:
        wheel.extractall(tmp_path)
    with ZipFile(offline) as wheel:
        metadata = wheel.read(next(name for name in wheel.namelist() if name.endswith("/METADATA"))).decode()
        assert "Name: mirus-backtest\n" in metadata
        assert "Requires-Dist:mirus==0.1.0" in metadata.replace(" ", "")
        source_files = [name for name in wheel.namelist() if name.endswith(".py")]
        assert source_files
        assert all(name.startswith("mirus/backtest/") for name in source_files)
        assert "mirus/backtest/interface.py" in source_files
        assert "mirus/backtest/spark/fetcher.py" in source_files
        wheel.extractall(tmp_path)
    subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys\n"
         "from mirus.backtest import OfflineFetcher, compile_offline\n"
         "from mirus import compile_offline as public_compile\n"
         "assert public_compile is compile_offline\n"
         "assert compile_offline().feature_names == ()\n"
         "assert 'pyspark' not in sys.modules"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)}, check=True,
        capture_output=True, text=True,
    )
