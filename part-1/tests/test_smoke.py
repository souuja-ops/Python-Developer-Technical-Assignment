def test_import_archive_files(monkeypatch):
    # Prevent psycopg2 import errors by inserting a dummy module before import.
    import types
    import sys

    # Provide a minimal fake psycopg2 package so importing modules that
    # reference psycopg2 at import-time does not fail. We only need the
    #  submodule and a placeholder  function for tests.
    psy = types.ModuleType('psycopg2')
    ext = types.ModuleType('psycopg2.extensions')
    ext.connection = object
    ext.cursor = object
    psy.extensions = ext

    def _fake_connect(*args, **kwargs):
        class DummyConn:
            def close(self):
                return None

        return DummyConn()

    psy.connect = _fake_connect
    sys.modules['psycopg2'] = psy
    sys.modules['psycopg2.extensions'] = ext
    import importlib

    archive_files = importlib.import_module('archive_files')
    assert hasattr(archive_files, 'main')
