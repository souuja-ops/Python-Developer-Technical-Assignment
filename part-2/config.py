"""LDAP connection configuration.

This module centralises all LDAP connection constants. Values can be
overridden by environment variables when running in different environments.

Do NOT hardcode credentials in other modules; import these constants from
`config.py` instead so tests can override them via environment variables.
"""

from __future__ import annotations

import os

# LDAP host (default: localhost)
LDAP_HOST = os.getenv("LDAP_HOST", "localhost")  # LDAP server hostname

# LDAP port (default: 3389)
LDAP_PORT = int(os.getenv("LDAP_PORT", "3389"))  # LDAP server port as int

# DN used to bind to the directory (default: admin bind DN)
LDAP_BIND_DN = os.getenv("LDAP_BIND_DN", "cn=admin,dc=dewcis,dc=com")  # Bind DN

# Password for the bind DN (default: adminpass)
LDAP_PASSWORD = os.getenv("LDAP_PASSWORD", "adminpass")  # Bind password

# Root DN of the directory (default: dewcis root)
LDAP_ROOT_DN = os.getenv("LDAP_ROOT_DN", "dc=dewcis,dc=com")  # Directory root

# Search base for groups (most specific base for groups)
LDAP_GROUPS_OU = os.getenv("LDAP_GROUPS_OU", "ou=groups,dc=dewcis,dc=com")  # Groups OU

# Search base for users (most specific base for users)
LDAP_USERS_OU = os.getenv("LDAP_USERS_OU", "ou=users,dc=dewcis,dc=com")  # Users OU
