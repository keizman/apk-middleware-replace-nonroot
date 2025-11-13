#!/bin/bash

echo "APK Middleware Replacement Server"
echo "=================================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 not found"
    exit 1
fi

# Check required tools
MISSING_TOOLS=()

if ! command -v apktool &> /dev/null; then
    MISSING_TOOLS+=("apktool")
fi

if ! command -v apksigner &> /dev/null; then
    MISSING_TOOLS+=("apksigner")
fi

if ! command -v zipalign &> /dev/null; then
    MISSING_TOOLS+=("zipalign")
fi

if ! command -v keytool &> /dev/null; then
    MISSING_TOOLS+=("keytool")
fi

if [ ${#MISSING_TOOLS[@]} -ne 0 ]; then
    echo "Error: Missing required tools: ${MISSING_TOOLS[*]}"
    echo ""
    echo "Install with:"
    echo "  apt install apktool apksigner zipalign default-jdk -y"
    exit 1
fi

# Check requirements
if [ ! -f "requirements.txt" ]; then
    echo "Error: requirements.txt not found"
    exit 1
fi

echo "Checking Python dependencies..."
pip3 install -q -r requirements.txt

# Create work directories
mkdir -p workdir/uploads workdir/processed workdir/temp

echo ""
echo "Starting server on http://0.0.0.0:8000"
echo "Press Ctrl+C to stop"
echo ""

python3 py_server_demo.py

