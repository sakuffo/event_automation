#!/bin/bash

# Setup script for Wix Events + Google Sheets Sync

echo "🚀 Setting up Wix Events + Google Sheets Sync"
echo "=".repeat 50

# Check Python version
echo "Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "✅ Python $PYTHON_VERSION found"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
if [ -d "venv" ]; then
    echo "Virtual environment already exists. Removing old one..."
    rm -rf venv
fi

python3 -m venv venv
if [ $? -eq 0 ]; then
    echo "✅ Virtual environment created"
else
    echo "❌ Failed to create virtual environment"
    exit 1
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate
if [ $? -eq 0 ]; then
    echo "✅ Virtual environment activated"
else
    echo "❌ Failed to activate virtual environment"
    exit 1
fi

# Upgrade pip
echo ""
echo "Upgrading pip..."
python -m pip install --upgrade pip --quiet

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -r requirements.txt
if [ $? -eq 0 ]; then
    echo "✅ Dependencies installed successfully"
else
    echo "❌ Failed to install dependencies"
    exit 1
fi

# Test imports
echo ""
echo "Testing imports..."
python -c "import requests, google.auth, googleapiclient, dotenv" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ All imports working correctly"
else
    echo "❌ Some imports failed. Please check installation."
    exit 1
fi

# Create .env file if it doesn't exist
echo ""
if [ ! -f ".env" ]; then
    echo "Creating .env template..."
    cat <<'EOF' > .env
# Wix Credentials
WIX_API_KEY=
WIX_ACCOUNT_ID=
WIX_SITE_ID=

# Google Sheets
GOOGLE_SHEET_ID=
GOOGLE_CREDENTIALS=
EOF
    echo "✅ .env template created"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env file and add your credentials"
    echo "   - WIX_API_KEY"
    echo "   - WIX_ACCOUNT_ID"
    echo "   - WIX_SITE_ID"
    echo "   - GOOGLE_SHEET_ID"
    echo "   - GOOGLE_CREDENTIALS"
else
    echo "✅ .env file already exists"
fi

echo ""
echo "=".repeat 50
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your credentials"
echo "2. Activate the virtual environment: source venv/bin/activate"
echo "3. Test credentials: python sync_events.py validate"
echo "4. Run sync: python sync_events.py sync"
echo ""
echo "For detailed setup instructions, see SETUP.md"