"""Fresh-process checks for serving and optional offline import boundaries."""

import subprocess
import sys


def test_online_feature_imports_do_not_load_offline_engines():
    subprocess.run(
        [sys.executable, "-c",
         "import mirus, sys; "
         "assert not {'pyspark', 'pandas', 'pymysql'} & sys.modules.keys()"],
        check=True,
    )


def test_decorator_import_does_not_load_online_or_offline_runtime():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from mirus import feature; "
            "assert 'mirus.features.compiler' not in sys.modules; "
            "assert 'mirus.features.compute' not in sys.modules; "
            "assert not any(name.startswith('mirus.backtest') "
            "for name in sys.modules)",
        ],
        check=True,
    )


def test_registry_is_created_only_when_first_used():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from mirus import feature\n"
            "from mirus.features import registry\n"
            "assert registry._default_registry is None\n"
            "@feature(source='loans')\n"
            "def count(rows) -> int: return len(rows)\n"
            "assert registry._default_registry is not None",
        ],
        check=True,
    )


def test_online_feature_api_does_not_import_offline_feature_modules():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from mirus.features import prepare_features; "
            "assert not any(name.startswith('mirus.backtest') "
            "for name in sys.modules)",
        ],
        check=True,
    )


def test_main_compute_module_does_not_import_offline_modules():
    subprocess.run(
        [sys.executable, "-c",
         "import sys; from mirus.features.compute import compute_features; "
         "assert compute_features({}) == {}; "
         "assert not any(name.startswith('mirus.backtest') for name in sys.modules); "
         "assert not {'pyspark', 'pandas'} & sys.modules.keys()"],
        check=True,
    )


def test_offline_compiler_does_not_import_online_runtime_or_spark():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from mirus.backtest import compile_offline; "
            "assert 'mirus.features.compute' not in sys.modules; "
            "assert 'pyspark' not in sys.modules",
        ],
        check=True,
    )
