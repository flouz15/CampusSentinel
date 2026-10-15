"""Start the Django API used by the campus dashboard."""

import os
import sys


def main():
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(backend_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus_security.settings")

    if not os.environ.get("AMAP_WEB_SERVICE_KEY", "").strip():
        print("[警告] backend/.env 未配置 AMAP_WEB_SERVICE_KEY，地图相关接口会返回配置错误。")

    print("服务地址: http://127.0.0.1:5000")
    print("接口入口: http://127.0.0.1:5000/api/")
    print("按 Ctrl+C 停止服务")

    from django.core.management import execute_from_command_line

    execute_from_command_line(["manage.py", "runserver", "0.0.0.0:5000"])

if __name__ == '__main__':
    main()
