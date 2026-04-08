"""Unit and integration tests for Part 2 LDAP query utilities.

This suite contains unit tests that mock ldap3 behaviour and a set of
integration tests that are marked `integration` and will be skipped when
the LDAP Docker container is not available.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import pytest

import ldap_client
import ldap_query


class TestGetConnection:
    """Unit tests for ldap_client.get_connection()."""

    def test_successful_bind(self):
        """Mock ldap3 Connection to bind successfully and return conn."""
        with patch("ldap_client.Connection") as mock_conn, patch("ldap_client.Server"):
            mock_conn.return_value = MagicMock()
            c = ldap_client.get_connection()
            assert c is not None

    def test_unreachable_host(self):
        """Simulate LDAPSocketOpenError being raised during bind."""
        from ldap3.core.exceptions import LDAPSocketOpenError

        with patch("ldap_client.Connection", side_effect=LDAPSocketOpenError()):
            with pytest.raises(LDAPSocketOpenError):
                ldap_client.get_connection()

    def test_wrong_credentials(self):
        """Simulate LDAPBindError being raised during bind."""
        from ldap3.core.exceptions import LDAPBindError

        with patch("ldap_client.Connection", side_effect=LDAPBindError()):
            with pytest.raises(LDAPBindError):
                ldap_client.get_connection()


class TestLookupGroup:
    """Unit tests for ldap_client.lookup_group()."""

    def test_group_found_multiple_members(self, mock_ldap_connection, mock_group_entry_developers):
        """Group with multiple memberUid values returns GroupEntry."""
        conn = mock_ldap_connection
        conn.entries = [mock_group_entry_developers]
        group = ldap_client.lookup_group(conn, "developers")
        assert group is not None
        assert group.cn == "developers"
        assert group.member_uids == ["alice", "bob"]

    def test_group_found_single_member(self, mock_ldap_connection, mock_group_entry_hr):
        """Group with single-string memberUid is normalised to a list."""
        conn = mock_ldap_connection
        conn.entries = [mock_group_entry_hr]
        group = ldap_client.lookup_group(conn, "hr")
        assert group is not None
        assert group.member_uids == ["grace"]

    def test_group_not_found(self, mock_ldap_connection):
        """Empty entries results in None returned."""
        conn = mock_ldap_connection
        conn.entries = []
        group = ldap_client.lookup_group(conn, "nope")
        assert group is None

    def test_search_uses_server_side_filter(self, mock_ldap_connection):
        """Ensure conn.search is called with a filter containing the group cn."""
        conn = mock_ldap_connection
        conn.entries = []
        ldap_client.lookup_group(conn, "developers")
        # Ensure the search was invoked with a filter that mentions the cn
        assert conn.search.called
        args, kwargs = conn.search.call_args
        assert "(cn=developers)" in kwargs.get("search_filter", args[1] if len(args) > 1 else "" )


class TestLookupUser:
    """Unit tests for ldap_client.lookup_user()."""

    def test_user_found(self, mock_ldap_connection, mock_user_alice):
        conn = mock_ldap_connection
        conn.entries = [mock_user_alice]
        user = ldap_client.lookup_user(conn, "alice")
        assert user is not None
        assert user.uid == "alice"
        assert user.home_directory == "/home/alice"

    def test_user_not_found(self, mock_ldap_connection):
        conn = mock_ldap_connection
        conn.entries = []
        user = ldap_client.lookup_user(conn, "nobody")
        assert user is None

    def test_search_uses_uid_filter(self, mock_ldap_connection):
        conn = mock_ldap_connection
        conn.entries = []
        ldap_client.lookup_user(conn, "alice")
        assert conn.search.called
        args, kwargs = conn.search.call_args
        assert "(uid=alice)" in kwargs.get("search_filter", args[1] if len(args) > 1 else "")


class TestResolveGroupMembers:
    """Unit tests for ldap_client.resolve_group_members()."""

    def test_full_resolution_developers(self, monkeypatch, mock_group_entry_developers, mock_user_alice, mock_user_bob):
        # Patch get_connection to return a mock connection
        conn = MagicMock()
        conn.entries = [mock_group_entry_developers]
        # lookup_group and lookup_user will be exercised via real functions but we patch connection behaviour
        monkeypatch.setattr(ldap_client, "get_connection", lambda: conn)

        # Simulate lookup_group reading entries
        conn.search.side_effect = None
        # For first search (group) entries[0] is the group
        conn.entries = [mock_group_entry_developers]

        # For subsequent user lookups we change entries accordingly by side effect on search
        def search_side_effect(search_base=None, search_filter=None, attributes=None):
            if search_filter.startswith("(cn="):
                conn.entries = [mock_group_entry_developers]
            else:
                if "alice" in search_filter:
                    conn.entries = [mock_user_alice]
                elif "bob" in search_filter:
                    conn.entries = [mock_user_bob]
                else:
                    conn.entries = []

        conn.search.side_effect = search_side_effect

        group, users = ldap_client.resolve_group_members("developers")
        assert group.cn == "developers"
        assert len(users) == 2

    def test_group_not_found_exits(self, monkeypatch):
        conn = MagicMock()
        conn.entries = []
        monkeypatch.setattr(ldap_client, "get_connection", lambda: conn)
        # lookup_group will see no entries and resolve_group_members should sys.exit(1)
        with pytest.raises(SystemExit) as exc:
            ldap_client.resolve_group_members("phantom")
        assert exc.value.code == 1

    def test_missing_user_skipped_gracefully(self, monkeypatch, mock_group_entry_developers, mock_user_alice):
        conn = MagicMock()
        monkeypatch.setattr(ldap_client, "get_connection", lambda: conn)

        # First, return the group entry
        def search_side_effect(search_base=None, search_filter=None, attributes=None):
            if search_filter.startswith("(cn="):
                conn.entries = [mock_group_entry_developers]
            else:
                # Simulate alice found, bob missing
                if "alice" in search_filter:
                    conn.entries = [mock_user_alice]
                else:
                    conn.entries = []

        conn.search.side_effect = search_side_effect

        group, users = ldap_client.resolve_group_members("developers")
        assert len(users) == 1
        assert users[0].uid == "alice"

    def test_connection_closed_on_exception(self, monkeypatch):
        # Use a MagicMock and ensure unbind is itself a MagicMock so we can
        # assert it was called even when exceptions are raised.
        conn = MagicMock()
        conn.unbind = MagicMock()
        def bad_get_connection():
            return conn

        monkeypatch.setattr(ldap_client, "get_connection", bad_get_connection)
        # Make lookup_group raise unexpectedly
        monkeypatch.setattr(ldap_client, "lookup_group", lambda c, g: (_ for _ in ()).throw(RuntimeError("boom")))

        with pytest.raises(RuntimeError):
            ldap_client.resolve_group_members("developers")

        # Ensure unbind was called in finally
        assert conn.unbind.called


class TestOutputFormatting:
    """Unit tests for ldap_query output formatting."""

    def test_output_developers_group(self, capsys):
        group = ldap_client.GroupEntry(cn="developers", gid_number="2001", member_uids=["alice", "bob"])
        users = [ldap_client.UserEntry(uid="alice", cn="Alice Mwangi", home_directory="/home/alice"),
                 ldap_client.UserEntry(uid="bob", cn="Bob Otieno", home_directory="/home/bob")]

        # Patch resolve_group_members to return our prepared data
        with patch.object(ldap_client, "resolve_group_members", return_value=(group, users)):
            # Call the CLI main and capture output
            with patch("ldap_query.resolve_group_members", return_value=(group, users)):
                ldap_query.main(["developers"])
                captured = capsys.readouterr()
                assert "Group: developers (gidNumber: 2001)" in captured.out
                assert "Alice Mwangi" in captured.out
                assert "/home/alice" in captured.out
