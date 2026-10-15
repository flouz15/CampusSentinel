@echo off
chcp 65001 >nul
echo ============================================================
echo 项目配置验证（使用 backend/.env 与 Django 设置）
echo ============================================================
echo.

echo [1/3] 检查 Python...
python --version
if %errorlevel% neq 0 (
  echo [错误] 未检测到 Python，请先安装并加入 PATH
  pause
  exit /b 1
)
echo.

echo [2/3] 检查 backend/.env ...
cd /d "%~dp0backend"
if not exist ".env" (
  echo [警告] 未找到 backend\.env，请复制 .env.example 为 .env 并配置数据库等。
  echo.
)

echo [3/3] Django 检查与数据库连接...
python check_deployment.py
if %errorlevel% neq 0 (
  echo [错误] 检查失败，请根据上方提示修复 .env 或 MySQL
  pause
  exit /b 1
)

echo.
echo ============================================================
echo 检查通过。可运行项目根目录的 start-portal-8080.bat
echo ============================================================
pause
