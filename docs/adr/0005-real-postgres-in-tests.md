# Real Postgres in every test run, via testcontainers

Tests that touch the store run against a throwaway `pgvector` container started by testcontainers, on the Mac as well as the tower; there is no in-memory store fake. The core feature is a hybrid retrieval query that exists only as SQL, and a fake would mean a second store implementation that never exercises it. Rule: fake what we don't own (Zotero, embedding endpoint, external HTTP), run what we do.

## Consequences

- Docker must be running to run the test suite, on both machines.
- Each test runs in a rolled-back transaction for isolation and speed.
