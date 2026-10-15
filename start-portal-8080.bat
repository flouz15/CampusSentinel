@echo off
setlocal EnableExtensions
chcp 65001 >nul

REM 项目根：静态服务托管整个仓库，以便访问 /frontend/ 与 /config/
set "ROOT=%~dp0"
cd /d "%ROOT%"

set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [错误] 未找到虚拟环境解释器:
    echo   %VENV_PY%
    echo 请在项目根目录执行: python -m venv .venv
    echo 并安装依赖后重试。
    pause
    exit /b 1
)

if not exist "backend\manage.py" (
    echo [错误] 未找到 backend\manage.py，请将本文件放在项目根目录下。
    pause
    exit /b 1
)

echo [1/2] 启动 Django 后端 0.0.0.0:5000 （使用项目 .venv）...
start "Django runserver 5000" /D "%ROOT%backend" cmd /k ""%VENV_PY%" manage.py runserver 0.0.0.0:5000"
timeout /t 3 >nul

echo [2/2] 启动静态服务 8080（项目根目录，使用项目 .venv）...
start "HTTP server 8080" /D "%ROOT%" cmd /k ""%VENV_PY%" -m http.server 8080"
timeout /t 2 >nul

echo 正在打开浏览器...
start "" "http://127.0.0.1:8080/frontend/index.html"

echo.
echo 大屏: http://127.0.0.1:8080/frontend/index.html
echo 配置: http://127.0.0.1:8080/config/dashboard.json （只读，勿暴露公网）
echo 后端: http://127.0.0.1:5000/api/
echo 关闭本窗口不会停止上述两个服务窗口。
echo.
pause
