# Part 2 — LDAP query exercise

This directory implements a small LDAP-query CLI and a test-suite suitable for
unit and integration testing. The goal is to resolve a group by `cn`, then
lookup each member's user record and print a compact table with their name and
home directory.

This README explains the layout, how to run unit tests (mocked), how to run
integration tests against a live OpenLDAP instance (Docker Compose), and how
to run the CLI locally.

## What’s in this folder

- `config.py` — configuration constants used by the LDAP client (host, ports,
  DNs). Values are written to be overridable via environment or edited for
  local testing.
- `ldap_client.py` — core logic: `get_connection()`, `lookup_group()`,
  `lookup_user()`, and `resolve_group_members()`.
- `ldap_query.py` — a small CLI wrapper that formats and prints results for a
  single group supplied as the only argument.
- `tests/` — test helpers and test suites. Notably:
  - `tests/conftest.py` contains fixtures used by tests (mock objects and an
    integration-availability fixture that will skip integration tests if a
    live LDAP server is not reachable).
  - `tests/test_ldap_query.py` contains unit tests that mock `ldap3` and
    exercise many edge cases and the CLI formatting.

## Quick prerequisites

- Python 3.8+ (this repo uses a virtualenv recorded at `.venv` in the workspace).
- Recommended: use the workspace virtualenv created previously. If you don't
  have one, create and activate a venv and install the test dependencies:

```bash
# from repo root
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r ../requirements-dev.txt  # if you keep one, otherwise:
pip install pytest ldap3
```

Note: the test-suite only requires `pytest` and `ldap3`. The unit tests are
fully mocked and do not require Docker.

## Run the unit tests (mocked)

From the repository root (or from `part-2/`), run the tests for this part with
PYTHONPATH pointing at this directory so the local modules are importable:

```bash
# from repo root
PYTHONPATH=part-2 .venv/bin/pytest -q part-2/tests

# or from inside part-2/
PYTHONPATH=$(pwd) ../.venv/bin/pytest -q tests
```

The test suite includes both unit tests (mocking `ldap3`) and a small set of
integration-aware tests; the integration-aware tests will be skipped when a
live LDAP server is not reachable.

Expected quick result (unit-only run):

```
15 passed, 2 warnings in 0.09s
```

The warnings are from a transitive `ldap3` dependency and are informational.

## Run integration tests against a Docker OpenLDAP server

If you want to run the integration tests that exercise a real LDAP server, the
`part-2/docker-compose.yml` in this repository contains a minimal OpenLDAP
service and seed LDIF (`ldap-seed.ldif`). The integration tests will attempt
to connect using the values in `part-2/config.py`.

Bring up the LDAP container:

```bash
# from part-2/
docker compose up -d

# wait for the server to start / load seed data
docker compose logs -f openldap
```

When the LDAP service is available, re-run the tests (same command as above).
The `tests/conftest.py` contains a fixture that calls `get_connection()` and
skips integration tests automatically if the bind fails.

To stop and remove the containers:

```bash
docker compose down
```

Notes and troubleshooting when using Docker:
- If your Docker host blocks the port or the seed LDIF failed to apply, check
  `docker compose logs openldap` and `docker compose ps` for container state.
- Ensure `config.py` matches the seed data (bind DN and password) if you edit
  `ldap-seed.ldif` or `docker-compose.yml`.

## CLI usage (`ldap_query.py`)

Usage: the CLI accepts a single positional argument — the group `cn` to query.

```bash
# assuming the workspace venv is active and PYTHONPATH includes part-2
PYTHONPATH=part-2 .venv/bin/python part-2/ldap_query.py developers
```

Example output (abbreviated):

```
Group: developers (gidNumber: 2001)
UID     Name                 Home directory
alice   Alice Mwangi         /home/alice
bob     Bob Otieno           /home/bob
```

The CLI returns an exit code of `0` on success and `1` on errors such as the
group not being found or a recoverable LDAP bind failure.

## Design notes

- `ldap_client.py` uses a two-step lookup strategy: server-side `cn` filter to
  find the group entry, then individual `uid` lookups to fetch user
  attributes. This limits the amount of data pulled during searches while
  keeping the logic straightforward.
- The code normalises both single-valued and multi-valued LDAP attributes so
  tests and callers safely treat member lists as Python lists.

## Troubleshooting

- Missing imports like `ldap3` or `pytest` usually mean your venv is not
  activated or packages are not installed. Activate the project's `.venv` and
  install requirements.
- If the CLI prints no users: verify the group exists in LDAP and that its
  `memberUid` attributes match the `uid` values in the `people` entries.
- If integration tests are skipped: check your Docker Compose logs and network
  connectivity; the tests intentionally skip when bind fails to avoid false
  negatives on machines without Docker or the LDAP service.

## Contributing

If you want to extend the exercise:
- Add tests for unusual attribute types (numeric homeDirectory values, empty
  strings).
- Add a bulk user lookup variant that performs a single search for all uids in
  one query (careful with timeouts for large groups).

## License

This exercise and its tests are provided under the same license as the
repository. Consult the top-level `LICENSE` file if present.

---
If you'd like, I can also add a short `make test` or GitHub Actions workflow to
run the unit tests automatically — tell me which you prefer and I'll add it.
