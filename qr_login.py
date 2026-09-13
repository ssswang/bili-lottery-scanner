# -*- coding: utf-8 -*-
"""扫码登录并保存账号会话；支持多账号，每个账号一个名字。"""

import argparse

from auth.user_auth import qr_login_main


def main():
    parser = argparse.ArgumentParser(description="扫码登录 B 站并保存账号会话")
    parser.add_argument("--name", default="acct1", help="账号名，如 acct1、acct2 ……")
    args = parser.parse_args()
    qr_login_main(name=args.name)


if __name__ == "__main__":
    main()
