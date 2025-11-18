import threading
import signal
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.align import Align
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
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.logging import RichHandler
from contextlib import contextmanager
from functools import wraps
from typing import List, Optional, Dict, Any, Callable
import yaml
from dataclasses import dataclass
import pickle

console = Console()

# Set up logging with Rich handler for nicer console output
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)]
)

class Config:
    def __init__(self):
        self.defaults = {
            'base_url': "https://attendance.caraga.nia.gov.ph",
            'auth_url': "https://accounts.nia.gov.ph/Account/Login",
            'timeout': 60,
            'monitor_interval': 300,
            'max_retries': 3,
            'headless': True,
            'browser_width': 1920,
            'browser_height': 1080
        }
        self.config_path = os.path.expanduser('~/.nia_monitor_config.yaml')
    
    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    user_config = yaml.safe_load(f) or {}
                    return {**self.defaults, **user_config}
            except Exception as e:
                logging.warning(f"Error loading config: {e}. Using defaults.")
        return self.defaults.copy()
    
    def save(self, config_data):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False)
            logging.info("✓ Configuration saved")
        except Exception as e:
            logging.error(f"Error saving config: {e}")

def retry_on_failure(max_retries: int = 3, delay: float = 2.0):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (TimeoutException, WebDriverException, Exception) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logging.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                        time.sleep(delay)
            logging.error(f"All {max_retries} attempts failed")
            raise last_exception
        return wrapper
    return decorator

@dataclass
class AttendanceRecord:
    date_time: datetime
    temperature: Optional[float]
    employee_id: str
    employee_name: str
    machine_name: str
    status: str
    action_details: str
    
    @classmethod
    def from_table_row(cls, headers: List[str], row: List[str]) -> 'AttendanceRecord':
        """Create record from table row with validation"""
        # Map headers to fields
        field_map = {
            'date_time': ['Date Time', 'DateTime', 'Time'],
            'temperature': ['Temperature', 'Temp'],
            'employee_id': ['Employee ID', 'EmpID'],
            'employee_name': ['Employee Name', 'Name'],
            'machine_name': ['Machine Name', 'Machine'],
            'status': ['Actions', 'Status']
        }
        
        # Extract values based on header mapping
        values = {}
        for field, possible_headers in field_map.items():
            for header in possible_headers:
                if header in headers:
                    idx = headers.index(header)
                    if idx < len(row):
                        values[field] = row[idx]
                        break
            # Fallback to index-based if header not found
            if field not in values and len(headers) > 0 and len(row) > 0:
                if field == 'date_time' and len(row) > 1:
                    values[field] = row[1]
                elif field == 'temperature' and len(row) > 2:
                    values[field] = row[2]
                elif field == 'employee_id' and len(row) > 4:
                    values[field] = row[4]
                elif field == 'employee_name' and len(row) > 3:
                    values[field] = row[3]
                elif field == 'machine_name' and len(row) > 5:
                    values[field] = row[5]
                elif field == 'status' and len(row) > 0:
                    values[field] = row[0]
        
        # Parse date time
        date_time = datetime.now()
        if 'date_time' in values:
            try:
                date_str = ' '.join(values['date_time'].split())
                date_time = datetime.strptime(date_str, '%m/%d/%Y %I:%M:%S %p')
            except ValueError:
                try:
                    date_time = datetime.strptime(date_str, '%m/%d/%Y %I:%M %p')
                except ValueError:
                    logging.warning(f"Could not parse date: {values['date_time']}")
        
        # Parse temperature
        temperature = None
        if 'temperature' in values:
            temp_str = values['temperature'].replace('°C', '').strip()
            try:
                temperature = float(temp_str) if temp_str else None
            except ValueError:
                pass
        
        return cls(
            date_time=date_time,
            temperature=temperature,
            employee_id=values.get('employee_id', ''),
            employee_name=values.get('employee_name', ''),
            machine_name=values.get('machine_name', ''),
            status=values.get('status', ''),
            action_details=values.get('status', '')
        )

class NIAAttendanceMonitor:
    def __init__(self, headless=True, driver_path=None, config=None):
        self.config = config or Config().load()
        self.base_url = self.config['base_url']
        self.auth_url = self.config['auth_url']
        self.headless = headless
        self.driver_path = driver_path
        self.state_file = os.path.expanduser('~/.nia_monitor_state.json')
        self._load_state()
    
    def _load_state(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    self.state = json.load(f)
            else:
                self.state = {'last_check': None, 'known_records': []}
        except Exception as e:
            logging.warning(f"Could not load state: {e}")
            self.state = {'last_check': None, 'known_records': []}
    
    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logging.error(f"Could not save state: {e}")
    
    def _hash_record(self, record: AttendanceRecord) -> str:
        key_data = f"{record.employee_id}_{record.date_time.isoformat()}_{record.status}"
        return hashlib.sha256(key_data.encode()).hexdigest()
    
    def detect_changes(self, current_records: List[AttendanceRecord]) -> Dict[str, Any]:
        current_hashes = [self._hash_record(record) for record in current_records]
        previous_hashes = self.state.get('known_records', [])
        
        new_records = [r for r in current_records if self._hash_record(r) not in previous_hashes]
        missing_records = [h for h in previous_hashes if h not in current_hashes]
        
        self.state['known_records'] = current_hashes
        self.state['last_check'] = datetime.now().isoformat()
        self._save_state()
        
        return {
            'new_records': new_records,
            'missing_records': missing_records,
            'total_current': len(current_records),
            'changes_detected': len(new_records) > 0 or len(missing_records) > 0
        }

    @contextmanager
    def browser_session(self):
        driver = None
        try:
            driver = self._create_driver()
            yield driver
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception as e:
                    logging.debug(f"Error closing driver: {e}")

    def _create_driver(self):
        """Create Firefox driver optimized for Android/Termux"""
        options = Options()
        
        if self.headless:
            options.add_argument("--headless")
        
        # Android/Termux specific options
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--width={self.config['browser_width']}")
        options.add_argument(f"--height={self.config['browser_height']}")
        
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
        driver.set_page_load_timeout(self.config['timeout'])
        driver.implicitly_wait(10)
        return driver

    @retry_on_failure(max_retries=3, delay=2.0)
    def _login_with_selenium(self, driver, employee_id, password):
        logging.debug("Opening login page with Selenium...")
        driver.get(f"{self.auth_url}?ReturnUrl={self.base_url}/")
        wait = WebDriverWait(driver, 30)

        # Wait for and fill login form
        employee_input = wait.until(EC.presence_of_element_located((By.NAME, "EmployeeID")))
        password_input = wait.until(EC.presence_of_element_located((By.NAME, "Password")))

        employee_input.clear()
        employee_input.send_keys(employee_id)
        password_input.clear()
        password_input.send_keys(password)

        # Take a screenshot before submitting (for debugging)
        try:
            driver.save_screenshot("before_login.png")
        except:
            pass

        # Submit the form
        try:
            submit_btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit']")
            submit_btn.click()
        except Exception as e:
            logging.debug(f"Could not find submit button: {e}")
            # Fallback: press Enter on password field
            password_input.submit()

        # Wait and check what happens after login
        try:
            # Wait up to 20 seconds for something to happen
            WebDriverWait(driver, 20).until(
                lambda d: d.current_url != f"{self.auth_url}?ReturnUrl={self.base_url}/"
            )
            
            current_url = driver.current_url
            logging.debug(f"Current URL after login attempt: {current_url}")
            
            # Check if login was successful
            if self.base_url in current_url:
                logging.info("✓ Login successful - redirected to attendance system")
            elif self.auth_url in current_url:
                # Still on login page - check for errors
                error_elements = driver.find_elements(By.CSS_SELECTOR, ".field-validation-error, .validation-summary-errors")
                if error_elements:
                    error_text = "\n".join([elem.text for elem in error_elements if elem.text])
                    raise Exception(f"Login failed: {error_text}")
                else:
                    raise Exception("Login failed - still on login page but no error message")
            else:
                # We're on some other page - might be successful
                logging.info(f"✓ Login redirected to: {current_url}")
                
        except TimeoutException:
            current_url = driver.current_url
            logging.debug(f"Final URL after timeout: {current_url}")
            
            if self.auth_url in current_url:
                raise Exception("Login timeout - never left login page")
            else:
                logging.info("✓ Login might have succeeded (page changed but timeout occurred)")
                
        # Take a screenshot after login attempt (for debugging)
        try:
            driver.save_screenshot("after_login.png")
        except:
            pass

    @retry_on_failure(max_retries=2, delay=3.0)
    def _load_attendance_html(self, driver):
        """Load attendance page with specific waiting for the table structure"""
        logging.debug("Navigating to attendance page...")
        
        try:
            driver.get(f"{self.base_url}/Attendance")
        except TimeoutException:
            logging.warning("Page load timed out, but continuing with current content...")
        
        wait = WebDriverWait(driver, 45)
        
        # Wait specifically for the table and its content
        try:
            # Wait for the table to be present
            logging.debug("Waiting for attendance table...")
            table_element = wait.until(EC.presence_of_element_located((By.ID, "DataTables_Table_0")))
            
            # Wait for rows to be populated in the specific tbody
            logging.debug("Waiting for table rows to load...")
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 #tbody1 tr")) > 0)
            
            # Extra wait for JavaScript to complete rendering
            time.sleep(3)
            
            # Verify we have data
            rows = driver.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 #tbody1 tr")
            logging.info(f"Found {len(rows)} attendance records")
            
        except TimeoutException as e:
            logging.warning(f"Timeout waiting for table content: {e}")
            # Continue anyway to capture whatever HTML we have
        
        html_content = driver.page_source
        logging.debug("Captured attendance page HTML (%s chars)", len(html_content))
        
        return html_content    
    
    def parse_attendance_html(self, html_content):
        """Parse attendance table HTML with the exact structure"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find the specific table
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        if not table:
            logging.error("No attendance table found with ID 'DataTables_Table_0'")
            return None

        # Extract table headers
        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                # Extract text from th elements, handling the nested structure
                for th in header_row.find_all('th'):
                    # Get clean text, removing extra whitespace
                    header_text = th.get_text(strip=True)
                    # Handle the multi-line headers by taking the main text
                    if '\n' in header_text:
                        # Take the first meaningful line
                        lines = [line.strip() for line in header_text.split('\n') if line.strip()]
                        header_text = lines[0] if lines else header_text
                    headers.append(header_text)

        logging.debug("Found table headers: %s", headers)

        # Extract table rows from the specific tbody
        rows = []
        table_body = table.find('tbody', {'id': 'tbody1'})
        if not table_body:
            logging.warning("No tbody with id 'tbody1' found, searching for any tbody")
            table_body = table.find('tbody')
        
        if table_body:
            for row in table_body.find_all('tr'):
                cells = row.find_all('td')
                if not cells:
                    continue
                    
                row_data = []
                for i, cell in enumerate(cells):
                    action_text = self._format_action_cell(cell)
                    if action_text:
                        row_data.append(action_text)
                        continue
                    if 'sorting_1' in cell.get('class', []):
                        # Handle date time cell with multiple spans
                        date_spans = cell.find_all('span')
                        if date_spans:
                            # Combine all span texts for date time
                            date_parts = [span.get_text(strip=True) for span in date_spans]
                            row_data.append(' '.join(date_parts))
                        else:
                            row_data.append(cell.get_text(strip=True))
                    else:
                        # Regular cell - just get text
                        row_data.append(cell.get_text(strip=True))
                rows.append(row_data)

        logging.debug("Attendance rows parsed: %s", len(rows))

        # Extract generated time from tfoot
        generated_time = "Unknown"
        tfoot = table.find('tfoot')
        if tfoot:
            tfoot_cells = tfoot.find_all('th')
            if len(tfoot_cells) >= 2:
                generated_time = tfoot_cells[1].get_text(strip=True)

        # Extract total records from caption
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
    
    def _format_action_cell(self, cell):
        """Return simplified status text for action cells"""
        link = cell.find('a')
        if not link:
            return None
        
        classes = link.get('class', [])
        
        # Determine status based on CSS classes
        if any('btn-danger' in cls for cls in classes):
            return 'FAILED'
        elif any('btn-success' in cls for cls in classes):
            return 'SUCCESS'
        elif any('btn-warning' in cls for cls in classes):
            return 'WARNING'
        else:
            # Fallback: try to extract from link text
            label = link.get_text(strip=True) or "Action"
            if 'fail' in label.lower():
                return 'FAILED'
            elif 'success' in label.lower():
                return 'SUCCESS'
            else:
                return label  # Return original if no status detected
    
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

    def get_attendance_data(self, employee_id, password):
        """Use Selenium to log in and extract attendance data"""
        with self.browser_session() as driver:
            try:
                self._login_with_selenium(driver, employee_id, password)
                html_content = self._load_attendance_html(driver)
                attendance_data = self.parse_attendance_html(html_content)
                
                if attendance_data and attendance_data['records']:
                    # Convert to AttendanceRecord objects for change detection
                    record_objects = [
                        AttendanceRecord.from_table_row(
                            attendance_data['table_headers'], 
                            row
                        ) for row in attendance_data['records']
                    ]
                    
                    # Detect changes
                    changes = self.detect_changes(record_objects)
                    if changes['changes_detected']:
                        logging.info(f"📈 Changes detected: {len(changes['new_records'])} new records")
                        if changes['new_records']:
                            for record in changes['new_records']:
                                logging.info(f"   NEW: {record.date_time} - {record.status}")
                    
                    # Save CSV with metadata
                    metadata = {
                        'employee_id': employee_id,
                        'total_records': len(attendance_data['records']),
                        'changes_detected': changes['changes_detected'],
                        'new_records_count': len(changes['new_records'])
                    }
                    self.save_as_csv(
                        attendance_data['table_headers'], 
                        attendance_data['records'],
                        metadata
                    )
                
                return attendance_data
                
            except Exception as e:
                logging.error(f"Error fetching attendance data: {e}")
                # Save debug HTML
                try:
                    with open("error_debug.html", "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    logging.info("Saved error page HTML to error_debug.html")
                except:
                    pass
                return None

    def save_as_csv(self, headers, rows, metadata=None):
        """Save attendance data as CSV file with metadata comments"""
        try:
            if not rows:
                logging.warning("No data to save as CSV")
                return

            # Generate filename with current date
            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

            # Write CSV with metadata comments
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                # Write metadata as comments
                if metadata:
                    csvfile.write("# NIA Attendance Export\n")
                    csvfile.write(f"# Generated: {datetime.now().isoformat()}\n")
                    for key, value in metadata.items():
                        csvfile.write(f"# {key}: {value}\n")
                    csvfile.write(f"# Total Records: {len(rows)}\n")
                    csvfile.write("#\n")
                
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
            return filename

        except Exception as e:
            logging.error(f"Error saving CSV: {e}")
            return None
    
    def analyze_attendance_patterns(self, attendance_data, employee_id):
        """Analyze attendance patterns and detect potential issues"""
        try:
            if not attendance_data or 'records' not in attendance_data:
                logging.warning("No attendance data to analyze")
                return None
            
            headers = attendance_data['table_headers']
            records = attendance_data['records']
            
            if not records:
                logging.warning(f"No records found")
                return None
            
            # Find column indices based on the actual headers
            action_idx = 0      # Action / status column
            date_time_idx = 1   # Date Time is the second column (index 1)
            emp_id_idx = 4      # Employee ID is the fifth column (index 4)
            temp_idx = 2        # Temperature is the third column (index 2)
            
            # Get ALL records for this employee (don't filter by FAILED status)
            my_records = []
            failed_records = []
            for record in records:
                if len(record) <= max(action_idx, emp_id_idx):
                    continue
                action_val = record[action_idx] if len(record) > action_idx else ""
                emp_val = record[emp_id_idx] if len(record) > emp_id_idx else ""
                
                # FIX: Better employee ID matching with stripping and type conversion
                is_for_employee = False
                if emp_val and employee_id:
                    # Convert both to strings and strip whitespace for comparison
                    emp_val_clean = str(emp_val).strip()
                    employee_id_clean = str(employee_id).strip()
                    is_for_employee = emp_val_clean == employee_id_clean
                
                # ONLY include records for this employee
                if is_for_employee:
                    my_records.append(record)
                    if "FAILED" in str(action_val).upper():
                        failed_records.append(record)

            # DEBUG: Show what we found
            logging.debug(f"Looking for employee ID: '{employee_id}'")
            logging.debug(f"Employee ID type: {type(employee_id)}")
            for i, record in enumerate(records[:3]):  # Show first 3 records for debugging
                if len(record) > emp_id_idx:
                    emp_val = record[emp_id_idx]
                    logging.debug(f"Record {i} emp_id: '{emp_val}' (type: {type(emp_val)})")
            
            if failed_records:
                logging.warning("Found %s FAILED record(s) in your data", len(failed_records))
            
            if not my_records:
                logging.warning(f"No matching records found for Employee ID: {employee_id}")
                # Show what employee IDs ARE in the data for debugging
                emp_ids_in_data = set()
                for record in records:
                    if len(record) > emp_id_idx:
                        emp_ids_in_data.add(record[emp_id_idx])
                logging.info(f"Employee IDs found in data: {emp_ids_in_data}")
                return None
            
            # Parse dates and analyze patterns
            today = datetime.now().date()
            today_records = []

            for record in my_records:
                if len(record) > date_time_idx:
                    date_str = record[date_time_idx]
                    try:
                        # Parse date string like "11/18/2025 12:57:39 PM"
                        # Remove any extra spaces for clean parsing
                        date_str_clean = ' '.join(date_str.split())
                        # Extract just the date part (before the time)
                        date_part = date_str_clean.split()[0]
                        record_date = datetime.strptime(date_part, '%m/%d/%Y').date()
                        if record_date == today:
                            today_records.append(record)
                    except ValueError as e:
                        logging.debug(f"Date parsing error for '{date_str}': {e}")
                        # Try to extract date using different methods
                        try:
                            # Look for MM/DD/YYYY pattern
                            date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', date_str)
                            if date_match:
                                record_date = datetime.strptime(date_match.group(1), '%m/%d/%Y').date()
                                if record_date == today:
                                    today_records.append(record)
                        except ValueError:
                            logging.warning(f"Could not parse date: {date_str}")
            
            logging.info("YOUR records for today (%s): %s", today, len(today_records))
            
            # Show today's records
            if today_records:
                logging.info("Your attendance today:")
                for record in today_records:
                    time_in_record = record[date_time_idx] if len(record) > date_time_idx else "N/A"
                    temp = record[temp_idx] if len(record) > temp_idx else "N/A"
                    action = record[action_idx] if len(record) > action_idx else "N/A"
                    status = "❌ FAILED" if "FAILED" in str(action).upper() else "✅ SUCCESS"
                    logging.info(f"  - {time_in_record} (Temp: {temp}°C) - {status}")
                
                # Check for potential issues
                if len(today_records) < 2:
                    logging.warning("⚠️  WARNING: Only one record today. Make sure you have both Time In and Time Out.")
                elif len(today_records) % 2 != 0:
                    logging.warning("⚠️  WARNING: Odd number of records today. Possible missing Time Out.")
                else:
                    logging.info("✓ Good: Even number of records today (likely both Time In and Time Out)")
            else:
                logging.info("No records found for today")
            
            return {
                'employee_id': employee_id,
                'total_records': len(my_records),  # Only YOUR records
                'total_all_records': len(records), # All records in system
                'today_records': len(today_records),
                'today_details': today_records,
                'failed_records': len(failed_records),
                'analysis_timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logging.error(f"Error analyzing attendance: {e}")
            return None  
    
    def save_attendance_record(self, attendance_data):
        """Save attendance data to a local JSON file"""
        try:
            filename = f"nia_attendance_backup_{datetime.now().strftime('%Y%m')}.json"
            
            # Load existing data or create new list
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = []
            
            # Add new record
            data.append(attendance_data)
            
            # Save back to file
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logging.info(f"✓ Attendance record saved to {filename}")
            
        except Exception as e:
            logging.error(f"Error saving attendance record: {e}")
    
    def _hash_records(self, records):
        hasher = hashlib.sha256()
        for row in records:
            line = "||".join(row)
            hasher.update(line.encode('utf-8', errors='replace'))
        return hasher.hexdigest()

    def monitor_attendance(self, employee_id, password, interval_seconds=300, max_checks=None, interactive=False):
        """Monitor attendance with optional interactive mode"""
        if interactive:
            return self.interactive_monitor(employee_id, password, interval_seconds)
        
        # Original non-interactive monitoring
        logging.info("Starting continuous monitoring (interval: %s seconds)", interval_seconds)
        checks = 0
        
        try:
            while True:
                attendance_data = self.get_attendance_data(employee_id, password)
                
                if attendance_data:
                    analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
                    if analysis:
                        self.save_attendance_record(analysis)
                else:
                    logging.warning("No attendance data retrieved this cycle.")

                checks += 1
                if max_checks and checks >= max_checks:
                    logging.info("Reached max checks limit (%s). Stopping monitor.", max_checks)
                    break

                logging.debug("Sleeping for %s seconds before next check...", interval_seconds)
                time.sleep(interval_seconds)
                
        except KeyboardInterrupt:
            logging.info("Monitoring interrupted by user.")
        
    def one_time_check(self, employee_id, password):
        """Perform a single attendance check with analysis using Selenium"""
        attendance_data = self.get_attendance_data(employee_id, password)
        if attendance_data:
            analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
            if analysis:
                self.save_attendance_record(analysis)
            else:
                # Create a basic analysis structure if analysis fails
                analysis = {
                    'employee_id': employee_id,
                    'total_records': 0,
                    'total_all_records': len(attendance_data.get('records', [])),
                    'today_records': 0,
                    'today_details': [],
                    'failed_records': 0,
                    'analysis_timestamp': datetime.now().isoformat(),
                    'note': 'Analysis failed - showing raw data'
                }
            return {
                'analysis': analysis,
                'attendance_data': attendance_data
            }
        return None
    def interactive_monitor(self, employee_id, password, interval_seconds=300):
        """Simplified interactive monitoring"""
        console = Console()
        
        console.print("[green]🚀 Starting interactive monitor...[/green]")
        console.print("[dim]Press 'r' to refresh, 's' to save, 'q' to quit[/dim]")
        
        check_count = 0
        
        while True:
            # Clear screen and show header
            console.clear()
            console.rule(f"[bold blue]NIA Attendance Monitor - Check #{check_count + 1}[/bold blue]")
            
            # Perform check
            console.print("[yellow]🔄 Checking attendance...[/yellow]")
            attendance_data = self.get_attendance_data(employee_id, password)
            
            if attendance_data:
                check_count += 1
                analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
                
                # Display results
                if analysis and analysis.get('today_details'):
                    table = Table(show_header=True, header_style="bold cyan")
                    table.add_column("Entry #", justify="right", style="white")
                    table.add_column("Date & Time", style="green")
                    table.add_column("Temperature", style="yellow", justify="center")
                    table.add_column("Status", style="magenta", justify="center")
                    
                    for idx, row in enumerate(analysis['today_details'], start=1):
                        date_time = row[1] if len(row) > 1 else "N/A"
                        temperature = row[2] if len(row) > 2 else "N/A"
                        status = row[0] if len(row) > 0 else "N/A"
                        
                        if "|" in str(status):
                            status = str(status).split("|")[0].strip()
                        
                        row_style = "red" if "FAILED" in str(status).upper() else None
                        table.add_row(str(idx), date_time, temperature, status, style=row_style)
                    
                    console.print(table)
                    console.print(f"[green]✓ Found {len(analysis['today_details'])} records for today[/green]")
                else:
                    console.print("[yellow]No records found for today[/yellow]")
            else:
                console.print("[red]❌ Failed to fetch attendance data[/red]")
            
            # Show controls
            console.print(f"\n[dim]Check #{check_count} completed at {datetime.now().strftime('%H:%M:%S')}[/dim]")
            console.print("\n[bold]Controls:[/bold] [green]R[/green]efresh | [yellow]S[/yellow]ave | [red]Q[/red]uit")
            
            # Get user input
            try:
                key = console.input("\nEnter command: ").lower().strip()
                
                if key == 'q':
                    break
                elif key == 's' and attendance_data:
                    filename = self.save_as_csv(
                        attendance_data['table_headers'],
                        attendance_data['records'],
                        {'manual_save': True, 'check_count': check_count}
                    )
                    if filename:
                        console.print(f"[green]💾 Saved to {filename}[/green]")
                    console.input("Press Enter to continue...")
                elif key == 'r':
                    continue  # Just continue to next iteration
                else:
                    console.print("[yellow]Unknown command. Use R, S, or Q.[/yellow]")
                    console.input("Press Enter to continue...")
                    
            except KeyboardInterrupt:
                console.print("\n[yellow]Stopping monitor...[/yellow]")
                break
        
        console.print("[green]✅ Monitor stopped[/green]")


def main():
    config = Config().load()
    
    parser = argparse.ArgumentParser(
        description="NIA Attendance Monitor for Android/Termux",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--mode',
        choices=['once', 'monitor', 'config'],
        help='Run once, continuously monitor, or show config'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=config['monitor_interval'],
        help='Monitoring interval in seconds'
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
    parser.add_argument(
        '--employee-id',
        help='Employee ID (can also use NIA_EMPLOYEE_ID env var)'
    )
    parser.add_argument(
        '--password',
        help='Password (can also use NIA_PASSWORD env var; use with caution)'
    )
    parser.add_argument(
        '--config-show',
        action='store_true',
        help='Show current configuration'
    )
    parser.add_argument(
        '--config-set',
        nargs=2,
        action='append',
        metavar=('KEY', 'VALUE'),
        help='Set configuration value (e.g., --config-set monitor_interval 600)'
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Use interactive monitor mode with live display'
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle configuration commands
    if args.config_show:
        console.print("Current configuration:")
        console.print_json(json.dumps(config, indent=2))
        return
    
    if args.config_set:
        config_obj = Config()
        current_config = config_obj.load()
        for key, value in args.config_set:
            # Convert value to appropriate type
            if value.isdigit():
                value = int(value)
            elif value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            current_config[key] = value
        config_obj.save(current_config)
        console.print("✓ Configuration updated")
        return

    monitor = NIAAttendanceMonitor(
        headless=not args.show_browser, 
        driver_path=args.driver_path,
        config=config
    )
    
    # ==== FIXED CREDENTIALS SECTION ====
    # Get credentials securely (now checks config file too!)
    employee_id = (args.employee_id or 
                   os.environ.get('NIA_EMPLOYEE_ID') or 
                   config.get('employee_id'))
    if not employee_id:
        employee_id = Prompt.ask("[bold]Enter your Employee ID[/]")
        
    password = (args.password or 
                os.environ.get('NIA_PASSWORD') or 
                config.get('password'))
    if not password:
        console.print("[bold]Enter your Password[/] (input hidden)")
        password = getpass.getpass("")
    # ==== END FIX ====
    
    if args.mode:
        choice = '1' if args.mode == 'once' else '2' if args.mode == 'monitor' else '3'
    else:
        console.print("\n[bold]Choose operation:[/bold]")
        console.print("1. One-time attendance check with analysis")
        console.print("2. Start continuous monitoring")
        console.print("3. Show configuration")
        choice = Prompt.ask("Enter choice", choices=["1", "2", "3"], default="1")
    
    if choice == "1":
        result = monitor.one_time_check(employee_id, password)
        if result:
            console.rule("[bold green]CHECK COMPLETED SUCCESSFULLY[/bold green]")
            
            analysis = result['analysis']
            attendance_data = result['attendance_data']
            
            console.print(f"[bold]Your records found:[/] {analysis.get('total_records', 0)}")
            console.print(f"[bold]Today's records:[/] {analysis.get('today_records', 0)}")
            if analysis.get('failed_records', 0) > 0:
                console.print(f"[bold red]Failed records:[/] {analysis['failed_records']}")
            
            if analysis.get('note'):
                console.print(f"[yellow]{analysis['note']}[/yellow]")

            # Show ONLY TODAY'S records in the table with specific columns
            today_details = analysis.get('today_details', [])
            if today_details:
                # Create table with only the columns we want to show
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("Entry #", justify="right", style="white")
                table.add_column("Date & Time", style="green", overflow="fold")
                table.add_column("Temperature", style="yellow", justify="center")
                table.add_column("Status", style="magenta", justify="center")  # Changed to just "Status"
                
                # Define column indices based on the table structure
                action_idx = 0      # Actions column
                date_time_idx = 1   # Date Time column  
                temp_idx = 2        # Temperature column
                
                for idx, row in enumerate(today_details, start=1):
                    # Extract the specific columns we want to display
                    date_time = row[date_time_idx] if len(row) > date_time_idx else "N/A"
                    temperature = row[temp_idx] if len(row) > temp_idx else "N/A"
                    status = row[action_idx] if len(row) > action_idx else "N/A"
                    
                    # Clean up the status text if it still has extra details
                    if "|" in str(status):
                        # Extract just the status part (before the first |)
                        status = str(status).split("|")[0].strip()
                    
                    # Style based on status
                    if "FAILED" in str(status).upper():
                        row_style = "red"
                        status_display = "FAILED"
                    elif "SUCCESS" in str(status).upper():
                        row_style = "green"
                        status_display = "SUCCESS"
                    elif "WARNING" in str(status).upper():
                        row_style = "yellow"
                        status_display = "WARNING"
                    else:
                        row_style = None
                        status_display = status
                    
                    table.add_row(
                        str(idx),
                        date_time,
                        temperature,
                        status_display,  # Use cleaned status
                        style=row_style
                    )
                
                console.print(table)
                console.print(f"[bold]Today's records displayed:[/] {len(today_details)}")
            else:
                console.print("[yellow]No records found for today[/yellow]")
                # Show available dates for context
                if attendance_data and 'records' in attendance_data:
                    dates_found = set()
                    emp_id_idx = 4  # Employee ID column
                    for record in attendance_data['records']:
                        if len(record) > emp_id_idx and str(record[emp_id_idx]).strip() == str(employee_id).strip():
                            if len(record) > 1:  # Has date field
                                # Extract just the date part (before time)
                                date_str = record[1]
                                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', date_str)
                                if date_match:
                                    dates_found.add(date_match.group(1))
                    if dates_found:
                        console.print(f"[dim]Your records found on dates: {sorted(dates_found)}[/dim]")
        else:
            console.print("[bold red]One-time check failed![/bold red]")
    
    elif choice == "2":
        monitor.monitor_attendance(
            employee_id,
            password,
            interval_seconds=args.interval,
            max_checks=args.max_checks,
            interactive=args.interactive  # Add this
        )
    
    elif choice == "3":
        console.print("Current configuration:")
        console.print_json(json.dumps(config, indent=2))
    
    else:
        console.print("[bold red]Invalid choice[/bold red]")

if __name__ == "__main__":
    main()