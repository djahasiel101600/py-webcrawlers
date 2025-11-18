import argparse
import hashlib
import json
import logging
import os
import re
import time
import csv
from datetime import datetime

import getpass
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class NIAAttendanceMonitor:
    def __init__(self, headless=True, driver_path=None):
        self.base_url = "https://attendance.caraga.nia.gov.ph"
        self.auth_url = "https://accounts.nia.gov.ph/Account/Login"
        self.headless = headless
        self.driver_path = driver_path
    
    def _create_driver(self):
        """Create Firefox driver optimized for Android/Termux"""
        options = Options()
        
        if self.headless:
            options.add_argument("--headless")
        
        # Android/Termux specific options
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--width=1920")
        options.add_argument("--height=1080")
        
        # Set Firefox binary path for Termux
        firefox_binary = "/data/data/com.termux/files/usr/bin/firefox"
        if os.path.exists(firefox_binary):
            options.binary_location = firefox_binary
        
        # GeckoDriver setup
        if self.driver_path:
            # Use specified driver path
            service = Service(executable_path=self.driver_path)
        else:
            # Try to find geckodriver in common locations
            possible_paths = [
                "/data/data/com.termux/files/usr/bin/geckodriver",
                "/data/data/com.termux/files/usr/local/bin/geckodriver",
                "./geckodriver"
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    service = Service(executable_path=path)
                    break
            else:
                # If no geckodriver found, let Selenium try to find it
                service = Service()
        
        driver = webdriver.Firefox(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.implicitly_wait(10)  # Add implicit wait
        return driver

    def _login_with_selenium(self, driver, employee_id, password):
        logging.debug("Opening login page with Selenium...")
        driver.get(f"{self.auth_url}?ReturnUrl={self.base_url}/")
        wait = WebDriverWait(driver, 30)

        employee_input = wait.until(EC.presence_of_element_located((By.NAME, "EmployeeID")))
        password_input = wait.until(EC.presence_of_element_located((By.NAME, "Password")))

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

        # Wait for redirect to attendance site with longer timeout
        wait.until(EC.url_contains(self.base_url))
        logging.info("✓ Login successful via Selenium")

    def _load_attendance_html(self, driver):
        """Load attendance page with better error handling and multiple fallbacks"""
        logging.debug("Navigating to attendance page...")
        
        try:
            driver.get(f"{self.base_url}/Attendance")
        except TimeoutException:
            logging.warning("Page load timed out, but continuing with current content...")
        
        wait = WebDriverWait(driver, 45)  # Increased timeout
        
        # Try multiple strategies to find the table or content
        try:
            # Strategy 1: Wait for the specific table
            logging.debug("Waiting for attendance table...")
            wait.until(EC.presence_of_element_located((By.ID, "DataTables_Table_0")))
        except TimeoutException:
            logging.warning("Table with ID 'DataTables_Table_0' not found, trying alternative selectors...")
            
            # Strategy 2: Look for any table that might contain attendance data
            try:
                tables = driver.find_elements(By.TAG_NAME, "table")
                logging.debug(f"Found {len(tables)} tables on page")
                if tables:
                    logging.debug("Using first available table")
                else:
                    logging.warning("No tables found on page")
            except Exception as e:
                logging.warning(f"Error finding tables: {e}")
        
        # Wait for any content to load
        try:
            # Wait for at least some content to be present
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(5)  # Extra wait for JavaScript
            
            # Check if we have any rows in the table
            try:
                rows = driver.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 tbody tr, table tbody tr")
                logging.debug(f"Found {len(rows)} rows in table")
                if not rows:
                    logging.warning("Table exists but contains no rows")
            except Exception as e:
                logging.debug(f"Could not count rows: {e}")
                
        except TimeoutException:
            logging.error("Page content failed to load")
            # Continue anyway to capture whatever HTML we have

        html_content = driver.page_source
        logging.debug("Captured attendance page HTML (%s chars)", len(html_content))
        
        # Debug: Save HTML for inspection
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logging.debug("Saved page HTML to debug_page.html for inspection")
        
        return html_content
    
    def parse_attendance_html(self, html_content):
        """Parse attendance table HTML with multiple fallback strategies"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Strategy 1: Look for the specific table ID
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        
        # Strategy 2: If not found, look for any table that might contain attendance data
        if not table:
            logging.warning("Table with ID 'DataTables_Table_0' not found, searching for any table...")
            tables = soup.find_all('table')
            if tables:
                table = tables[0]  # Use first table found
                logging.info(f"Using first available table (out of {len(tables)} tables found)")
            else:
                logging.error("No tables found on page at all!")
                # Try to extract any structured data
                return self._extract_fallback_data(soup)
        
        if not table:
            logging.error("No attendance table found on page")
            return None

        # Extract table headers
        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]
        
        # If no headers in thead, try to get from first row
        if not headers:
            first_row = table.find('tr')
            if first_row:
                headers = [cell.get_text(strip=True) for cell in first_row.find_all(['th', 'td'])]

        logging.debug("Found table headers: %s", headers)

        # Extract table rows
        rows = []
        table_body = table.find('tbody')
        if table_body:
            for row in table_body.find_all('tr'):
                cells = row.find_all(['td', 'th'])
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
        else:
            # If no tbody, get all rows except header
            all_rows = table.find_all('tr')
            if headers and all_rows:
                all_rows = all_rows[1:]  # Skip header row
            for row in all_rows:
                cells = row.find_all(['td', 'th'])
                if cells:
                    row_data = [cell.get_text(strip=True) for cell in cells]
                    rows.append(row_data)

        logging.debug("Attendance rows parsed: %s", len(rows))

        # Extract metadata
        generated_time = "Unknown"
        total_records = "Unknown"
        
        tfoot = table.find('tfoot')
        if tfoot:
            tfoot_cells = tfoot.find_all('th')
            if len(tfoot_cells) >= 2:
                generated_time = tfoot_cells[1].get_text(strip=True)

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

    def _extract_fallback_data(self, soup):
        """Extract data when no table is found"""
        logging.warning("Attempting to extract data without table structure...")
        
        # Look for any structured data that might be attendance records
        records = []
        
        # Try to find divs or other elements that might contain records
        potential_containers = soup.find_all(['div', 'section', 'article'], class_=re.compile(r'record|attendance|data', re.I))
        
        for container in potential_containers:
            text = container.get_text(strip=True)
            if any(keyword in text.lower() for keyword in ['time', 'date', 'attendance', 'record']):
                records.append([text])
        
        return {
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'table_headers': ['Fallback Data'],
            'records': records,
            'records_found': len(records),
            'total_records_caption': 'Unknown',
            'report_generated_time': 'Unknown',
            'note': 'Data extracted using fallback method - table not found'
        }

    def get_attendance_data(self, employee_id, password, driver=None, reuse_driver=False):
        """Use Selenium to log in and extract attendance data"""
        created_driver = driver is None
        if created_driver:
            try:
                driver = self._create_driver()
            except WebDriverException as e:
                logging.error(f"Unable to start Firefox driver: {e}")
                logging.info("Make sure Firefox and geckodriver are installed in Termux")
                return None, None
            try:
                self._login_with_selenium(driver, employee_id, password)
            except Exception as e:
                logging.error(f"Login failed during Selenium setup: {e}")
                if driver:
                    driver.quit()
                return None, None

        try:
            html_content = self._load_attendance_html(driver)
            attendance_data = self.parse_attendance_html(html_content)

            if attendance_data and attendance_data['records'] and not reuse_driver:
                self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])

            return attendance_data, driver
        except TimeoutException as e:
            logging.error(f"Selenium timed out while loading the page: {e}")
            # Try to get whatever HTML we have
            try:
                html_content = driver.page_source
                attendance_data = self.parse_attendance_html(html_content)
                return attendance_data, driver
            except Exception:
                return None, driver
        except Exception as e:
            logging.error(f"Error fetching attendance via Selenium: {e}")
            return None, driver
        finally:
            if created_driver and not reuse_driver and driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    # ... keep the rest of your methods the same (save_as_csv, analyze_attendance_patterns, etc.)

    def save_as_csv(self, headers, rows):
        """Save attendance data as CSV file using built-in csv module"""
        try:
            if not rows:
                logging.warning("No data to save as CSV")
                return

            # Generate filename with current date
            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

            # Write CSV using built-in csv module
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                # Write headers
                writer.writerow(headers)
                # Write all rows
                writer.writerows(rows)

            logging.info("✓ Attendance data saved as %s", filename)

            # Preview first 10 rows in logs
            logging.debug("Recent attendance records preview:")
            for row in rows[:10]:
                logging.debug(row)

            logging.debug("Total records: %s", len(rows))

        except Exception as e:
            logging.error(f"Error saving CSV: {e}")

    # ... keep all your other existing methods (analyze_attendance_patterns, save_attendance_record, monitor_attendance, one_time_check)

def main():
    parser = argparse.ArgumentParser(description="NIA Attendance Monitor for Android/Termux")
    parser.add_argument(
        '--mode',
        choices=['once', 'monitor'],
        help='Run once or continuously monitor'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=300,
        help='Monitoring interval in seconds (default: 300)'
    )
    parser.add_argument(
        '--max-checks',
        type=int,
        help='Optional limit on number of monitoring cycles'
    )
    parser.add_argument(
        '--show-browser',
        action='store_true',
        help='Show the browser window (not recommended for Termux)'
    )
    parser.add_argument(
        '--driver-path',
        help='Path to geckodriver executable (optional)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose (DEBUG) logging output'
    )
    parser.add_argument(
        '--debug-html',
        action='store_true',
        help='Save HTML content for debugging'
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    monitor = NIAAttendanceMonitor(headless=not args.show_browser, driver_path=args.driver_path)
    
    # Get credentials securely
    employee_id = input("Enter your Employee ID: ")
    password = getpass.getpass("Enter your Password: ")
    
    if args.mode:
        choice = '1' if args.mode == 'once' else '2'
    else:
        print("\nChoose operation:")
        print("1. One-time attendance check with analysis")
        print("2. Start continuous monitoring")
        choice = input("Enter choice (1 or 2): ").strip()
    
    if choice == "1":
        result = monitor.one_time_check(employee_id, password)
        if result:
            print("\n" + "="*50)
            print("CHECK COMPLETED SUCCESSFULLY!")
            if 'today_records' in result:
                print(f"Today's records: {result['today_records']}")
            if 'note' in result:
                print(f"Note: {result['note']}")
        else:
            print("One-time check failed!")
    
    elif choice == "2":
        monitor.monitor_attendance(
            employee_id,
            password,
            interval_seconds=args.interval,
            max_checks=args.max_checks
        )
    
    else:
        print("Invalid choice")

if __name__ == "__main__":
    main()