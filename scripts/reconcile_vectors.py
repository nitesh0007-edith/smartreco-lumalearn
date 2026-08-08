#!/usr/bin/env python3
"""Manually drain the durable catalog/vector synchronization outbox."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db  # noqa: E402
from app.services.catalog import VectorSyncService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile SQL catalog records with Chroma")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    init_db()
    print(VectorSyncService().process_pending(limit=max(1, min(args.limit, 1000))))


if __name__ == "__main__":
    main()
