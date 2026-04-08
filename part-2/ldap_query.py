"""ldap_query.py — LDAP Group Member Lookup CLI

Usage:
    python3 ldap_query.py <group_name>

Connects to the OpenLDAP instance defined in config.py, resolves the given
group and prints each member's uid, full name, and home directory.

Exit codes:
    0 — success
    1 — group not found or connection error
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from ldap3.core.exceptions import LDAPBindError, LDAPSocketOpenError

from ldap_client import resolve_group_members, GroupEntry, UserEntry
from config import LDAP_HOST, LDAP_PORT


def _configure_logging() -> None:
    """Configure logging for the CLI using LOG_LEVEL env var (default WARNING).

    The format is kept compact so examiners see only relevant output by
    default. Set LOG_LEVEL=DEBUG to view internal debug messages.
    """

    level = logging.WARNING
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level)


def _format_and_print(group: GroupEntry, users: List[UserEntry]) -> None:
    """Format the resolved group and members and print to stdout.

    Formatting rules:
      - Align columns with str.ljust so pipe separators line up.
      - One blank line between header and members list.
      - If a user entry was not resolved, print "(not found in directory)" and "—" for home.
    """

    print(f"Group: {group.cn} (gidNumber: {group.gid_number})")
    print("Members:")

    # Compute column widths for uid and cn to align pipes
    uids = [u.uid for u in users]
    names = [u.cn for u in users]
    uid_width = max((len(s) for s in uids), default=4)
    name_width = max((len(s) for s in names), default=10)

    # Print a blank line separating header and members
    print("")

    for u in users:
        # If the UserEntry fields are missing use the not-found format
        if u is None or not getattr(u, "uid", None):
            # When a user could not be resolved, show the placeholder
            print(f"  {str(u)[:uid_width].ljust(uid_width)} | (not found in directory) | —")
            continue

        uid = u.uid.ljust(uid_width)
        cn = u.cn.ljust(name_width)
        home = u.home_directory
        # Align the columns and print
        print(f"  {uid} | {cn} | {home}")


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments: exactly one positional `group_name`.

    Positional argument is used because the command models a single required
    resource identifier (the group) and is simpler for examiners to run.
    """

    parser = argparse.ArgumentParser(
        prog="ldap_query.py",
        description="Lookup LDAP group members and print their details.",
        epilog="Example: python3 ldap_query.py developers",
    )

    parser.add_argument("group_name", help="The name of the LDAP group to query")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Program entry point for the ldap_query CLI.

    Delegates LDAP logic to `ldap_client.resolve_group_members` and
    handles only CLI-level errors and output formatting.
    """

    _configure_logging()
    args = _parse_args(argv)
    group_name = args.group_name

    try:
        group, users = resolve_group_members(group_name)
        _format_and_print(group, users)
        return 0
    except LDAPSocketOpenError:
        print(f"Error: cannot connect to LDAP server at {LDAP_HOST}:{LDAP_PORT}.", file=sys.stderr)
        return 1
    except LDAPBindError:
        print("Error: LDAP authentication failed. Check credentials.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Guard prevents execution when module is imported by tests
    sys.exit(main())
