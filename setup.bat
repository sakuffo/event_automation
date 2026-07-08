@echo off
REM Setup script for Wix Events + Google Sheets Sync (Windows)

echo 🚀 Setting up Wix Events + Google Sheets Sync
echo ========================================================

REM Check Python version
echo Checking Python version...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python is not installed. Please install Python 3.8+
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo ✅ Python %PYTHON_VERSION% found

REM Create virtual environment
echo.
echo Creating virtual environment...
if exist venv (
    echo Virtual environment already exists. Removing old one...
    rmdir /s /q venv
)

python -m venv venv
if %errorlevel% equ 0 (
    echo ✅ Virtual environment created
) else (
    echo ❌ Failed to create virtual environment
    exit /b 1
)

REM Activate virtual environment
echo.
echo Activating virtual environment...
call venv\Scripts\activate.bat
if %errorlevel% equ 0 (
    echo ✅ Virtual environment activated
) else (
    echo ❌ Failed to activate virtual environment
    exit /b 1
)

REM Upgrade pip
echo.
echo Upgrading pip...
python -m pip install --upgrade pip --quiet

REM Install dependencies
echo.
echo Installing dependencies...
pip install -r requirements.txt
if %errorlevel% equ 0 (
    echo ✅ Dependencies installed successfully
) else (
    echo ❌ Failed to install dependencies
    exit /b 1
)

REM Test imports
echo.
echo Testing imports...
python -c "import requests, google.auth, googleapiclient, dotenv" 2>nul
if %errorlevel% equ 0 (
    echo ✅ All imports working correctly
) else (
    echo ❌ Some imports failed. Please check installation.
    exit /b 1
)

REM Create .env file if it doesn't exist
echo.
if not exist .env (
    echo Creating .env from .env.example...
    copy .env.example .env >nul
    echo ✅ .env created
    echo.
    echo ⚠️  IMPORTANT: Edit .env and fill in your credentials
    echo    - WIX_API_KEY + WIX_SITE_ID ^(the DEV site^) + WIX_DEV_SITE_ID
    echo    - NOTION_ACCESS_TOKEN ^(+ NOTION_*_DB_ID after setup-notion^)
    echo    - GOOGLE_CREDENTIALS ^(Drive-hosted event images^)
) else (
    echo ✅ .env file already exists
)

echo.
echo ========================================================
echo ✅ Setup complete!
echo.
echo Next steps:
echo 1. Edit .env file with your credentials
echo 2. Activate the virtual environment: venv\Scripts\activate.bat
echo 3. Test credentials: python sync_events.py validate
echo 4. Run sync: python sync_events.py sync
echo.
echo For detailed setup instructions, see SETUP.md
pause