Running the Selenium crawler inside Ubuntu (proot-distro) on Termux (Android 12)

Overview

- This README describes how to install an Ubuntu userland inside Termux using
  `proot-distro`, install Chromium + chromedriver, and run the Selenium-based
  `attendance-crawler-ubuntu-termux.py` script.

Install Termux packages (on Android host):

```bash
pkg update; pkg upgrade -y
pkg install proot-distro
```

Install and login to Ubuntu (example 20.04):

```bash
proot-distro install ubuntu-20.04
proot-distro login ubuntu-20.04
```

Inside the Ubuntu session (this is a real shell inside the distro):

```bash
apt update; apt upgrade -y
apt install -y python3 python3-pip chromium-browser chromium-chromedriver curl wget unzip
# Optional: git if you want to clone the repo
apt install -y git

# Install Python deps
pip3 install -r requirements-ubuntu-termux.txt
```

Notes about Chromedriver

- The distro's `chromedriver` binary (installed via `apt`) is preferred.
- If Chromium's version and chromedriver version mismatch, you may need to
  download a matching chromedriver from https://chromedriver.chromium.org/
  and pass its path using `--driver-path /path/to/chromedriver`.

Running the script

- One-time check (interactive credentials):

```bash
python3 attendance-crawler-ubuntu-termux.py --mode once
```

- Run monitoring mode every 5 minutes, limit to 10 checks:

```bash
python3 attendance-crawler-ubuntu-termux.py --mode monitor --interval 300 --max-checks 10
```

- If you want to see the browser (non-headless), add `--show-browser` flag. In proot this may not display a UI; headless is recommended.

Troubleshooting

- If the script cannot find `chromedriver`, pass `--driver-path /usr/bin/chromedriver`.
- If Selenium fails to start, run the script with `--verbose` to see debug logs.

If you'd like, I can also:

- Add an automated proot-distro bootstrap script that installs Ubuntu and the packages.
- Add automatic chromedriver matching logic (download correct chromedriver for installed Chromium).
