import argparse
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
import getpass
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import subprocess
import sys

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class NIAAttendanceMonitor:
    def __init__(self, headless=True):
        self.base_url = "https://attendance.caraga.nia.gov.ph"
        self.auth_url = "https://accounts.nia.gov.ph/Account/Login"
        self.headless = headless
    
    def _find_chrome_in_termux(self):
        """Find Chrome executable in Termux"""
        possible_paths = [
            '/data/data/com.termux/files/usr/bin/chrome',
            '/data/data/com.termux/files/usr/bin/chromium',
            '/data/data/com.termux/files/usr/bin/chromium-browser',
            subprocess.getoutput('which chromium'),
            subprocess.getoutput('which chromium-browser'),
            subprocess.getoutput('which chrome')
        ]
        
        for path in possible_paths:
            if path and os.path.exists(path):
                logging.info(f"Found Chrome at: {path}")
                return path
        
        # If not found, try to install it
        logging.info("Chrome not found. Installing chromium...")
        result = subprocess.run(['pkg', 'install', 'chromium', '-y'], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            chrome_path = subprocess.getoutput('which chromium')
            if chrome_path:
                return chrome_path
        
        logging.error("Could not find or install Chrome in Termux")
        return None

    def _create_driver_termux(self):
        """Create Chrome driver optimized for Termux"""
        chrome_path = self._find_chrome_in_termux()
        if not chrome_path:
            raise Exception("Chrome not available in Termux")
        
        options = Options()
        
        if self.headless:
            options.add_argument("--headless=new")
        
        # Essential options for Termux/Android
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--window-size=1280,720")
        
        # Performance optimizations
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins")
        options.add_argument("--disable-translate")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-features=TranslateUI")
        options.add_argument("--disable-ipc-flooding-protection")
        
        # Set binary location
        options.binary_location = chrome_path
        
        try:
            # Use Chrome in portable mode
            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(45)
            driver.implicitly_wait(10)
            return driver
        except Exception as e:
            logging.error(f"Failed to create Chrome driver: {e}")
            # Fallback: try without specifying binary
            try:
                driver = webdriver.Chrome(options=options)
                driver.set_page_load_timeout(45)
                return driver
            except Exception as e2:
                logging.error(f"Fallback also failed: {e2}")
                raise

    def _login_with_selenium(self, driver, employee_id, password):
        """Login using Selenium"""
        try:
            logging.info("Opening login page...")
            driver.get(f"{self.auth_url}?ReturnUrl={self.base_url}/")
            
            wait = WebDriverWait(driver, 30)
            
            # Wait for and fill employee ID
            employee_input = wait.until(
                EC.presence_of_element_located((By.NAME, "EmployeeID"))
            )
            employee_input.clear()
            employee_input.send_keys(employee_id)
            
            # Wait for and fill password
            password_input = wait.until(
                EC.presence_of_element_located((By.NAME, "Password"))
            )
            password_input.clear()
            password_input.send_keys(password)
            
            # Try to find and click submit button
            try:
                submit_btn = wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"))
                )
                submit_btn.click()
            except:
                # Fallback: press Enter on password field
                from selenium.webdriver.common.keys import Keys
                password_input.send_keys(Keys.RETURN)
            
            # Wait for redirect to attendance portal
            wait.until(EC.url_contains(self.base_url))
            logging.info("✓ Login successful")
            
            # Additional wait for page to fully load
            time.sleep(3)
            return True
            
        except Exception as e:
            logging.error(f"Login failed: {e}")
            # Save screenshot for debugging
            try:
                driver.save_screenshot("login_error.png")
                logging.info("Saved screenshot as login_error.png")
            except:
                pass
            return False

    def _load_attendance_data(self, driver):
        """Load attendance page and extract data"""
        try:
            logging.info("Navigating to attendance page...")
            driver.get(f"{self.base_url}/Attendance")
            
            wait = WebDriverWait(driver, 30)
            
            # Wait for the data table to load
            wait.until(
                EC.presence_of_element_located((By.ID, "DataTables_Table_0"))
            )
            
            # Wait for data to populate (JavaScript rendering)
            time.sleep(5)
            
            # Check if table has rows
            rows = driver.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 tbody tr")
            if not rows:
                logging.warning("Table loaded but no rows found. Waiting longer...")
                time.sleep(5)
                rows = driver.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 tbody tr")
            
            logging.info(f"Found {len(rows)} attendance records")
            
            # Get page source and parse
            html_content = driver.page_source
            return self.parse_attendance_html(html_content)
            
        except Exception as e:
            logging.error(f"Error loading attendance data: {e}")
            # Save page source for debugging
            try:
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                logging.info("Saved page source as debug_page.html")
            except:
                pass
            return None

    def get_attendance_data(self, employee_id, password):
        """Main method to get attendance data"""
        driver = None
        try:
            driver = self._create_driver_termux()
            if self._login_with_selenium(driver, employee_id, password):
                attendance_data = self._load_attendance_data(driver)
                if attendance_data and attendance_data['records']:
                    self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])
                return attendance_data
            return None
        except Exception as e:
            logging.error(f"Error in get_attendance_data: {e}")
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

    def parse_attendance_html(self, html_content):
        """Parse the attendance table from HTML"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find the main table
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        if not table:
            logging.error("Could not find attendance table")
            return None

        # Extract headers
        headers = []
        header_row = table.find('thead').find('tr') if table.find('thead') else None
        if header_row:
            headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]

        # Extract data rows
        rows = []
        tbody = table.find('tbody')
        if tbody:
            for row in tbody.find_all('tr'):
                cells = row.find_all('td')
                if cells:
                    row_data = [cell.get_text(strip=True) for cell in cells]
                    rows.append(row_data)

        logging.info(f"Parsed {len(rows)} records with {len(headers)} columns")

        return {
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'table_headers': headers,
            'records': rows,
            'records_found': len(rows),
            'total_records_caption': str(len(rows)),
            'report_generated_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    def save_as_csv(self, headers, rows):
        """Save data to CSV"""
        try:
            if not rows:
                logging.warning("No data to save")
                return
            
            df = pd.DataFrame(rows, columns=headers)
            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            df.to_csv(filename, index=False)
            logging.info(f"✓ Saved {len(rows)} records to {filename}")
            
            # Show preview
            print("\nRecent records:")
            print(df.head().to_string(index=False))
            
        except Exception as e:
            logging.error(f"Error saving CSV: {e}")

    def analyze_attendance_patterns(self, attendance_data, employee_id):
        """Analyze attendance patterns"""
        if not attendance_data:
            return None
            
        try:
            headers = attendance_data['table_headers']
            records = attendance_data['records']
            
            if not records:
                logging.warning("No records to analyze")
                return None

            # Find relevant columns
            date_idx = next((i for i, h in enumerate(headers) if 'date' in h.lower()), 1)
            emp_id_idx = next((i for i, h in enumerate(headers) if 'employee' in h.lower() and 'id' in h.lower()), 4)
            
            # Filter user's records
            user_records = [r for r in records if len(r) > emp_id_idx and r[emp_id_idx] == employee_id]
            
            # Analyze today's records
            today = datetime.now().date()
            today_records = []
            
            for record in user_records:
                if len(record) > date_idx:
                    date_str = record[date_idx]
                    try:
                        record_date = datetime.strptime(date_str, '%m/%d/%Y %I:%M:%S %p').date()
                        if record_date == today:
                            today_records.append(record)
                    except ValueError:
                        try:
                            record_date = datetime.strptime(date_str, '%m/%d/%Y %I:%M %p').date()
                            if record_date == today:
                                today_records.append(record)
                        except ValueError:
                            continue

            logging.info(f"Today's records: {len(today_records)}")
            
            if today_records:
                for record in today_records:
                    logging.info(f"  - {record[date_idx]}")
                
                if len(today_records) < 2:
                    logging.warning("⚠️  Only one record today - check Time In/Out")
                elif len(today_records) % 2 != 0:
                    logging.warning("⚠️  Odd number of records - possible missing Time Out")

            return {
                'employee_id': employee_id,
                'total_records': len(user_records),
                'today_records': len(today_records),
                'analysis_timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logging.error(f"Analysis error: {e}")
            return None

    def monitor_attendance(self, employee_id, password, interval=300, max_checks=None):
        """Continuous monitoring"""
        logging.info(f"Starting monitor (interval: {interval}s)")
        checks = 0
        
        try:
            while True:
                if max_checks and checks >= max_checks:
                    break
                    
                data = self.get_attendance_data(employee_id, password)
                if data:
                    self.analyze_attendance_patterns(data, employee_id)
                
                checks += 1
                logging.info(f"Check {checks} completed. Waiting {interval} seconds...")
                time.sleep(interval)
                
        except KeyboardInterrupt:
            logging.info("Monitoring stopped")

def main():
    parser = argparse.ArgumentParser(description="NIA Attendance Monitor - Termux Version")
    parser.add_argument('--mode', choices=['once', 'monitor'], help='Run mode')
    parser.add_argument('--interval', type=int, default=300, help='Monitor interval')
    parser.add_argument('--visible', action='store_true', help='Show browser window')
    parser.add_argument('--verbose', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    monitor = NIAAttendanceMonitor(headless=not args.visible)
    
    print("NIA Attendance Monitor - Termux Edition")
    employee_id = input("Employee ID: ")
    password = getpass.getpass("Password: ")
    
    if args.mode == 'monitor':
        monitor.monitor_attendance(employee_id, password, args.interval)
    else:
        data = monitor.get_attendance_data(employee_id, password)
        if data:
            monitor.analyze_attendance_patterns(data, employee_id)
            print("✓ Check completed successfully!")
        else:
            print("❌ Failed to retrieve data")

if __name__ == "__main__":
    main()