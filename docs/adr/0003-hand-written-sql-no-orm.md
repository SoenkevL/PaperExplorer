# Hand-written SQL with psycopg 3, no ORM

Database access uses psycopg 3 with hand-written SQL mapped into pydantic models, and numbered plain `.sql` migrations. Learning SQL is an explicit goal, and the core feature (hybrid vector + full-text retrieval fused with RRF) is a hand-tuned query an ORM would obscure. Do not "fix" this by introducing SQLAlchemy.
