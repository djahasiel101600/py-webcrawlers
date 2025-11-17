#!/usr/bin/env python3
"""
Ubuntu-in-Termux (proot) friendly NIA Attendance Crawler

This script is a close port of `attendance-crawler.py` but tuned to run inside
an Ubuntu/proot environment on Termux (Android 12). It prefers a system
`chromedriver` (installed via apt) and Chromium/Chrome. It falls back to
`webdriver-manager` only if necessary.

Usage examples:
  python3 attendance-crawler-ubuntu-termux-fixed.py --mode once --driver-path /usr/bin/chromedriver
  python3 attendance-crawler-ubuntu-termux-fixed.py --mode monitor --interval 300

Notes:
- Inside Termux run: `pkg install proot-distro` then `proot-distro install ubuntu-20.04`.
  Login with `proot-distro login ubuntu-20.04` and install Chromium + chromedriver
  with apt: `apt update; apt install -y chromium-browser chromium-chromedriver python3-pip`
- Use `pip3 install -r requirements-ubuntu-termux.txt` inside the distro.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import time
import shutil
from datetime import datetime

import getpass
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# webdriver-manager is optional; we import lazily if needed

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class UbuntuTermuxNIA:
    def __init__(self, headless=True, driver_path=None):
        self.base_url = "https://attendance.caraga.nia.gov.ph"
        self.auth_url = "https://accounts.nia.gov.ph/Account/Login"
        self.headless = headless
        self.driver_path = driver_path

    def _find_chromedriver(self):
        # Use explicit driver_path, then common system paths, then shutil.which
        if self.driver_path and os.path.exists(self.driver_path):
            return self.driver_path
        candidates = [
            '/usr/bin/chromedriver',
            '/usr/local/bin/chromedriver',
            '/opt/chromedriver/chromedriver'
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        binpath = shutil.which('chromedriver')
        if binpath:
            return binpath
        return None

    def _create_driver(self):
        options = Options()
        if self.headless:
            # use new headless flag for modern Chromium
            options.add_argument('--headless=new')
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--log-level=3')
        options.add_argument('--disable-extensions')
        options.add_argument('--remote-debugging-port=9222')

        driver_bin = self._find_chromedriver()
        if driver_bin:
            service = Service(driver_bin)
            logging.debug('Using chromedriver at %s', driver_bin)
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(60)
            return driver

        # Last resort: try webdriver-manager (may download appropriate binary)
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            logging.info('chromedriver not found on PATH; using webdriver-manager to install one')
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(60)
            return driver
        except Exception as e:
            logging.error('chromedriver not found and webdriver-manager failed: %s', e)
            raise WebDriverException('No chromedriver available')

    def _login_with_selenium(self, driver, employee_id, password):
        logging.debug('Opening login page with Selenium...')
        driver.get(f"{self.auth_url}?ReturnUrl={self.base_url}/")
        wait = WebDriverWait(driver, 30)

        employee_input = wait.until(EC.presence_of_element_located((By.NAME, 'EmployeeID')))
        password_input = wait.until(EC.presence_of_element_located((By.NAME, 'Password')))

        employee_input.clear()
        employee_input.send_keys(employee_id)
        password_input.clear()
        password_input.send_keys(password)

        # Try to click the submit button, fall back to pressing Enter
        try:
            submit_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            submit_btn.click()
        except Exception:
            password_input.submit()

        wait.until(EC.url_contains(self.base_url))
        logging.info('✓ Login successful via Selenium')

    def _load_attendance_html(self, driver):
        logging.debug('Navigating to attendance page...')
        driver.get(f"{self.base_url}/Attendance")
        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.ID, 'DataTables_Table_0')))

        # Wait for rows to be populated (if table loads via JS)
        try:
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '#DataTables_Table_0 tbody tr')) > 0)
        except TimeoutException:
            logging.warning('Attendance table loaded but contains no rows (yet). Proceeding with current content.')

        html_content = driver.page_source
        logging.debug('Captured attendance page HTML (%s chars)', len(html_content))
        return html_content

    def get_attendance_data(self, employee_id, password, driver=None, reuse_driver=False):
        created_driver = driver is None
        if created_driver:
            try:
                driver = self._create_driver()
            except WebDriverException as e:
                logging.error('Unable to start Selenium driver: %s', e)
                return None, None
            try:
                self._login_with_selenium(driver, employee_id, password)
            except Exception as e:
                logging.error('Login failed during Selenium setup: %s', e)
                try:
                    driver.quit()
                except Exception:
                    pass
                return None, None

        try:
            html_content = self._load_attendance_html(driver)
            attendance_data = self.parse_attendance_html(html_content)

            if attendance_data and attendance_data['records'] and not reuse_driver:
                self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])

            return attendance_data, driver
        except TimeoutException as e:
            logging.error('Selenium timed out while loading the page: %s', e)
            return None, driver
        except Exception as e:
            logging.error('Error fetching attendance via Selenium: %s', e)
            return None, driver
        finally:
            if created_driver and not reuse_driver and driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def parse_attendance_html(self, html_content):
        soup = BeautifulSoup(html_content, 'html.parser')
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        if not table:
            logging.error('No attendance table found on page')
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

        generated_time = 'Unknown'
        tfoot = table.find('tfoot')
        if tfoot:
            tfoot_cells = tfoot.find_all('th')
            if len(tfoot_cells) >= 2:
                generated_time = tfoot_cells[1].get_text(strip=True)

        total_records = 'Unknown'
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

    def save_as_csv(self, headers, rows):
        try:
            if not rows:
                logging.warning('No data to save as CSV')
                return
            df = pd.DataFrame(rows, columns=headers)
            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            df.to_csv(filename, index=False, encoding='utf-8')
            logging.info('✓ Attendance data saved as %s', filename)
            print(df.head(10).to_string(index=False))
        except Exception as e:
            logging.error('Error saving CSV: %s', e)

    def analyze_attendance_patterns(self, attendance_data, employee_id):
        try:
            if not attendance_data or 'records' not in attendance_data:
                logging.warning('No attendance data to analyze')
                return None
            headers = attendance_data['table_headers']
            records = attendance_data['records']
            if not records:
                logging.warning(f'No records found')
                return None
            date_time_idx = headers.index('Date Time') if 'Date Time' in headers else 1
            emp_id_idx = headers.index('Employee ID') if 'Employee ID' in headers else 4
            my_records = [record for record in records if len(record) > emp_id_idx and record[emp_id_idx] == employee_id]
            logging.debug('ATTENDANCE ANALYSIS FOR EMPLOYEE %s', employee_id)
            logging.debug('Total records found: %s', len(my_records))
            if not my_records:
                logging.warning(f'No matching records found for Employee ID: {employee_id}')
                return None
            today = datetime.now().date()
            today_records = []
            for record in my_records:
                if len(record) > date_time_idx:
                    date_str = record[date_time_idx]
                    try:
                        record_date = datetime.strptime(date_str, '%m/%d/%Y %I:%M:%S %p').date()
                        if record_date == today:
                            today_records.append(record)
                    except ValueError as e:
                        logging.debug(f"Date parsing error for '{date_str}': {e}")
                        try:
                            record_date = datetime.strptime(date_str, '%m/%d/%Y %I:%M %p').date()
                            if record_date == today:
                                today_records.append(record)
                        except ValueError:
                            pass
            logging.info('Records for today (%s): %s', today, len(today_records))
            if today_records:
                logging.info("Today's attendance:")
                for record in today_records:
                    time_in_record = record[date_time_idx] if len(record) > date_time_idx else 'N/A'
                    temp = record[2] if len(record) > 2 else 'N/A'
                    logging.info(f"  - {time_in_record} (Temp: {temp}°C)")
                if len(today_records) < 2:
                    logging.warning('⚠️  WARNING: Only one record today. Make sure you have both Time In and Time Out.')
                elif len(today_records) % 2 != 0:
                    logging.warning('⚠️  WARNING: Odd number of records today. Possible missing Time Out.')
                else:
                    logging.info('✓ Good: Even number of records today (likely both Time In and Time Out)')
            else:
                logging.info('No records found for today')
            return {
                'employee_id': employee_id,
                'total_records': len(my_records),
                'today_records': len(today_records),
                'today_details': today_records,
                'analysis_timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logging.error('Error analyzing attendance: %s', e)
            return None
