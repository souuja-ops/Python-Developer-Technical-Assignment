from pathlib import Path
import sys
import pytest

# Make part-1 importable during tests
PART_1_DIR = Path(__file__).resolve().parents[1]
if str(PART_1_DIR) not in sys.path:
    sys.path.insert(0, str(PART_1_DIR))


# Provide a minimal psycopg2.extensions shim so modules that import
# `psycopg2.extensions` at import time (but don't actually connect) can
# be imported during test collection on machines without psycopg2 installed.
# We do NOT provide a `psycopg2.connect` implementation here to avoid
# falsely reporting an available DB.
if 'psycopg2' not in sys.modules:
    import types

    psy = types.ModuleType('psycopg2')
    ext = types.ModuleType('psycopg2.extensions')
    ext.connection = object
    ext.cursor = object
    psy.extensions = ext
    sys.modules['psycopg2'] = psy
    sys.modules['psycopg2.extensions'] = ext


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require a live PostgreSQL connection",
    )
