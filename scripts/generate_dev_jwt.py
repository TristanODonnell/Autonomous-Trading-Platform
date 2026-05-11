from __future__ import annotations

import os

import jwt
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    secret = os.environ["JWT_SECRET"]

    token = jwt.encode(
        {
            "sub": "local-dev-operator",
            "role": "admin",
            "roles": ["admin", "operator", "risk_manager"],
        },
        secret,
        algorithm="HS256",
    )

    print(token)


if __name__ == "__main__":
    main()
