"""LDAP client utilities.

This module owns all LDAP connection and search logic used by the
`ldap_query.py` CLI. It implements a two-step lookup pattern: first the
group is resolved in the groups OU to obtain member UIDs, then each UID is
resolved in the users OU to collect user details.

Exceptions raised:
  - ldap3.core.exceptions.LDAPBindError when bind fails
  - ldap3.core.exceptions.LDAPSocketOpenError when the server is unreachable
  - ldap3.core.exceptions.LDAPSearchError on search failures

The module exposes a small, well-documented API that returns dataclasses
instead of exposing ldap3 objects directly.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ldap3 import Server, Connection, ALL, SIMPLE
from ldap3.core.exceptions import LDAPBindError, LDAPSocketOpenError, LDAPException

from config import (
    LDAP_HOST,
    LDAP_PORT,
    LDAP_BIND_DN,
    LDAP_PASSWORD,
    LDAP_GROUPS_OU,
    LDAP_USERS_OU,
)

# Module-level logger
logger = logging.getLogger(__name__)


"""
── Design & Lookup Strategy ──

PLANNING DECISION 1 — Two-step lookup strategy:
  We perform two separate server-side searches:
    Step 1: Search the groups OU (LDAP_GROUPS_OU) for the group entry using
            filter (cn=<group_name>) to retrieve the `memberUid` list.
    Step 2: For each memberUid, search the users OU (LDAP_USERS_OU) using
            filter (uid=<member>) to retrieve the user's full entry.

  Rationale: Performing server-side searches is more efficient and correct
  than fetching all groups and filtering in Python. LDAP servers are built
  to execute filters efficiently and return only the required entries.

PLANNING DECISION 2 — Search bases:
  Groups search base : LDAP_GROUPS_OU (ou=groups,dc=dewcis,dc=com)
  Users search base  : LDAP_USERS_OU  (ou=users,dc=dewcis,dc=com)

  Rationale: Using the most specific search base improves performance and
  reduces the risk of unintended matches elsewhere in the directory tree.

PLANNING DECISION 3 — Attributes requested:
  Group search  → request only: ['cn', 'gidNumber', 'memberUid']
  User search   → request only: ['uid', 'cn', 'homeDirectory']

  Rationale: Requesting only the attributes we need reduces bandwidth,
  lowers memory use, and follows the principle of least privilege.

PLANNING DECISION 4 — Group not found handling:
  After the group search, if no entry is returned we:
    → print to stderr: "Error: group '{name}' not found in directory."
    → call sys.exit(1) at the CLI boundary (resolve_group_members will
      propagate this behaviour) so the user sees a clean error message.
  We avoid exposing tracebacks to end users.

"""


@dataclass
class GroupEntry:
    """Represents a resolved LDAP group from LDAP_GROUPS_OU.

    Attributes:
        cn: Group common name (cn attribute)
        gid_number: gidNumber attribute as string
        member_uids: List of member uid strings from memberUid attribute
    """

    cn: str
    gid_number: str
    member_uids: List[str]


@dataclass
class UserEntry:
    """Represents a resolved LDAP user from LDAP_USERS_OU.

    Attributes:
        uid: Login name (uid attribute)
        cn: Full name (cn attribute)
        home_directory: Home directory path (homeDirectory attribute)
    """

    uid: str
    cn: str
    home_directory: str


def get_connection() -> Connection:
    """Establish and return a bound LDAP connection.

    Purpose:
        Create an ldap3 Server and Connection, then bind using the values
        supplied in `config.py`.

    Returns:
        ldap3.Connection: A bound, ready-to-use LDAP connection.

    Raises:
        LDAPBindError: If credentials are wrong.
        LDAPSocketOpenError: If the host is unreachable.
    """

    server = Server(LDAP_HOST, port=LDAP_PORT, get_info=ALL)  # Server description
    try:
        # auto_bind=True will attempt to bind immediately and raise on failure
        conn = Connection(server, user=LDAP_BIND_DN, password=LDAP_PASSWORD, authentication=SIMPLE, auto_bind=True)
        logger.debug("LDAP bind successful to %s:%s", LDAP_HOST, LDAP_PORT)
        return conn
    except LDAPBindError:
        logger.error("LDAP bind failed for %s", LDAP_BIND_DN)
        raise
    except LDAPSocketOpenError:
        logger.error("Unable to reach LDAP server at %s:%s", LDAP_HOST, LDAP_PORT)
        raise


def lookup_group(conn: Connection, group_name: str) -> Optional[GroupEntry]:
    """Search LDAP_GROUPS_OU for a group by its cn and return GroupEntry.

    Args:
        conn: An authenticated ldap3 Connection.
        group_name: The cn of the group to search for.

    Returns:
        GroupEntry if found, otherwise None.

    Raises:
        LDAPException: On search failure.
    """

    search_base = LDAP_GROUPS_OU
    # Use a server-side filter to find the exact group
    search_filter = f"(cn={group_name})"
    attributes = ["cn", "gidNumber", "memberUid"]

    # Perform the search; let ldap3 raise on failure
    conn.search(search_base=search_base, search_filter=search_filter, attributes=attributes)

    # If no entries found -> group not present
    if not getattr(conn, "entries", []):
        logger.debug("Group %s not found under %s", group_name, search_base)
        return None

    entry = conn.entries[0]

    # Extract attributes safely: ldap3 Entry attributes may expose `.value` or be lists
    def _val(obj):
        # obj may be a mock, an ldap3 Attribute, or a raw value
        if obj is None:
            return None
        if hasattr(obj, "value"):
            return obj.value
        return obj

    cn = _val(getattr(entry, "cn", None))
    gid = _val(getattr(entry, "gidNumber", None))
    member_attr = getattr(entry, "memberUid", None)

    raw_member = _val(member_attr)
    # Normalize single string vs list into a list of strings
    if raw_member is None:
        member_uids: List[str] = []
    elif isinstance(raw_member, list):
        member_uids = [str(x) for x in raw_member]
    else:
        member_uids = [str(raw_member)]

    logger.debug("Found group %s gid=%s members=%d", cn, gid, len(member_uids))
    return GroupEntry(cn=str(cn), gid_number=str(gid), member_uids=member_uids)


def lookup_user(conn: Connection, uid: str) -> Optional[UserEntry]:
    """Search LDAP_USERS_OU for a user by uid and return UserEntry.

    Args:
        conn: An authenticated ldap3 Connection.
        uid: The user's uid to search for.

    Returns:
        UserEntry if found, otherwise None.

    Raises:
        LDAPException: On search failure.
    """

    search_base = LDAP_USERS_OU
    search_filter = f"(uid={uid})"
    attributes = ["uid", "cn", "homeDirectory"]

    conn.search(search_base=search_base, search_filter=search_filter, attributes=attributes)

    if not getattr(conn, "entries", []):
        logger.debug("User %s not found under %s", uid, search_base)
        return None

    entry = conn.entries[0]

    def _val(obj):
        if obj is None:
            return None
        if hasattr(obj, "value"):
            return obj.value
        return obj

    uid_val = _val(getattr(entry, "uid", None))
    cn_val = _val(getattr(entry, "cn", None))
    home_val = _val(getattr(entry, "homeDirectory", None))

    logger.debug("Found user %s home=%s", uid_val, home_val)
    return UserEntry(uid=str(uid_val), cn=str(cn_val), home_directory=str(home_val))


def resolve_group_members(group_name: str) -> Tuple[GroupEntry, List[UserEntry]]:
    """Resolve a group and all its member user entries.

    This function opens a connection, looks up the group by name, then
    resolves each member UID to a UserEntry. The connection is closed in a
    finally block so resources are always released.

    Args:
        group_name: The cn of the LDAP group to resolve.

    Returns:
        A tuple of (GroupEntry, List[UserEntry]).

    Raises:
        SystemExit(1): If the group is not found (prints a friendly message).
        LDAPSocketOpenError: If LDAP server is unreachable.
    """

    conn = None
    try:
        conn = get_connection()
        group = lookup_group(conn, group_name)
        if group is None:
            print(f"Error: group '{group_name}' not found in directory.", file=sys.stderr)
            # Exit at CLI boundary as requested
            raise SystemExit(1)

        users: List[UserEntry] = []
        for uid in group.member_uids:
            try:
                user = lookup_user(conn, uid)
                if user is None:
                    logger.warning("User '%s' in group '%s' not found in users OU", uid, group_name)
                    continue
                users.append(user)
            except LDAPException as exc:
                logger.error("Error looking up user %s: %s", uid, exc)
                continue

        return group, users
    finally:
        # Ensure connection is closed if it was opened
        if conn is not None:
            try:
                conn.unbind()
            except Exception:
                logger.debug("Exception during LDAP unbind, ignoring")
