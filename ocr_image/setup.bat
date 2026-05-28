@echo off
chcp 65001 >nul
echo ============================================
echo   牛牛OCR — 一键环境配置
echo ============================================
echo.

cd /d "%~dp0"

:: 1. 创建虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 正在创建虚拟环境...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [错误] 创建虚拟环境失败，请确认 Python 已安装并加入 PATH
        pause
        exit /b 1
    )
) else (
    echo [1/3] 虚拟环境已存在，跳过
)

:: 2. 安装依赖
echo [2/3] 正在安装依赖...
call .venv\Scripts\activate.bat
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

:: 3. 检查关键配置
echo [3/3] 检查配置...

echo.
echo ============================================
echo   配置完成！使用方式：
echo ============================================
echo.
echo   处理单张图片：
echo     .venv\Scripts\python.exe run.py process -f "图片路径"
echo.
echo   批量处理 input 目录：
echo     .venv\Scripts\python.exe run.py batch
echo.
echo   查看数据库：
echo     .venv\Scripts\python.exe check_db.py
echo.
echo   查询历史记录：
echo     .venv\Scripts\python.exe run.py query
echo.
echo   注意：请确认 config.yaml 中 tesseract_cmd 路径正确
echo         如需使用云端 OCR，确保 .env 中 API Key 有效
echo ============================================
pause
