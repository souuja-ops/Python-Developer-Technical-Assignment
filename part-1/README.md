# File Archiving System — Examiner Guide

This README describes how to run the File Archiving System and the
Debian package that bundles the command-line archiver.

Prerequisites
-------------

	- Docker and Docker Compose (verify: `docker compose version`)
	- Python 3.10+ (verify: `python3 --version`)
	- pip packages: `fastapi uvicorn psycopg2-binary pytest`

Step 1 — Start the environment
------------------------------

	docker compose up -d
	docker compose ps
	Expected: all three services show healthy / running
	Services: postgres, pgadmin, testenv

Step 2 — Run the archiver (first time)
-------------------------------------

	docker compose exec testenv python3 archive_files.py --group developers
	Expected output:
		[MOVED] /home/alice/... → /archive/home/alice/...
		... (16 lines total)
		── Archive complete ──────────────────
		Group   : developers
		Run ID  : 1
		Moved   : 16
		Skipped : 0
		Errors  : 0
		Duration: x.xxxs
		──────────────────────────────────────

Step 3 — Verify the database
----------------------------

	Open pgAdmin: http://localhost:5050
	Login: admin@dewcis.com / adminpass
	Run these SQL queries:
		SELECT * FROM archive_runs;
		SELECT * FROM archive_events WHERE run_id = 1 LIMIT 10;
	Expected: 1 run row with status='completed', 16 event rows with status='moved'

Step 4 — Start the FastAPI service
----------------------------------

	uvicorn main:app --reload --port 8000
	Auto-docs: http://localhost:8000/docs
	Example curl commands with expected JSON responses:
		curl http://localhost:8000/runs
		curl http://localhost:8000/runs/1
		curl http://localhost:8000/stats
		curl "http://localhost:8000/runs/1/files?status=moved"
		curl http://localhost:8000/runs/99999   ← expect 404

Step 5 — Open the dashboard
---------------------------

	URL: http://localhost:8000/
	Expected: summary bar shows 1 run, 16 archived, 0 skipped, 0 errors.
	Runs table shows one row for the developers group.
	Click the row to see all 16 file events in the detail panel.

Step 6 — Run the archiver a second time
--------------------------------------

	docker compose exec testenv python3 archive_files.py --group developers
	Expected output: 16 [SKIPPED] lines, Moved=0, Skipped=16
	Dashboard: new row appears within 10 seconds (auto-refresh),
	second run shows status='completed', moved=0, skipped=16.

Step 7 — Build and install the Debian package
---------------------------------------------

	# Make the binary executable first
	docker compose exec testenv chmod +x debian-pkg/usr/local/bin/archive-files
	# Build the .deb package
	docker compose exec testenv dpkg-deb --build debian-pkg archive-files.deb
	# Install it
	docker compose exec testenv dpkg -i archive-files.deb
	# Verify it runs from PATH
	docker compose exec testenv archive-files --group ops
	Expected: ops group archived (carol + david files), new run visible in dashboard.

Running the test suite
----------------------

	pip install pytest
	pytest tests/ -v
	Expected: all unit tests pass; integration tests skip if DB unavailable.

Submission checklist
--------------------

	- [ ] archive_files.py
	- [ ] main.py, models.py, db.py, config.py
	- [ ] static/index.html
	- [ ] tests/test_archiver.py + tests/conftest.py
	- [ ] debian-pkg/ directory + archive-files.deb
	- [ ] docker-compose.yml
	- [ ] README.md
