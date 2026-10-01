"""CLI: python -m app.seed [--reset] | python -m app.seed purge"""

from __future__ import annotations

import sys

from app.core.db import SessionLocal
from app.core.logging import configure_logging


def main(argv: list[str]) -> int:
    configure_logging()
    from app.seed.demo import DEMO_PASSWORD, seed
    from app.seed.purge import purge_demo_data

    with SessionLocal() as db:
        if argv[:1] == ["purge"] or "--reset" in argv:
            print("Purged demo data:", purge_demo_data(db))
            if argv[:1] == ["purge"]:
                return 0
        counts = seed(db)
    if counts:
        print("Seeded demo data:", counts)
        print(f"Demo accounts (password: {DEMO_PASSWORD}): admin@example.com, moderator@example.com, organizer@example.com, "
              "uniadmin@example.com, judge@example.com, sponsor@example.com, maintainer@example.com, student@example.com")
    else:
        print("Demo data already present (use --reset to recreate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
