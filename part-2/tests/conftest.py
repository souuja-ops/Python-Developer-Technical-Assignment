"""Pytest fixtures for Part 2 LDAP query tests.

Provides mock ldap3 connection objects and sample ldap3-like entry
objects for use in unit tests. Also provides a helper to skip
integration tests when the LDAP Docker container is not available.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

import ldap_client


@pytest.fixture
def mock_ldap_connection() -> MagicMock:
    """Return a MagicMock mimicking an ldap3 Connection.

    The mock has a `.search()` method and an `.entries` attribute which
    tests can customise to simulate different server responses.
    """

    conn = MagicMock()
    conn.search = MagicMock()
    conn.entries = []
    conn.unbind = MagicMock()
    return conn


@pytest.fixture
def mock_group_entry_developers() -> SimpleNamespace:
    """Mock ldap3 entry for the developers group with two members."""

    return SimpleNamespace(
        cn=SimpleNamespace(value="developers"),
        gidNumber=SimpleNamespace(value="2001"),
        memberUid=SimpleNamespace(value=["alice", "bob"]),
    )


@pytest.fixture
def mock_group_entry_hr() -> SimpleNamespace:
    """Mock ldap3 entry for the hr group with a single-member memberUid as string."""

    return SimpleNamespace(
        cn=SimpleNamespace(value="hr"),
        gidNumber=SimpleNamespace(value="2004"),
        memberUid=SimpleNamespace(value="grace"),
    )


@pytest.fixture
def mock_user_alice() -> SimpleNamespace:
    """Mock ldap3 user entry for alice."""

    return SimpleNamespace(
        uid=SimpleNamespace(value="alice"),
        cn=SimpleNamespace(value="Alice Mwangi"),
        homeDirectory=SimpleNamespace(value="/home/alice"),
    )


@pytest.fixture
def mock_user_bob() -> SimpleNamespace:
    """Mock ldap3 user entry for bob."""

    return SimpleNamespace(
        uid=SimpleNamespace(value="bob"),
        cn=SimpleNamespace(value="Bob Otieno"),
        homeDirectory=SimpleNamespace(value="/home/bob"),
    )


@pytest.fixture
def ldap_integration_available() -> bool:
    """Return True if a live LDAP connection can be established.

    Used to skip integration tests when the LDAP Docker container is not running.
    """

    try:
        ldap_client.get_connection().unbind()
        return True
    except Exception:
        pytest.skip("LDAP container not running; skipping integration tests")
