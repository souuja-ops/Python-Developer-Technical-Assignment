"""
Configuration module for the Python File Archiving System.

All configuration values are environment-driven and never hardcoded.
This ensures the application can be deployed across different environments
without code changes by simply adjusting environment variables.
"""

import os

# Database connection settings
DB_HOST = os.getenv("DB_HOST", "localhost")        # Database server hostname
DB_PORT = int(os.getenv("DB_PORT", "5432"))        # Database server port number
DB_NAME = os.getenv("DB_NAME", "archivedb")        # Database name to connect to
DB_USER = os.getenv("DB_USER", "archiveuser")      # Database username for authentication
DB_PASSWORD = os.getenv("DB_PASSWORD", "archivepass")  # Database password for authentication

# File system settings
ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "/archive")  # Root directory for archived files

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")        # Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
