# Python Developer Technical Assignment
**Ref: 60/2026 | Dew CIS Solutions Limited | Westlands, Nairobi**

A Python technical assessment implementing a File Archiving System and an LDAP Query tool.

---

## Stack
Python 3.10+ · PostgreSQL · FastAPI · Docker · ldap3

## Structure

 part1 - # File Archiving System — CLI, PostgreSQL, FastAPI, Dashboard
 
 
 part2 - # LDAP Query Tool — OpenLDAP, group/member resolution

## Quick Start

**Part 1**
```bash
cd part1
docker compose up -d
docker compose exec testenv python3 archive_files.py --group developers
uvicorn main:app --reload --port 8000
```

**Part 2**
```bash
cd part2
docker compose up -d && sleep 10
python3 ldap_query.py developers
```

## Docs
- Part 1 guide → `part1/README.md`
- Part 2 guide → `part2/README.md`

---

*Candidate: Samuel | [GitHub](https://github.com/souuja-ops)*
