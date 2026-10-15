"""
一次性检查：Django 配置与数据库连接（供 verify_config.bat 调用）。
需在 backend 目录下执行：python check_deployment.py
"""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus_security.settings")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402


def main():
    print("[check] Django manage.py check ...")
    call_command("check")
    print("[check] 数据库连接 ...")
    connection.ensure_connection()
    print("[OK] 数据库连接成功")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        sys.exit(1)
