from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.db import dispose_engine, init_engine, sessionmaker
from app.models import AdminUser
from app.services.auth import ROLES, hash_password

MIN_PASSWORD = 12


def _read_password() -> str:
    env = os.environ.get("ADMIN_PASSWORD")
    if env:
        return env
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    while True:
        p1 = getpass.getpass("Password: ")
        p2 = getpass.getpass("Password (again): ")
        if p1 != p2:
            print("パスワードが一致しません", file=sys.stderr)
            continue
        return p1


async def create_admin(username: str, role: str) -> int:
    if role not in ROLES:
        print(f"role は {ROLES} のいずれか", file=sys.stderr)
        return 2
    password = _read_password()
    if len(password) < MIN_PASSWORD:
        print(f"パスワードは {MIN_PASSWORD} 文字以上にしてください", file=sys.stderr)
        return 2
    async with sessionmaker()() as session:
        exists = (
            await session.execute(select(AdminUser).where(AdminUser.username == username))
        ).scalar_one_or_none()
        if exists:
            print(f"ユーザー {username} は既に存在します", file=sys.stderr)
            return 1
        session.add(AdminUser(username=username, password_hash=hash_password(password), role=role))
        await session.commit()
    print(f"created {role}: {username}")
    return 0


async def set_password(username: str) -> int:
    password = _read_password()
    if len(password) < MIN_PASSWORD:
        print(f"パスワードは {MIN_PASSWORD} 文字以上にしてください", file=sys.stderr)
        return 2
    async with sessionmaker()() as session:
        user = (
            await session.execute(select(AdminUser).where(AdminUser.username == username))
        ).scalar_one_or_none()
        if user is None:
            print("not found", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        await session.commit()
    print("password updated")
    return 0


async def reset_totp(username: str) -> int:
    async with sessionmaker()() as session:
        user = (
            await session.execute(select(AdminUser).where(AdminUser.username == username))
        ).scalar_one_or_none()
        if user is None:
            print("not found", file=sys.stderr)
            return 1
        user.totp_enabled = False
        user.totp_secret = None
        await session.commit()
    print("totp reset")
    return 0


async def list_admins() -> int:
    async with sessionmaker()() as session:
        for u in (await session.execute(select(AdminUser).order_by(AdminUser.id))).scalars():
            print(
                f"{u.id}\t{u.username}\t{u.role}\ttotp={'on' if u.totp_enabled else 'off'}\tactive={u.is_active}"
            )
    return 0


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser(
        "create-admin", help="管理者/モデレーターを作成（パスワードは対話入力または ADMIN_PASSWORD）"
    )
    c.add_argument("--username", required=True)
    c.add_argument("--role", default="admin", choices=ROLES)
    p = sub.add_parser("set-password")
    p.add_argument("--username", required=True)
    r = sub.add_parser("reset-totp")
    r.add_argument("--username", required=True)
    sub.add_parser("list-admins")
    args = parser.parse_args(argv)

    init_engine(null_pool=True)
    try:
        if args.cmd == "create-admin":
            return await create_admin(args.username, args.role)
        if args.cmd == "set-password":
            return await set_password(args.username)
        if args.cmd == "reset-totp":
            return await reset_totp(args.username)
        return await list_admins()
    finally:
        await dispose_engine()


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
