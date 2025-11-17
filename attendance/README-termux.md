Termux / Ubuntu-in-Termux notes for `attendance-crawler-termux.py`

Quick steps (Termux without proot/ubuntu):

- Install Python (Termux uses `pkg`):
  pkg update; pkg install python
- Install dependencies with pip:
  pip install -r requirements-termux.txt

Run:
python3 attendance-crawler-termux.py --mode once

Notes and caveats:

- This version tries to login using `requests` (no browser). If the site requires heavy JavaScript for login or table rendering, the requests-based approach may fail.
- If requests-based login fails you have two options:
  1. Run a full Ubuntu/proot-distro inside Termux and install Chromium + ChromeDriver, then run the original Selenium script.
  2. Run the script on a machine with a full Chrome + chromedriver and use the `attendance-crawler-android.py` (Selenium) script.

Running inside Ubuntu (proot-distro):

- Install a distro, e.g. `proot-distro install ubuntu-20.04` and `proot-distro login ubuntu-20.04`
- Inside the distro: install `chromium-driver`/`chromium` and `python3` and pip packages, then run the Selenium script.

If you'd like, I can:

- Add a Selenium fallback in `attendance-crawler-termux.py` that attempts to run headless Chromium when available.
- Prepare a short proot-distro install script for Termux.
