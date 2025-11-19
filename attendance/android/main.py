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

console = Console()

# Set up logging with Rich handler for mobile-friendly output
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="%H:%M:%S",  # Shorter timestamp for mobile
    handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True, show_path=False)]
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
            'browser_height': 1080,
            'enable_csv': False  # New: CSV disabled by default
        }
        self.config_path = os.path.expanduser('~/.nia_monitor_config.yaml')
    
    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    user_config = yaml.safe_load(f) or {}
                    return {**self.defaults, **user_config}
            except Exception as e:
                logging.warning(f"Config load error: {e}")
        return self.defaults.copy()
    
    def save(self, config_data):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False)
        except Exception as e:
            logging.error(f"Save config error: {e}")

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
                        time.sleep(delay)
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
        field_map = {
            'date_time': ['Date Time', 'DateTime', 'Time'],
            'temperature': ['Temperature', 'Temp'],
            'employee_id': ['Employee ID', 'EmpID'],
            'employee_name': ['Employee Name', 'Name'],
            'machine_name': ['Machine Name', 'Machine'],
            'status': ['Actions', 'Status']
        }
        
        values = {}
        for field, possible_headers in field_map.items():
            for header in possible_headers:
                if header in headers:
                    idx = headers.index(header)
                    if idx < len(row):
                        values[field] = row[idx]
                        break
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
        
        date_time = datetime.now()
        if 'date_time' in values:
            try:
                date_str = ' '.join(values['date_time'].split())
                date_time = datetime.strptime(date_str, '%m/%d/%Y %I:%M:%S %p')
            except ValueError:
                try:
                    date_time = datetime.strptime(date_str, '%m/%d/%Y %I:%M %p')
                except ValueError:
                    pass
        
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
            logging.warning(f"State load error: {e}")
            self.state = {'last_check': None, 'known_records': []}
    
    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logging.error(f"Save state error: {e}")
    
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
        """Create browser session with better error handling"""
        driver = None
        max_attempts = 2
        
        for attempt in range(max_attempts):
            try:
                driver = self._create_driver()
                yield driver
                break  # Success
            except Exception as e:
                logging.warning(f"Browser session attempt {attempt + 1} failed: {e}")
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass
                if attempt == max_attempts - 1:
                    raise  # Re-raise on final attempt
                else:
                    raise Exception("Failed to create browser session after multiple attempts")
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass  # Ignore cleanup errors
                time.sleep(2)  # Wait before retry

    def _create_driver(self):
        """Create Firefox driver with better configuration"""
        options = Options()
        
        if self.headless:
            options.add_argument("--headless")
        
        # Mobile-optimized options
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--width=800")
        options.add_argument("--height=600")
        
        # Better performance options
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins")
        options.add_argument("--disable-images")  # Faster loading
        options.set_preference("permissions.default.image", 2)  # Disable images
        
        # Set Firefox binary path for Termux
        firefox_binary = "/data/data/com.termux/files/usr/bin/firefox"
        if os.path.exists(firefox_binary):
            options.binary_location = firefox_binary
        
        # GeckoDriver setup with better error handling
        if self.driver_path:
            service = Service(executable_path=self.driver_path)
        else:
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
                service = Service()
        
        try:
            driver = webdriver.Firefox(service=service, options=options)
            driver.set_page_load_timeout(self.config['timeout'])
            driver.implicitly_wait(10)
            return driver
        except Exception as e:
            logging.error(f"Failed to create driver: {e}")
            raise
    @retry_on_failure(max_retries=3, delay=2.0)
    def _login_with_selenium(self, driver, employee_id, password):
        """Login once and keep session"""
        logging.info("Logging in...")
        driver.get(f"{self.auth_url}?ReturnUrl={self.base_url}/")
        wait = WebDriverWait(driver, 30)

        employee_input = wait.until(EC.presence_of_element_located((By.NAME, "EmployeeID")))
        password_input = wait.until(EC.presence_of_element_located((By.NAME, "Password")))

        employee_input.clear()
        employee_input.send_keys(employee_id)
        password_input.clear()
        password_input.send_keys(password)

        # Submit form
        try:
            submit_btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit']")
            submit_btn.click()
        except Exception:
            password_input.submit()

        # Wait for login completion
        try:
            WebDriverWait(driver, 20).until(
                lambda d: d.current_url != f"{self.auth_url}?ReturnUrl={self.base_url}/"
            )
            
            current_url = driver.current_url
            if self.base_url in current_url:
                logging.info("✓ Login OK")
            elif self.auth_url in current_url:
                error_elements = driver.find_elements(By.CSS_SELECTOR, ".field-validation-error, .validation-summary-errors")
                if error_elements:
                    error_text = "\n".join([elem.text for elem in error_elements if elem.text])
                    raise Exception(f"Login failed: {error_text}")
                else:
                    raise Exception("Login failed")
            else:
                logging.info(f"✓ Login redirected")
                
        except TimeoutException:
            current_url = driver.current_url
            if self.auth_url in current_url:
                raise Exception("Login timeout")
            else:
                logging.info("✓ Login likely OK")

    @retry_on_failure(max_retries=2, delay=3.0)
    def _load_attendance_html(self, driver, refresh_only=False):
        """Load attendance page - can refresh without re-login"""
        if not refresh_only:
            logging.info("Loading page...")
            driver.get(f"{self.base_url}/Attendance")
        else:
            logging.info("Refreshing...")
            driver.refresh()
        
        wait = WebDriverWait(driver, 45)
        
        try:
            table_element = wait.until(EC.presence_of_element_located((By.ID, "DataTables_Table_0")))
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 #tbody1 tr")) > 0)
            time.sleep(2)  # Reduced wait for mobile
            
            rows = driver.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 #tbody1 tr")
            logging.info(f"Found {len(rows)} records")
            
        except TimeoutException as e:
            logging.warning("Timeout loading table")
        
        return driver.page_source
    
    def parse_attendance_html(self, html_content):
        """Parse attendance table HTML"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        if not table:
            logging.error("No table found")
            return None

        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                for th in header_row.find_all('th'):
                    header_text = th.get_text(strip=True)
                    if '\n' in header_text:
                        lines = [line.strip() for line in header_text.split('\n') if line.strip()]
                        header_text = lines[0] if lines else header_text
                    headers.append(header_text)

        rows = []
        table_body = table.find('tbody', {'id': 'tbody1'})
        if not table_body:
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
                        date_spans = cell.find_all('span')
                        if date_spans:
                            date_parts = [span.get_text(strip=True) for span in date_spans]
                            row_data.append(' '.join(date_parts))
                        else:
                            row_data.append(cell.get_text(strip=True))
                    else:
                        row_data.append(cell.get_text(strip=True))
                rows.append(row_data)

        return {
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'table_headers': headers,
            'records': rows,
            'records_found': len(rows)
        }
    
    def _format_action_cell(self, cell):
        """Return simplified status text"""
        link = cell.find('a')
        if not link:
            return None
        
        classes = link.get('class', [])
        
        if any('btn-danger' in cls for cls in classes):
            return 'FAILED'
        elif any('btn-success' in cls for cls in classes):
            return 'SUCCESS'
        elif any('btn-warning' in cls for cls in classes):
            return 'WARNING'
        else:
            label = link.get_text(strip=True) or "Action"
            if 'fail' in label.lower():
                return 'FAILED'
            elif 'success' in label.lower():
                return 'SUCCESS'
            else:
                return label

    def get_attendance_data(self, employee_id, password, refresh_only=False):
        """Get attendance data - always use fresh session for reliability"""
        # Don't try to reuse sessions - it's causing connection issues
        # Always create a new session for each request
        with self.browser_session() as driver:
            try:
                if not refresh_only:
                    logging.info("Logging in...")
                    self._login_with_selenium(driver, employee_id, password)
                else:
                    # For "refresh", we still need to login since we can't maintain sessions reliably
                    logging.info("Re-logging in for refresh...")
                    self._login_with_selenium(driver, employee_id, password)
                
                html_content = self._load_attendance_html(driver, refresh_only=False)
                attendance_data = self.parse_attendance_html(html_content)
                return self._process_attendance_data(attendance_data, employee_id)
                
            except Exception as e:
                logging.error(f"Error: {e}")
                return None
            
    def _load_attendance_html(self, driver, refresh_only=False):
        """Load attendance page with better error handling"""
        try:
            if not refresh_only:
                logging.info("Loading page...")
                driver.get(f"{self.base_url}/Attendance")
            else:
                logging.info("Refreshing...")
                # Try multiple approaches for refresh
                try:
                    driver.refresh()
                except:
                    # Fallback: navigate to the URL again
                    driver.get(f"{self.base_url}/Attendance")
            
            wait = WebDriverWait(driver, 45)
            
            # Wait for the table with multiple fallback strategies
            try:
                # Strategy 1: Wait for table by ID
                table_element = wait.until(EC.presence_of_element_located((By.ID, "DataTables_Table_0")))
                
                # Strategy 2: Wait for rows in specific tbody
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 #tbody1 tr")) > 0)
                
            except TimeoutException:
                # Strategy 3: Look for any table with attendance data
                try:
                    wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "table tbody tr")) > 0)
                    logging.info("Found table with generic selector")
                except TimeoutException:
                    logging.error("No table found after refresh")
                    # Save page source for debugging
                    try:
                        with open("refresh_debug.html", "w", encoding="utf-8") as f:
                            f.write(driver.page_source)
                        logging.info("Saved refresh page to refresh_debug.html")
                    except:
                        pass
                    raise Exception("No attendance table found")
            
            # Give it a moment for JavaScript to render
            time.sleep(2)
            
            rows = driver.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 #tbody1 tr")
            if not rows:
                # Fallback: try generic rows
                rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                
            logging.info(f"Found {len(rows)} records")
            return driver.page_source
            
        except Exception as e:
            logging.error(f"Page load error: {e}")
            raise

    def parse_attendance_html(self, html_content):
        """Parse attendance table HTML with better fallbacks"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Try multiple table selectors
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        if not table:
            # Fallback: look for any table with attendance data
            tables = soup.find_all('table')
            for t in tables:
                # Check if this looks like an attendance table
                headers = t.find_all('th')
                header_texts = [h.get_text(strip=True).lower() for h in headers]
                if any('time' in text or 'date' in text or 'employee' in text for text in header_texts):
                    table = t
                    logging.info("Using fallback table")
                    break
        
        if not table:
            logging.error("No table found in HTML")
            # Debug: save the problematic HTML
            try:
                with open("no_table_debug.html", "w", encoding="utf-8") as f:
                    f.write(html_content)
                logging.info("Saved problematic HTML to no_table_debug.html")
            except:
                pass
            return None

        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                for th in header_row.find_all('th'):
                    header_text = th.get_text(strip=True)
                    if '\n' in header_text:
                        lines = [line.strip() for line in header_text.split('\n') if line.strip()]
                        header_text = lines[0] if lines else header_text
                    headers.append(header_text)

        rows = []
        table_body = table.find('tbody', {'id': 'tbody1'})
        if not table_body:
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
                        date_spans = cell.find_all('span')
                        if date_spans:
                            date_parts = [span.get_text(strip=True) for span in date_spans]
                            row_data.append(' '.join(date_parts))
                        else:
                            row_data.append(cell.get_text(strip=True))
                    else:
                        row_data.append(cell.get_text(strip=True))
                rows.append(row_data)

        return {
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'table_headers': headers,
            'records': rows,
            'records_found': len(rows)
        }

    def _process_attendance_data(self, attendance_data, employee_id):
        """Process attendance data with change detection"""
        if attendance_data and attendance_data['records']:
            record_objects = [
                AttendanceRecord.from_table_row(
                    attendance_data['table_headers'], 
                    row
                ) for row in attendance_data['records']
            ]
            
            changes = self.detect_changes(record_objects)
            if changes['changes_detected']:
                logging.info(f"New: {len(changes['new_records'])}")
            
            # Conditional CSV saving
            if self.config.get('enable_csv', False):
                metadata = {
                    'employee_id': employee_id,
                    'total_records': len(attendance_data['records']),
                    'changes_detected': changes['changes_detected']
                }
                self.save_as_csv(
                    attendance_data['table_headers'], 
                    attendance_data['records'],
                    metadata
                )
        
        return attendance_data

    def save_as_csv(self, headers, rows, metadata=None):
        """Save attendance data as CSV (optional)"""
        if not self.config.get('enable_csv', False):
            return None
            
        try:
            if not rows:
                return None

            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                if metadata:
                    csvfile.write("# NIA Attendance Export\n")
                    csvfile.write(f"# Generated: {datetime.now().isoformat()}\n")
                    csvfile.write(f"# Records: {len(rows)}\n#\n")
                
                writer = csv.writer(csvfile)
                writer.writerow(headers)
                writer.writerows(rows)

            logging.info(f"Saved {filename}")
            return filename

        except Exception as e:
            logging.error(f"CSV error: {e}")
            return None
    
    def analyze_attendance_patterns(self, attendance_data, employee_id):
        """Analyze attendance patterns - mobile optimized logs"""
        try:
            if not attendance_data or 'records' not in attendance_data:
                return None
            
            headers = attendance_data['table_headers']
            records = attendance_data['records']
            
            if not records:
                return None
            
            action_idx = 0
            date_time_idx = 1
            emp_id_idx = 4
            temp_idx = 2
            
            # Get records for this employee
            my_records = []
            failed_records = []
            for record in records:
                if len(record) <= max(action_idx, emp_id_idx):
                    continue
                action_val = record[action_idx] if len(record) > action_idx else ""
                emp_val = record[emp_id_idx] if len(record) > emp_id_idx else ""
                
                is_for_employee = False
                if emp_val and employee_id:
                    emp_val_clean = str(emp_val).strip()
                    employee_id_clean = str(employee_id).strip()
                    is_for_employee = emp_val_clean == employee_id_clean
                
                if is_for_employee:
                    my_records.append(record)
                    if "FAILED" in str(action_val).upper():
                        failed_records.append(record)

            if not my_records:
                return None
            
            # Parse today's records
            today = datetime.now().date()
            today_records = []

            for record in my_records:
                if len(record) > date_time_idx:
                    date_str = record[date_time_idx]
                    try:
                        date_str_clean = ' '.join(date_str.split())
                        date_part = date_str_clean.split()[0]
                        record_date = datetime.strptime(date_part, '%m/%d/%Y').date()
                        if record_date == today:
                            today_records.append(record)
                    except ValueError:
                        try:
                            date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', date_str)
                            if date_match:
                                record_date = datetime.strptime(date_match.group(1), '%m/%d/%Y').date()
                                if record_date == today:
                                    today_records.append(record)
                        except ValueError:
                            pass
            
            # Mobile-friendly logging
            if today_records:
                if len(today_records) < 2:
                    logging.warning("⚠️  Need Time In/Out")
                elif len(today_records) % 2 != 0:
                    logging.warning("⚠️  Missing Time Out?")
                else:
                    logging.info("✓ Records OK")
            else:
                logging.info("No today records")
            
            return {
                'employee_id': employee_id,
                'total_records': len(my_records),
                'total_all_records': len(records),
                'today_records': len(today_records),
                'today_details': today_records,
                'failed_records': len(failed_records)
            }
            
        except Exception as e:
            logging.error(f"Analysis error: {e}")
            return None

    def monitor_attendance(self, employee_id, password, interval_seconds=300, max_checks=None, interactive=False):
        """Monitor attendance with optional interactive mode"""
        if interactive:
            return self.interactive_monitor(employee_id, password, interval_seconds)
        
        # Original non-interactive monitoring
        logging.info(f"Monitoring every {interval_seconds}s")
        checks = 0
        
        try:
            while True:
                attendance_data = self.get_attendance_data(employee_id, password)
                
                if attendance_data:
                    analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
                    if analysis:
                        self.save_attendance_record(analysis)
                else:
                    logging.warning("No data this cycle")

                checks += 1
                if max_checks and checks >= max_checks:
                    logging.info(f"Reached {max_checks} checks")
                    break

                time.sleep(interval_seconds)
                
        except KeyboardInterrupt:
            logging.info("Stopped by user")
    
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
            
            logging.info(f"✓ Saved to {filename}")
            
        except Exception as e:
            logging.error(f"Save error: {e}")
    
    def interactive_monitor(self, employee_id, password, interval_seconds=300):
        """Interactive monitoring - simplified without session reuse"""
        console = Console()
        
        console.print("[green]🚀 Interactive Monitor[/green]")
        console.print("[dim]R=Refresh S=Save Q=Quit[/dim]")
        
        check_count = 0
        
        while True:
            console.clear()
            console.rule(f"[blue]Check #{check_count + 1}[/blue]")
            
            console.print("[yellow]🔄 Checking...[/yellow]")
            
            # Always create fresh session - more reliable
            attendance_data = self.get_attendance_data(employee_id, password, refresh_only=False)
            
            if attendance_data:
                check_count += 1
                analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
                
                if analysis and analysis.get('today_details'):
                    # Mobile-optimized table
                    table = Table(show_header=True, header_style="bold cyan", width=60)
                    table.add_column("#", justify="right", style="white", width=4)
                    table.add_column("Time", style="green", width=15)
                    table.add_column("Temp", style="yellow", width=6)
                    table.add_column("Status", style="magenta", width=8)
                    
                    for idx, row in enumerate(analysis['today_details'], start=1):
                        date_time = row[1] if len(row) > 1 else "N/A"
                        temperature = row[2] if len(row) > 2 else "N/A"
                        status = row[0] if len(row) > 0 else "N/A"
                        
                        if "|" in str(status):
                            status = str(status).split("|")[0].strip()
                        
                        # Shorten time for mobile
                        time_part = date_time.split()[1] if ' ' in date_time else date_time
                        row_style = "red" if "FAILED" in str(status).upper() else None
                        table.add_row(str(idx), time_part, temperature, status, style=row_style)
                    
                    console.print(table)
                    console.print(f"[green]✓ {len(analysis['today_details'])} today[/green]")
                else:
                    console.print("[yellow]No today records[/yellow]")
            else:
                console.print("[red]❌ Fetch failed[/red]")
            
            # Mobile-friendly status
            console.print(f"\n[dim]#{check_count} {datetime.now().strftime('%H:%M')}[/dim]")
            console.print("[bold]R[/bold]efresh [bold]S[/bold]ave [bold]Q[/bold]uit")
            
            # Input with mobile-friendly prompts
            try:
                key = console.input("\nCmd: ").lower().strip()
                
                if key == 'q':
                    break
                elif key == 's':
                    if attendance_data and self.config.get('enable_csv', False):
                        filename = self.save_as_csv(
                            attendance_data['table_headers'],
                            attendance_data['records'],
                            {'manual_save': True, 'check_count': check_count}
                        )
                        if filename:
                            console.print(f"[green]Saved[/green]")
                        console.input("Enter...")
                    else:
                        console.print("[yellow]CSV disabled[/yellow]")
                        console.input("Enter...")
                elif key == 'r':
                    continue  # This will just loop again with fresh session
                else:
                    console.print("[yellow]Use R,S,Q[/yellow]")
                    console.input("Enter...")
                    
            except KeyboardInterrupt:
                console.print("\n[yellow]Stopping...[/yellow]")
                break
        
        console.print("[green]✅ Stopped[/green]")
    def one_time_check(self, employee_id, password):
        """Single check with mobile-optimized output"""
        attendance_data = self.get_attendance_data(employee_id, password)
        if attendance_data:
            analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
            if not analysis:
                analysis = {
                    'employee_id': employee_id,
                    'total_records': 0,
                    'total_all_records': len(attendance_data.get('records', [])),
                    'today_records': 0,
                    'today_details': [],
                    'failed_records': 0
                }
            return {
                'analysis': analysis,
                'attendance_data': attendance_data
            }
        return None


def main():
    config = Config().load()
    
    parser = argparse.ArgumentParser(
        description="NIA Attendance Monitor - Mobile Optimized",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--mode',
        choices=['once', 'monitor', 'config'],
        help='Operation mode'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=config['monitor_interval'],
        help='Monitoring interval (seconds)'
    )
    parser.add_argument(
        '--enable-csv',
        action='store_true',
        help='Enable CSV export (disabled by default)'
    )
    parser.add_argument(
        '--show-browser',
        action='store_true',
        help='Show browser window'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--employee-id',
        help='Employee ID'
    )
    parser.add_argument(
        '--password',
        help='Password'
    )
    parser.add_argument(
        '--config-show',
        action='store_true',
        help='Show configuration'
    )
    parser.add_argument(
        '--config-set',
        nargs=2,
        action='append',
        metavar=('KEY', 'VALUE'),
        help='Set configuration value'
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Use interactive monitor'
    )
    
    args = parser.parse_args()

    # Update config with CSV setting
    if args.enable_csv:
        config['enable_csv'] = True

    if args.config_show:
        console.print("Configuration:")
        console.print_json(json.dumps(config, indent=2))
        return
    
    if args.config_set:
        config_obj = Config()
        current_config = config_obj.load()
        for key, value in args.config_set:
            if value.isdigit():
                value = int(value)
            elif value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            current_config[key] = value
        config_obj.save(current_config)
        console.print("✓ Config updated")
        return

    monitor = NIAAttendanceMonitor(
        headless=not args.show_browser,
        config=config
    )
    
    # Get credentials
    employee_id = (args.employee_id or 
                   os.environ.get('NIA_EMPLOYEE_ID') or 
                   config.get('employee_id'))
    if not employee_id:
        employee_id = Prompt.ask("Employee ID")
        
    password = (args.password or 
                os.environ.get('NIA_PASSWORD') or 
                config.get('password'))
    if not password:
        console.print("Password (hidden):")
        password = getpass.getpass("")
    
    if args.mode:
        choice = '1' if args.mode == 'once' else '2' if args.mode == 'monitor' else '3'
    else:
        console.print("\n[bold]Options:[/bold]")
        console.print("1. One-time check")
        console.print("2. Monitor")
        console.print("3. Config")
        choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")
    
    if choice == "1":
        result = monitor.one_time_check(employee_id, password)
        if result:
            console.rule("[green]COMPLETE[/green]")
            
            analysis = result['analysis']
            console.print(f"Your records: {analysis.get('total_records', 0)}")
            console.print(f"Today: {analysis.get('today_records', 0)}")
            
            today_details = analysis.get('today_details', [])
            if today_details:
                table = Table(show_header=True, header_style="bold cyan", width=60)
                table.add_column("#", justify="right", width=4)
                table.add_column("Time", style="green", width=15)
                table.add_column("Temp", style="yellow", width=6)
                table.add_column("Status", style="magenta", width=8)
                
                action_idx = 0
                date_time_idx = 1
                temp_idx = 2
                
                for idx, row in enumerate(today_details, start=1):
                    date_time = row[date_time_idx] if len(row) > date_time_idx else "N/A"
                    temperature = row[temp_idx] if len(row) > temp_idx else "N/A"
                    status = row[action_idx] if len(row) > action_idx else "N/A"
                    
                    if "|" in str(status):
                        status = str(status).split("|")[0].strip()
                    
                    time_part = date_time.split()[1] if ' ' in date_time else date_time
                    row_style = "red" if "FAILED" in str(status).upper() else None
                    table.add_row(str(idx), time_part, temperature, status, style=row_style)
                
                console.print(table)
        else:
            console.print("[red]Check failed![/red]")
    
    elif choice == "2":
        monitor.monitor_attendance(
            employee_id,
            password,
            interval_seconds=args.interval,
            interactive=args.interactive
        )
    
    elif choice == "3":
        console.print("Config:")
        console.print_json(json.dumps(config, indent=2))

if __name__ == "__main__":
    main()