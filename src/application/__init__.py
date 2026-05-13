"""Application Services layer — orchestrates domain objects and repositories.

Rules (DDD Rule 3):
  - Application services are the single entry point for all use cases.
  - They load aggregates from repositories, call domain methods, and persist results.
  - They never import FastAPI, HTTPException, Request, or any HTTP primitive.
  - They never import sqlite3 or any I/O library directly.
  - They never read from os.environ.

Services in this package:
  DiscoveryService  — manages the L2 discovery candidate lifecycle
  AccountService    — manages KB account CRUD
"""
