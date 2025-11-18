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

console = Console()

# Set up logging with Rich handler for nicer console output
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)]
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
        """Return enriched text for action cells containing status buttons"""
        link = cell.find('a')
        if not link:
            return None
        
        classes = link.get('class', [])
        label = link.get_text(strip=True) or "Action"
        status = None
        if any('btn-danger' in cls for cls in classes):
            status = 'FAILED'
        elif any('btn-success' in cls for cls in classes):
            status = 'SUCCESS'
        elif any('btn-warning' in cls for cls in classes):
            status = 'WARNING'
        
        href = link.get('href')
        details = []
        if status:
            details.append(status)
        details.append(label)
        if href:
            if href.startswith('/'):
                href = f"{self.base_url}{href}"
            details.append(f"Details: {href}")
        
        return " | ".join(details)
    
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
                logging.error(f"Login failed: {e}")
                # Save the page HTML for debugging
                try:
                    with open("login_debug.html", "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    logging.info("Saved login page HTML to login_debug.html for inspection")
                except:
                    pass
                if driver:
                    driver.quit()
                return None, None

        try:
            # Debug: Check current URL before loading attendance
            logging.debug(f"Current URL before loading attendance: {driver.current_url}")
            
            html_content = self._load_attendance_html(driver)
            attendance_data = self.parse_attendance_html(html_content)

            if attendance_data and attendance_data['records'] and not reuse_driver:
                self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])

            return attendance_data, driver
            
        except TimeoutException as e:
            logging.error(f"Selenium timed out while loading the page: {e}")
            # Save debug HTML
            try:
                with open("timeout_debug.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                logging.info("Saved timeout page HTML to timeout_debug.html")
            except:
                pass
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
    # Replace the save_as_csv method with this:
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
            # From your table structure: Actions, Date Time, Temperature, Employee Name, Employee ID, Machine Name
            action_idx = 0      # Action / status column
            date_time_idx = 1   # Date Time is the second column (index 1)
            emp_id_idx = 4      # Employee ID is the fifth column (index 4)
            temp_idx = 2        # Temperature is the third column (index 2)
            
            # Filter records for this employee and include FAILED entries even if name/ID is missing
            my_records = []
            failed_records = []
            for record in records:
                if len(record) <= max(action_idx, emp_id_idx):
                    continue
                action_val = record[action_idx] if len(record) > action_idx else ""
                emp_val = record[emp_id_idx] if len(record) > emp_id_idx else ""
                is_for_employee = emp_val == employee_id
                is_failed = "FAILED" in str(action_val).upper()
                
                if is_for_employee or is_failed:
                    my_records.append(record)
                    if is_failed:
                        failed_records.append(record)
            
            logging.info("ATTENDANCE ANALYSIS FOR EMPLOYEE %s", employee_id)
            logging.info("Total records found: %s", len(my_records))
            if failed_records:
                logging.warning("Found %s FAILED record(s) in the data", len(failed_records))
            
            if not my_records:
                logging.warning(f"No matching records found for Employee ID: {employee_id}")
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
                        record_date = datetime.strptime(date_str_clean, '%m/%d/%Y %I:%M:%S %p').date()
                        if record_date == today:
                            today_records.append(record)
                    except ValueError as e:
                        logging.debug(f"Date parsing error for '{date_str}': {e}")
                        # Try alternative format without seconds
                        try:
                            date_str_clean = ' '.join(date_str.split())
                            record_date = datetime.strptime(date_str_clean, '%m/%d/%Y %I:%M %p').date()
                            if record_date == today:
                                today_records.append(record)
                        except ValueError:
                            logging.warning(f"Could not parse date: {date_str}")
            
            logging.info("Records for today (%s): %s", today, len(today_records))
            
            # Show today's records
            if today_records:
                logging.info("Today's attendance:")
                for record in today_records:
                    time_in_record = record[date_time_idx] if len(record) > date_time_idx else "N/A"
                    temp = record[temp_idx] if len(record) > temp_idx else "N/A"
                    logging.info(f"  - {time_in_record} (Temp: {temp}°C)")
                
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
                'total_records': len(my_records),
                'today_records': len(today_records),
                'today_details': today_records,
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

    def monitor_attendance(self, employee_id, password, interval_seconds=300, max_checks=None):
            logging.info("Starting continuous monitoring (interval: %s seconds)", interval_seconds)
            checks = 0
            driver = None
            last_hash = None

            try:
                attendance_data, driver = self.get_attendance_data(
                    employee_id,
                    password,
                    driver=None,
                    reuse_driver=True
                )

                if driver is None:
                    logging.error("Failed to initialize Selenium driver. Cannot start monitoring.")
                    return

                while True:
                    if attendance_data:
                        current_hash = self._hash_records(attendance_data['records'])
                        if last_hash is None:
                            last_hash = current_hash
                            logging.info("Initial snapshot captured (%s records)", attendance_data['records_found'])
                        elif current_hash != last_hash:
                            logging.info("Detected change in attendance records!")
                            last_hash = current_hash
                            if attendance_data['records']:
                                self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])
                            analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
                            if analysis:
                                self.save_attendance_record(analysis)
                        else:
                            logging.debug("No changes detected since last check.")
                    else:
                        logging.warning("No attendance data retrieved this cycle.")

                    checks += 1
                    if max_checks and checks >= max_checks:
                        logging.info("Reached max checks limit (%s). Stopping monitor.", max_checks)
                        break

                    logging.debug("Sleeping for %s seconds before next check...", interval_seconds)
                    time.sleep(interval_seconds)

                    attendance_data, driver = self.get_attendance_data(
                        employee_id,
                        password,
                        driver=driver,
                        reuse_driver=True
                    )
            except KeyboardInterrupt:
                logging.info("Monitoring interrupted by user.")
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass
        
    def one_time_check(self, employee_id, password):
        """Perform a single attendance check with analysis using Selenium"""
        attendance_data, _ = self.get_attendance_data(employee_id, password)
        if attendance_data:
            analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
            if analysis:
                self.save_attendance_record(analysis)
                return analysis
            return attendance_data
        return None


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
    parser.add_argument(
        '--employee-id',
        help='Employee ID (can also use NIA_EMPLOYEE_ID env var)'
    )
    parser.add_argument(
        '--password',
        help='Password (can also use NIA_PASSWORD env var; use with caution)'
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    monitor = NIAAttendanceMonitor(headless=not args.show_browser, driver_path=args.driver_path)
    
    # Get credentials securely
    employee_id = args.employee_id or os.environ.get('NIA_EMPLOYEE_ID')
    if not employee_id:
        employee_id = Prompt.ask("[bold]Enter your Employee ID[/]")
    password = args.password or os.environ.get('NIA_PASSWORD')
    if not password:
        console.print("[bold]Enter your Password[/] (input hidden)")
        password = getpass.getpass("")
    
    if args.mode:
        choice = '1' if args.mode == 'once' else '2'
    else:
        console.print("\n[bold]Choose operation:[/bold]")
        console.print("1. One-time attendance check with analysis")
        console.print("2. Start continuous monitoring")
        choice = Prompt.ask("Enter choice", choices=["1", "2"], default="1")
    
    if choice == "1":
        result = monitor.one_time_check(employee_id, password)
        if result:
            console.rule("[bold green]CHECK COMPLETED SUCCESSFULLY[/bold green]")
            if 'today_records' in result:
                console.print(f"[bold]Today's records:[/] {result['today_records']}")
            if 'note' in result:
                console.print(f"[yellow]Note:[/] {result['note']}")

            today_details = result.get('today_details')
            if today_details:
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("Entry #", justify="right")
                table.add_column("Date & Time", overflow="fold")
                table.add_column("Temperature")
                table.add_column("Action / Status", overflow="fold")
                for idx, row in enumerate(today_details, start=1):
                    date_time = row[1] if len(row) > 1 else "N/A"
                    temperature = row[2] if len(row) > 2 else "N/A"
                    action = row[0] if len(row) > 0 else ""
                    row_style = "red" if "FAILED" in str(action).upper() else None
                    table.add_row(str(idx), date_time, temperature, action, style=row_style)
                console.print(table)
        else:
            console.print("[bold red]One-time check failed![/bold red]")
    
    elif choice == "2":
        monitor.monitor_attendance(
            employee_id,
            password,
            interval_seconds=args.interval,
            max_checks=args.max_checks
        )
    
    else:
        console.print("[bold red]Invalid choice[/bold red]")

if __name__ == "__main__":
    main()