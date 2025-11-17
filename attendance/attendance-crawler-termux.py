#!/usr/bin/env python3
"""
Termux-friendly NIA Attendance Crawler

This script tries to use plain HTTP (requests + BeautifulSoup) to login and fetch
attendance HTML so it can run on Termux or a lightweight Ubuntu inside Termux.

If the website requires JavaScript-only login or rendering, use the `--selenium`
flag to attempt a Selenium fallback (requires Chromium/Chrome + ChromeDriver
available inside your environment).

Usage examples:
  python3 attendance-crawler-termux.py --mode once
  python3 attendance-crawler-termux.py --mode monitor --interval 300

See `requirements-termux.txt` and `README-termux.md` for Termux install notes.
"""

import argparse
import getpass
import json
import logging
import os
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class TermuxNIA:
    def __init__(self, base_url=None, auth_url=None, user_agent=None):
        self.base_url = base_url or "https://attendance.caraga.nia.gov.ph"
        self.auth_url = auth_url or "https://accounts.nia.gov.ph/Account/Login"
        self.user_agent = user_agent or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
        )

    def _get_headers(self):
        return {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }

    def login_with_requests(self, session: requests.Session, employee_id: str, password: str) -> bool:
        """Attempt to login using requests. Returns True if login looks successful."""
        logging.debug("Fetching login page to get hidden fields...")
        resp = session.get(self.auth_url, headers=self._get_headers(), timeout=30)
        if resp.status_code != 200:
            logging.error("Failed to fetch login page (status %s)", resp.status_code)
            return False

        soup = BeautifulSoup(resp.text, 'html.parser')
        form = soup.find('form')
        payload = {}
        if form:
            # Collect hidden inputs (anti-forgery tokens etc.)
            for hidden in form.find_all('input', {'type': ['hidden', 'submit', 'text', 'password']}):
                name = hidden.get('name')
                if not name:
                    continue
                value = hidden.get('value', '')
                payload[name] = value

        # Overwrite or set the credentials fields expected by the portal
        # Known names in the original script: EmployeeID, Password
        payload.update({
            'EmployeeID': employee_id,
            'Password': password
        })

        # If form has a ReturnUrl field, keep it; otherwise include base return
        if 'ReturnUrl' not in payload:
            payload['ReturnUrl'] = f"{self.base_url}/"

        logging.debug("Submitting login form via POST (requests)...")
        post_url = form.get('action') if form and form.get('action') else self.auth_url
        if post_url.startswith('/'):
            # relative
            post_url = requests.compat.urljoin(self.auth_url, post_url)

        r2 = session.post(post_url, data=payload, headers=self._get_headers(), timeout=30, allow_redirects=True)
        logging.debug("Login POST status: %s", r2.status_code)

        # After POST, check if we can reach attendance page and see the attendance table
        att = session.get(f"{self.base_url}/Attendance", headers=self._get_headers(), timeout=30)
        if att.status_code != 200:
            logging.warning("Unable to GET attendance page after login (status %s)", att.status_code)
            return False

        if 'DataTables_Table_0' in att.text:
            logging.info("✓ Login successful (requests)")
            return True

        # If DataTables not present, maybe JS is required. Return False so caller can fallback.
        logging.warning("Attendance table not found in requests response — site may require JavaScript")
        return False

    def fetch_attendance_html_requests(self, session: requests.Session) -> str | None:
        r = session.get(f"{self.base_url}/Attendance", headers=self._get_headers(), timeout=30)
        if r.status_code == 200:
            return r.text
        logging.error("Failed to fetch attendance page (status %s)", r.status_code)
        return None

    def parse_attendance_html(self, html_content):
        soup = BeautifulSoup(html_content, 'html.parser')
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        if not table:
            logging.error("No attendance table found on page")
            return None

        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all('th')]

        rows = []
        table_body = table.find('tbody')
        if table_body:
            for row in table_body.find_all('tr'):
                cells = row.find_all('td')
                if not cells:
                    continue
                row_data = []
                for cell in cells:
                    if 'sorting_1' in cell.get('class', []):
                        date_parts = [
                            span.get_text(strip=True)
                            for span in cell.find_all('span')
                            if span.get_text(strip=True)
                        ]
                        row_data.append(' '.join(date_parts) if date_parts else cell.get_text(strip=True))
                    else:
                        row_data.append(cell.get_text(strip=True))
                rows.append(row_data)

        generated_time = "Unknown"
        tfoot = table.find('tfoot')
        if tfoot:
            tfoot_cells = tfoot.find_all('th')
            if len(tfoot_cells) >= 2:
                generated_time = tfoot_cells[1].get_text(strip=True)

        total_records = "Unknown"
        caption = table.find('caption')
        if caption:
            caption_text = caption.get_text(strip=True)
            match = re.search(r'\((\d+)\)', caption_text)
            if match:
                total_records = match.group(1)

        return {
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'table_headers': headers,
            'records': rows,
            'records_found': len(rows),
            'total_records_caption': total_records,
            'report_generated_time': generated_time
        }

    def find_and_fetch_ajax_data(self, session: requests.Session, html_content: str | None = None):
        """Try to discover AJAX/XHR endpoints in the attendance page and fetch JSON data.

        This searches for common DataTables/ajax patterns in script tags and attempts
        to GET or POST to candidate endpoints. Returns parsed attendance-like dict
        or None.
        """
        logging.debug("Attempting to discover AJAX endpoints from page HTML...")
        if html_content is None:
            try:
                html_content = session.get(f"{self.base_url}/Attendance", headers=self._get_headers(), timeout=30).text
            except Exception as e:
                logging.debug("Error fetching attendance page for AJAX discovery: %s", e)
                return None

        soup = BeautifulSoup(html_content, 'html.parser')
        candidates = set()

        # Look for explicit DataTables ajax: 'ajax: "..."' or 'ajax: { url: "..." }'
        for script in soup.find_all('script'):
            if not script.string:
                continue
            txt = script.string
            # simple regexes to extract URL-like fragments
            for m in re.findall(r"ajax\s*:\s*['\"]([^'\"]+)['\"]", txt, flags=re.IGNORECASE):
                candidates.add(m)
            for m in re.findall(r"ajax\s*:\s*\{[^}]*url\s*:\s*['\"]([^'\"]+)['\"]", txt, flags=re.IGNORECASE):
                candidates.add(m)
            # jQuery $.ajax or $.get/post patterns
            for m in re.findall(r"\$\.ajax\s*\([^\)]*url\s*:\s*['\"]([^'\"]+)['\"]", txt, flags=re.IGNORECASE):
                candidates.add(m)
            for m in re.findall(r"\$\.get(?:JSON)?\s*\(\s*['\"]([^'\"]+)['\"]", txt, flags=re.IGNORECASE):
                candidates.add(m)
            for m in re.findall(r"\$\.post\s*\(\s*['\"]([^'\"]+)['\"]", txt, flags=re.IGNORECASE):
                candidates.add(m)
            # fetch('...') or fetch("...")
            for m in re.findall(r"fetch\(\s*['\"]([^'\"]+)['\"]", txt, flags=re.IGNORECASE):
                candidates.add(m)
            # generic URL patterns that often appear in JS (heuristic)
            for m in re.findall(r"['\"](\/?[A-Za-z0-9_\-\./]+(?:Get|get|List|list|Data|data|attendance)[^'\"]*)['\"]", txt):
                candidates.add(m)

        # Also look for links on the page referencing 'Attendance' or 'api'
        for a in soup.find_all(['a', 'form']):
            href = a.get('href') or a.get('action')
            if not href:
                continue
            if 'Attendance' in href or 'attendance' in href or 'api' in href or any(k in href.lower() for k in ('get', 'list', 'data')):
                candidates.add(href)

        # Log discovered candidate count
        if candidates:
            logging.debug("AJAX discovery found %s candidate endpoints", len(candidates))
            for c in candidates:
                logging.debug("  - candidate: %s", c)
        else:
            logging.debug("AJAX discovery found no candidate endpoints in page HTML")

        # Normalize and try each candidate
        for cand in list(candidates):
            try:
                url = cand
                if url.startswith('/'):
                    url = requests.compat.urljoin(self.base_url, url)
                elif not url.startswith('http'):
                    url = requests.compat.urljoin(self.base_url, url)

                logging.debug("Trying candidate AJAX URL: %s", url)

                # Try GET first
                resp = session.get(url, headers=self._get_headers(), timeout=20)
                if resp.status_code != 200:
                    # try POST without payload
                    resp = session.post(url, data={}, headers=self._get_headers(), timeout=20)

                if resp.status_code != 200:
                    logging.debug("Candidate returned status %s", resp.status_code)
                    continue

                # If JSON-looking response, attempt to parse
                ctype = resp.headers.get('Content-Type', '')
                body = resp.text.strip()
                if 'application/json' in ctype or body.startswith('{') or body.startswith('['):
                    try:
                        payload = resp.json()
                    except Exception:
                        logging.debug("Response looked like JSON but failed to parse for %s", url)
                        continue

                    # Normalize common shapes: { data: [...] } or [...]
                    rows = []
                    headers = []
                    if isinstance(payload, dict):
                        # Try common keys
                        if 'data' in payload and isinstance(payload['data'], list):
                            raw = payload['data']
                        elif 'rows' in payload and isinstance(payload['rows'], list):
                            raw = payload['rows']
                        else:
                            # try to find first list in values
                            raw = None
                            for v in payload.values():
                                if isinstance(v, list):
                                    raw = v
                                    break
                            if raw is None:
                                raw = []
                    elif isinstance(payload, list):
                        raw = payload
                    else:
                        raw = []

                    # If raw is list of dicts, extract headers from keys
                    if raw and isinstance(raw[0], dict):
                        headers = list(raw[0].keys())
                        for item in raw:
                            rows.append([str(item.get(h, '')) for h in headers])
                    elif raw and isinstance(raw[0], (list, tuple)):
                        rows = [list(map(str, r)) for r in raw]
                    else:
                        # If payload itself is simple list of strings, convert
                        if isinstance(payload, list) and all(isinstance(x, str) for x in payload):
                            rows = [[x] for x in payload]

                    if rows:
                        logging.info("✓ Discovered AJAX data endpoint and retrieved %s rows", len(rows))
                        return {
                            'timestamp': datetime.now().isoformat(),
                            'date': datetime.now().strftime('%Y-%m-%d'),
                            'table_headers': headers or [],
                            'records': rows,
                            'records_found': len(rows),
                            'total_records_caption': str(len(rows)),
                            'report_generated_time': 'Unknown'
                        }

            except Exception as e:
                logging.debug("Error trying candidate endpoint %s: %s", cand, e)

        logging.debug("No AJAX endpoints yielded attendance data")
        return None

    def save_as_csv(self, headers, rows, prefix='attendance'):
        try:
            if not rows:
                logging.warning("No data to save as CSV")
                return None

            df = pd.DataFrame(rows, columns=headers)
            filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            df.to_csv(filename, index=False, encoding='utf-8')
            logging.info("✓ Attendance data saved as %s", filename)
            print(df.head(10).to_string(index=False))
            return filename
        except Exception as e:
            logging.error("Error saving CSV: %s", e)
            return None

    def save_attendance_record(self, attendance_data, prefix='nia_attendance_backup'):
        try:
            filename = f"{prefix}_{datetime.now().strftime('%Y%m')}.json"
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = []
            data.append(attendance_data)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logging.info("✓ Attendance record saved to %s", filename)
            return filename
        except Exception as e:
            logging.error("Error saving attendance record: %s", e)
            return None

    def one_time_check(self, employee_id, password, use_selenium=False, driver_path=None):
        session = requests.Session()
        session.headers.update(self._get_headers())
        ok = self.login_with_requests(session, employee_id, password)
        if not ok:
            logging.warning("Requests-based login failed or site requires JS. Use --selenium to try Selenium fallback.")
            return None
        html = self.fetch_attendance_html_requests(session)
        if not html:
            logging.error("Failed to fetch attendance HTML after login")
            return None

        attendance = self.parse_attendance_html(html)
        if not attendance or not attendance.get('records'):
            # Try to find AJAX endpoint returning JSON data
            logging.debug("No table found in HTML; attempting AJAX/XHR discovery...")
            attendance = self.find_and_fetch_ajax_data(session, html)

        if attendance and attendance.get('records'):
            self.save_as_csv(attendance['table_headers'], attendance['records'], prefix='attendance_requests')
            analysis = self.simple_analysis(attendance, employee_id)
            if analysis:
                self.save_attendance_record(analysis)
                return analysis
            return attendance

        return attendance

    def simple_analysis(self, attendance_data, employee_id):
        # A small slice of the original analysis to keep this lightweight
        try:
            headers = attendance_data.get('table_headers', [])
            records = attendance_data.get('records', [])
            if not records:
                return None
            # Try to find indices
            date_idx = headers.index('Date Time') if 'Date Time' in headers else 1
            emp_idx = headers.index('Employee ID') if 'Employee ID' in headers else 4
            my_records = [r for r in records if len(r) > emp_idx and r[emp_idx] == employee_id]
            today = datetime.now().date()
            today_records = []
            for r in my_records:
                if len(r) > date_idx:
                    try:
                        dt = datetime.strptime(r[date_idx], '%m/%d/%Y %I:%M:%S %p').date()
                        if dt == today:
                            today_records.append(r)
                    except Exception:
                        pass
            return {
                'employee_id': employee_id,
                'total_records': len(my_records),
                'today_records': len(today_records),
                'today_details': today_records,
                'analysis_timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logging.error("Error during analysis: %s", e)
            return None

    def monitor_attendance(self, employee_id, password, interval_seconds=300, max_checks=None):
        session = requests.Session()
        session.headers.update(self._get_headers())
        ok = self.login_with_requests(session, employee_id, password)
        if not ok:
            logging.error("Requests-based login failed. Monitoring aborted.")
            return

        last_hash = None
        checks = 0
        while True:
            html = self.fetch_attendance_html_requests(session)
            if not html:
                logging.warning("No html retrieved this cycle.")
            else:
                attendance = self.parse_attendance_html(html)
                if not attendance or not attendance.get('records'):
                    logging.debug("No static table found; trying AJAX/XHR discovery for monitor...")
                    attendance = self.find_and_fetch_ajax_data(session, html)
                if attendance:
                    cur_hash = self._hash_records(attendance['records'])
                    if last_hash is None:
                        last_hash = cur_hash
                        logging.info("Initial snapshot captured (%s records)", attendance['records_found'])
                    elif cur_hash != last_hash:
                        logging.info("Detected change in attendance records!")
                        last_hash = cur_hash
                        if attendance['records']:
                            self.save_as_csv(attendance['table_headers'], attendance['records'], prefix='attendance_monitor')
                        analysis = self.simple_analysis(attendance, employee_id)
                        if analysis:
                            self.save_attendance_record(analysis)
                    else:
                        logging.debug("No changes detected since last check.")

            checks += 1
            if max_checks and checks >= max_checks:
                logging.info("Reached max checks. Stopping monitor.")
                break
            time.sleep(interval_seconds)

    def _hash_records(self, records):
        import hashlib
        hasher = hashlib.sha256()
        for row in records:
            line = "||".join(row)
            hasher.update(line.encode('utf-8', errors='replace'))
        return hasher.hexdigest()


def main():
    parser = argparse.ArgumentParser(description='Termux-friendly NIA Attendance Crawler')
    parser.add_argument('--mode', choices=['once', 'monitor'], default='once')
    parser.add_argument('--interval', type=int, default=300)
    parser.add_argument('--max-checks', type=int)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    employee_id = input('Enter your Employee ID: ')
    password = getpass.getpass('Enter your Password: ')

    crawler = TermuxNIA()

    if args.mode == 'once':
        result = crawler.one_time_check(employee_id, password)
        if result:
            print('\n=== CHECK COMPLETED ===')
            if 'today_records' in result:
                print(f"Today's records: {result['today_records']}")
        else:
            print('One-time check failed. Consider running with --verbose or try Selenium fallback.')

    elif args.mode == 'monitor':
        crawler.monitor_attendance(employee_id, password, interval_seconds=args.interval, max_checks=args.max_checks)


if __name__ == '__main__':
    main()
