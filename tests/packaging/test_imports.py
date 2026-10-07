"""Fresh-process checks for serving and optional offline import boundaries."""

import subprocess
import sys


def test_public_apis_are_separated_by_responsibility():
    subprocess.run(
        [sys.executable, "-c",
         "import mirus, mirus.features\n"
         "from mirus.features.decorators import feature, field\n"
         "from mirus.features.compute import compute_features, prepare_features\n"
         "from mirus.features import compute, compiler, decorators\n"
         "assert compute_features is compute.compute_features\n"
         "assert prepare_features is compiler.prepare_features\n"
         "assert feature is decorators.feature\n"
         "assert field is decorators.field\n"
         "for name in ('feature', 'field', 'compute_features', 'prepare_features', 'compile_offline'):\n"
         "    assert not hasattr(mirus, name), name\n"
         "    assert not hasattr(mirus.features, name), name\n"
         "assert not hasattr(__import__('mirus.features.compute', fromlist=['feature']), 'feature')\n"
         "import sys; assert not {'pyspark', 'pandas', 'pyarrow', 'pymysql', 'yaml'} & sys.modules.keys()\n"],
        check=True,
    )


def test_decorator_import_does_not_load_online_or_offline_runtime():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from mirus.features.decorators import feature; "
            "assert 'mirus.features.compiler' not in sys.modules; "
            "assert 'mirus.features.compute' not in sys.modules; "
            "assert not any(name.startswith('mirus.backtest') "
            "for name in sys.modules)",
        ],
        check=True,
    )


def test_registry_starts_empty_and_collects_on_decoration():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from mirus.features import registry\n"
            "from mirus.features.decorators import feature\n"
            "assert registry.default_registry.snapshot() == ((), ())\n"
            "@feature(source='loans')\n"
            "def count(rows) -> int: return len(rows)\n"
            "fields, features = registry.default_registry.snapshot()\n"
            "assert fields == () and len(features) == 1",
        ],
        check=True,
    )


def test_main_compute_module_does_not_import_offline_modules():
    subprocess.run(
        [sys.executable, "-c",
         "import sys; from mirus.features.compute import compute_features, prepare_features; "
         "assert prepare_features().features_by_name == {}; "
         "assert compute_features({}) == {}; "
         "assert not any(name.startswith('mirus.backtest') for name in sys.modules); "
         "assert not any(name.startswith('mirus.validation') for name in sys.modules); "
         "assert not {'pyspark', 'pandas', 'pyarrow', 'pymysql', 'yaml'} & sys.modules.keys()"],
        check=True,
    )


def test_validation_import_does_not_load_compute_or_backend_engines():
    subprocess.run(
        [sys.executable, "-c",
         "import sys; from mirus.validation import validate_project; "
         "assert 'mirus.features.compute' not in sys.modules; "
         "assert not any(name.startswith(('mirus.backtest', 'mirus.serving')) for name in sys.modules); "
         "assert not {'pyspark', 'pandas', 'pymysql', 'yaml'} & sys.modules.keys()"],
        check=True,
    )


def test_offline_compiler_does_not_import_online_runtime_or_spark():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from mirus.backtest import compile_offline, OfflinePlan; "
            "from mirus.backtest import compiler, plan; "
            "assert compile_offline is compiler.compile_offline; "
            "assert OfflinePlan is plan.OfflinePlan; "
            "assert 'mirus.features.compute' not in sys.modules; "
            "assert 'pyspark' not in sys.modules",
        ],
        check=True,
    )


def test_backtest_import_does_not_load_spark_or_yaml():
    subprocess.run(
        [sys.executable, "-c",
         "import sys; from mirus.backtest import Backtest; "
         "assert Backtest.available_backends == ('spark', 'spark.pandas'); "
         "assert not {'pyspark', 'pandas', 'pyarrow', 'yaml'} & sys.modules.keys()"],
        check=True,
    )
