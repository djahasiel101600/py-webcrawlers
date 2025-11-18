# Install Firefox in Termux
pkg update && pkg upgrade
pkg install python firefox
pip install selenium beautifulsoup4

# Download geckodriver for Android
# You'll need to download the appropriate ARM64 version
wget https://github.com/mozilla/geckodriver/releases/download/v0.36.0/geckodriver-v0.36.0-linux-aarch64.tar.gz
tar -xzf geckodriver-v0.34.0-linux-aarch64.tar.gz
chmod +x geckodriver
mv geckodriver /data/data/com.termux/files/usr/bin/

# Run your script
python your_script.py --driver-path /data/data/com.termux/files/usr/bin/geckodriver