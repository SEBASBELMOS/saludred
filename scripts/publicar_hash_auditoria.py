"""Print the head of the audit chain so it can be published OUTSIDE the database.

    python -m scripts.publicar_hash_auditoria                       # one line to stdout
    python -m scripts.publicar_hash_auditoria --salida cabeza.log   # also appended to a file

The chain detects an edited row, a deleted row in the middle and a broken link, but not
the deletion of the LAST rows: nothing after them points at them. Keeping a copy of the
head (``seq`` + ``hash``) somewhere an attacker with database access cannot reach (a
file in the repository, a message to the team, a gist) closes that gap: a later
verification compares the head it finds with the last one published. Run it daily.

It only reads. The same data is served to the admin by ``GET /api/v1/audit-logs/head``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.services import audit_chain


def main(argv: list[str] | None = None, session_factory=None) -> int:  # noqa: ANN001
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--salida", type=Path, help="archivo donde agregar la linea")
    args = parser.parse_args(argv)

    if session_factory is None:
        from app.core.database import SessionLocal

        session_factory = SessionLocal

    with session_factory() as db:
        last = audit_chain.head(db)
    if last is None:
        print("La cadena de auditoria todavia no tiene filas.")
        return 1
    line = (
        f"AUDIT_HEAD seq={last.seq} hash={last.row_hash} "
        f"fecha={last.created_at.isoformat()} publicado={datetime.now(timezone.utc).isoformat()}"
    )
    print(line)
    if args.salida:
        with args.salida.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
