"""Test environment defaults.

Set before any application import so ``Settings`` can be constructed without a
real ``.env``. The database URL points nowhere on purpose: unit tests must not
touch a database, and a test that tries will fail loudly instead of silently
hitting a development instance.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://nobody:nothing@localhost:1/void"
)
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-not-a-real-secret")
