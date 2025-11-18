#!/data/data/com.termux/files/usr/bin/bash

echo "Setting up web crawler for Termux..."

# Update packages
pkg update && pkg upgrade -y

# Install required packages
pkg install -y python chromium

# Install Python packages
pip install selenium beautifulsoup4 requests requests_html

# Create crawler directory
mkdir -p ~/web-crawler
cd ~/web-crawler

echo "Setup complete!"
echo "Run: python android_crawler.py"