@echo off
chcp 65001 >nul
echo ========================================
echo 数据库初始化（依赖 .env 中的 DB_*）
echo ========================================
echo.

cd /d "%~dp0"
if not exist ".env" (
  echo [提示] 未找到 .env，请先复制 .env.example 为 .env 并填写数据库密码等。
  echo.
)

echo [1/3] 安装 Python 依赖...
pip install -r requirements.txt
if %errorlevel% neq 0 (
  echo 错误: 依赖安装失败
  pause
  exit /b 1
)
echo.

echo [2/3] 创建数据库（若不存在）...
python create_db.py
if %errorlevel% neq 0 (
  echo 错误: 创建数据库失败
  pause
  exit /b 1
)
echo.

echo [3/3] 运行数据库迁移...
python manage.py migrate
if %errorlevel% neq 0 (
  echo 错误: 数据库迁移失败
  pause
  exit /b 1
)
echo.
echo ========================================
echo 完成。下一步可运行 start.py 启动后端
echo ========================================
pause
