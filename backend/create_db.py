"""
创建 MySQL 数据库 campus_security（若不存在）。
从 backend/.env 读取 DB_HOST、DB_USER、DB_PASSWORD、DB_PORT（与 Django 一致）。
"""
import os
from pathlib import Path

import MySQLdb

BASE_DIR = Path(__file__).resolve().parent


def load_env_file():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_env_file()

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "port": int(os.environ.get("DB_PORT", "3306") or 3306),
    "charset": "utf8mb4",
}

DB_NAME = os.environ.get("DB_NAME", "campus_security")


def create_database():
    try:
        print("正在连接到 MySQL 服务器...")
        conn = MySQLdb.connect(**DB_CONFIG)
        cursor = conn.cursor()

        print(f"正在创建数据库 '{DB_NAME}'...")
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )

        cursor.execute("SHOW DATABASES LIKE %s", (DB_NAME,))
        result = cursor.fetchone()

        if result:
            print(f"✓ 数据库 '{DB_NAME}' 已就绪。")
            cursor.execute(f"USE `{DB_NAME}`")
            cursor.execute("SELECT DATABASE()")
            current_db = cursor.fetchone()[0]
            print(f"✓ 当前数据库: {current_db}")
            return True
        print(f"✗ 数据库 '{DB_NAME}' 创建失败")
        return False

    except MySQLdb.Error as e:
        print(f"✗ MySQL 错误: {e}")
        return False
    finally:
        if "cursor" in locals():
            cursor.close()
        if "conn" in locals():
            conn.close()


if __name__ == "__main__":
    print("=" * 50)
    print("平安校园 - 数据库创建脚本")
    print("=" * 50)
    print()
    if not (BASE_DIR / ".env").exists():
        print("提示: 未找到 backend/.env，请先复制 .env.example 为 .env 并填写 DB_PASSWORD 等项。")
        print()

    if create_database():
        print()
        print("下一步: python manage.py migrate")
        print()
    else:
        print()
        print("请检查: MySQL 是否运行、.env 中 DB_USER/DB_PASSWORD/DB_HOST 是否正确。")
        print()
