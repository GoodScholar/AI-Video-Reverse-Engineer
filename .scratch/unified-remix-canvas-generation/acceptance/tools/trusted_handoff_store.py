#!/usr/bin/env python3
"""Fail-closed local entrypoint for the portable handoff store."""

from __future__ import annotations

import argparse
import sys

from trusted_evidence.handoffs import TrustedHandoffStoreV1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify-root")
    publish = commands.add_parser("publish-local")
    publish.add_argument("--producer-task", type=int, required=True)
    commands.add_parser("publish-export-request")
    commands.add_parser("verify-cache")
    for name in ("fetch", "import", "finalize", "read", "read-product-subject", "verify-policy-activation", "cleanup-owned-tree"):
        command = commands.add_parser(name)
        command.add_argument("handoff_id", nargs="?")
    args = parser.parse_args(argv)
    try:
        TrustedHandoffStoreV1.open_from_environment()
        if args.command == "publish-local" and args.producer_task in {22, 27}:
            raise ValueError("reserved producer")
        if args.command in {"read", "read-product-subject"}:
            raise ValueError("not finalized")
        # Remaining lifecycle actions deliberately require the C transport seal.
        if args.command not in {"verify-root", "publish-local"}:
            raise ValueError("transport lifecycle is not installed")
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
