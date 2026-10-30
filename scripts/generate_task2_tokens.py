#!/usr/bin/env python3
"""Generate short-lived admin/viewer JWTs for local Task 2 development."""

from __future__ import annotations

import argparse
from datetime import timedelta

from ai_field.task2_gateway.auth import Role, create_development_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        choices=("admin", "viewer", "both"),
        default="both",
        help="Token role to generate (default: both).",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=60,
        help="Positive token lifetime in minutes (default: 60).",
    )
    arguments = parser.parse_args()
    if arguments.minutes <= 0:
        parser.error("--minutes must be positive")

    roles: tuple[Role, ...]
    if arguments.role == "both":
        roles = ("viewer", "admin")
    else:
        roles = (arguments.role,)

    for role in roles:
        token = create_development_token(
            role,
            lifetime=timedelta(minutes=arguments.minutes),
        )
        print(f"{role.upper()}_TOKEN={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
