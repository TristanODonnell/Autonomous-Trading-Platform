"""Mint a JWT for manual API testing, or a long-lived token for the worker box.

    python scripts/generate_dev_jwt.py                       # admin, for local testing
    python scripts/generate_dev_jwt.py --role worker --sub box-b   # BOX_A_API_TOKEN on Box B

Run on the machine that holds Box A's JWT_SECRET (Box A itself, or a dev box with the same
.env). The worker role may only post results; it cannot read or change anything else.
"""

from __future__ import annotations

import argparse
import os

import jwt
from dotenv import load_dotenv

ROLES = ("admin", "operator", "risk_manager", "researcher", "worker")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", choices=ROLES, default="admin")
    parser.add_argument("--sub", default=None, help="Subject claim (default: local-dev-<role>)")
    args = parser.parse_args()

    load_dotenv()
    secret = os.environ["JWT_SECRET"]

    claims: dict[str, object] = {"sub": args.sub or f"local-dev-{args.role}", "role": args.role}
    if args.role == "admin":
        claims["roles"] = ["admin", "operator", "risk_manager"]

    print(jwt.encode(claims, secret, algorithm="HS256"))


if __name__ == "__main__":
    main()
