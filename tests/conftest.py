"""Process-local, generated configuration for the isolated test suite."""

import os
import secrets

os.environ.setdefault(
    "FINSIGHT_DATABASE_URL",
    "postgresql+psycopg://finsight@localhost:5432/finsight",
)
os.environ.setdefault("FINSIGHT_EXPERIMENT_ASSIGNMENT_SECRET", secrets.token_hex(32))
