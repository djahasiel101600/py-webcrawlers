#!/usr/bin/env python3
"""
Ubuntu-in-Termux (proot) friendly NIA Attendance Crawler with Seledroid

This version uses Seledroid (https://pypi.org/project/seledroid/) for 
Android automation directly from Python, which is simpler than Selendroid
and doesn't require Java server setup.

Usage examples:
  python3 attendance-crawler-seledroid.py --mode once
  python3 attendance-crawler-seledroid.py --mode monitor --interval 300

Requirements:
- seledroid package: pip install seledroid
- ADB enabled on Android device
- Termux with proper permissions
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

# Try to import seledroid
try:
    from seledroid import Seledroid
    SELEDROID_AVAILABLE = True
except ImportError:
    SELEDROID_AVAILABLE = False
    logging.warning("seledroid package not available. Please install with: pip install seledroid")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class SeledroidNIA:
    def __init__(self, headless=False, device_id=None, browser_package="com.android.chrome"):
        self.base_url = "https://attendance.caraga.nia.gov.ph"
        self.auth_url = "https://accounts.nia.gov.ph/Account/Login"
        self.headless = headless
        self.device_id = device_id
        self.browser_package = browser_package
        self.driver = None

    def _create_driver(self):
        """Create Seledroid driver instance"""
        if not SELEDROID_AVAILABLE:
            raise RuntimeError("seledroid package not available. Install with: pip install seledroid")
        
        try:
            # Initialize Seledroid with device ID and browser package
            driver = Seledroid(
                device_id=self.device_id,
                app_package=self.browser_package,
                app_activity="com.google.android.apps.chrome.Main"
            )
            
            logging.info("✓ Seledroid driver created successfully")
            return driver
            
        except Exception as e:
            logging.error(f"Failed to create Seledroid driver: {e}")
            raise RuntimeError(f"Seledroid driver creation failed: {e}")

    def _login_with_seledroid(self, driver, employee_id, password):
        """Login using Seledroid automation"""
        logging.info('Opening login page with Seledroid...')
        
        # Navigate to login page
        driver.get(f"{self.auth_url}?ReturnUrl={self.base_url}/")
        time.sleep(5)  # Wait for page load
        
        # Find and fill employee ID field
        employee_input = driver.find_element_by_name('EmployeeID')
        if employee_input:
            employee_input.clear()
            employee_input.send_keys(employee_id)
            logging.debug('Filled employee ID')
        else:
            logging.error('Could not find EmployeeID field')
            raise Exception("EmployeeID field not found")
        
        # Find and fill password field
        password_input = driver.find_element_by_name('Password')
        if password_input:
            password_input.clear()
            password_input.send_keys(password)
            logging.debug('Filled password')
        else:
            logging.error('Could not find Password field')
            raise Exception("Password field not found")
        
        # Submit form
        try:
            submit_btn = driver.find_element_by_css_selector("button[type='submit']")
            submit_btn.click()
            logging.debug('Clicked submit button')
        except Exception as e:
            logging.warning(f'Could not find submit button, trying Enter key: {e}')
            driver.press_keycode(66)  # Enter key
        
        # Wait for redirect and page load
        time.sleep(8)
        
        # Check if we're on the attendance page
        current_url = driver.current_url
        if self.base_url in current_url:
            logging.info('✓ Login successful via Seledroid')
        else:
            logging.warning('May not have redirected to attendance page correctly')

    def _load_attendance_html(self, driver):
        """Load attendance page and extract HTML"""
        logging.debug('Navigating to attendance page...')
        
        # Navigate directly to attendance page
        driver.get(f"{self.base_url}/Attendance")
        time.sleep(8)  # Wait for page and JavaScript to load
        
        # Get page source
        html_content = driver.page_source
        logging.debug('Captured attendance page HTML (%s chars)', len(html_content))
        
        return html_content

    def get_attendance_data(self, employee_id, password, reuse_driver=False):
        """Main method to get attendance data using Seledroid"""
        if not self.driver or not reuse_driver:
            try:
                self.driver = self._create_driver()
            except Exception as e:
                logging.error('Unable to start Seledroid driver: %s', e)
                return None
        
        try:
            self._login_with_seledroid(self.driver, employee_id, password)
            html_content = self._load_attendance_html(self.driver)
            attendance_data = self.parse_attendance_html(html_content)
            
            if attendance_data and attendance_data['records'] and not reuse_driver:
                self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])
            
            return attendance_data
            
        except TimeoutError as e:
            logging.error('Seledroid timed out while loading the page: %s', e)
            return None
        except Exception as e:
            logging.error('Error fetching attendance via Seledroid: %s', e)
            return None
        finally:
            if not reuse_driver and self.driver:
                self.cleanup()

    def parse_attendance_html(self, html_content):
        """Parse attendance HTML and extract data"""
        soup = BeautifulSoup(html_content, 'html.parser')
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        if not table:
            logging.error('No attendance table found on page')
            return None

        # Extract headers
        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all('th')]

        # Extract rows
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

        # Extract metadata
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
        """Save attendance data as CSV"""
        try:
            if not rows:
                logging.warning('No data to save as CSV')
                return
            df = pd.DataFrame(rows, columns=headers)
            filename = f"attendance_seledroid_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            df.to_csv(filename, index=False, encoding='utf-8')
            logging.info('✓ Attendance data saved as %s', filename)
            print(df.head(10).to_string(index=False))
        except Exception as e:
            logging.error('Error saving CSV: %s', e)

    def analyze_attendance_patterns(self, attendance_data, employee_id):
        """Analyze attendance patterns for specific employee"""
        try:
            if not attendance_data or 'records' not in attendance_data:
                logging.warning('No attendance data to analyze')
                return None
            
            headers = attendance_data['table_headers']
            records = attendance_data['records']
            
            if not records:
                logging.warning('No records found')
                return None
            
            # Find indices
            date_time_idx = headers.index('Date Time') if 'Date Time' in headers else 1
            emp_id_idx = headers.index('Employee ID') if 'Employee ID' in headers else 4
            
            # Filter records for this employee
            my_records = [
                record for record in records 
                if len(record) > emp_id_idx and record[emp_id_idx] == employee_id
            ]
            
            logging.debug('ATTENDANCE ANALYSIS FOR EMPLOYEE %s', employee_id)
            logging.debug('Total records found: %s', len(my_records))
            
            if not my_records:
                logging.warning(f'No matching records found for Employee ID: {employee_id}')
                return None
            
            # Analyze today's records
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

    def cleanup(self):
        """Clean up resources"""
        if self.driver:
            try:
                self.driver.quit()
                self.driver = None
                logging.info('✓ Seledroid driver cleaned up')
            except Exception as e:
                logging.warning(f"Error quitting driver: {e}")


def check_seledroid_requirements():
    """Check if required tools are available"""
    requirements_met = True
    
    if not SELEDROID_AVAILABLE:
        logging.error('✗ seledroid package not installed')
        logging.info('Please install with: pip install seledroid')
        requirements_met = False
    else:
        logging.info('✓ seledroid package is available')
    
    # Check if ADB is available
    try:
        adb_check = os.popen('adb version').read()
        if 'Android Debug Bridge' in adb_check:
            logging.info('✓ ADB is available')
        else:
            logging.warning('⚠ ADB may not be properly installed')
    except Exception:
        logging.warning('⚠ ADB check failed')
    
    return requirements_met


def list_android_devices():
    """List available Android devices"""
    try:
        devices_output = os.popen('adb devices').read()
        lines = devices_output.strip().split('\n')
        if len(lines) <= 1:
            logging.warning('No Android devices found')
            return []
        
        devices = []
        for line in lines[1:]:
            if line.strip() and 'device' in line:
                device_id = line.split('\t')[0]
                devices.append(device_id)
        
        logging.info(f'Found {len(devices)} Android device(s): {devices}')
        return devices
    except Exception as e:
        logging.warning(f'Error listing devices: {e}')
        return []


def main():
    parser = argparse.ArgumentParser(description='NIA Attendance Crawler with Seledroid')
    parser.add_argument('--mode', choices=['once', 'monitor'], default='once',
                       help='Run once or monitor continuously (default: once)')
    parser.add_argument('--interval', type=int, default=300,
                       help='Interval in seconds for monitor mode (default: 300)')
    parser.add_argument('--device-id', type=str,
                       help='Android device ID (optional, will use first available if not specified)')
    parser.add_argument('--browser-package', type=str, default="com.android.chrome",
                       help='Browser package name (default: com.android.chrome)')
    parser.add_argument('--employee-id', type=str,
                       help='Employee ID (will prompt if not provided)')
    parser.add_argument('--password', type=str,
                       help='Password (will prompt if not provided)')
    
    args = parser.parse_args()

    # Check requirements
    if not check_seledroid_requirements():
        logging.error("System requirements not met. Please install missing components.")
        return

    # List available devices
    available_devices = list_android_devices()
    if not available_devices:
        logging.error("No Android devices found. Please connect a device and enable ADB debugging.")
        return

    # Use specified device or first available
    device_id = args.device_id
    if not device_id and available_devices:
        device_id = available_devices[0]
        logging.info(f"Using device: {device_id}")

    # Get credentials
    employee_id = args.employee_id or input("Enter Employee ID: ")
    password = args.password or getpass.getpass("Enter Password: ")

    # Initialize Seledroid crawler
    crawler = SeledroidNIA(
        device_id=device_id,
        browser_package=args.browser_package
    )

    try:
        if args.mode == 'once':
            logging.info("Running in single-shot mode with Seledroid...")
            attendance_data = crawler.get_attendance_data(employee_id, password)
            if attendance_data:
                analysis = crawler.analyze_attendance_patterns(attendance_data, employee_id)
                logging.info("Single run completed successfully")
            else:
                logging.error("Failed to fetch attendance data")

        elif args.mode == 'monitor':
            logging.info(f"Starting monitor mode with {args.interval} second intervals...")
            run_count = 0
            
            try:
                while True:
                    run_count += 1
                    logging.info(f"Monitor run #{run_count} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    attendance_data = crawler.get_attendance_data(
                        employee_id, password, reuse_driver=True
                    )
                    
                    if attendance_data:
                        analysis = crawler.analyze_attendance_patterns(attendance_data, employee_id)
                    else:
                        logging.error("Failed to fetch attendance data in monitor run #%d", run_count)
                        # Reset on failure
                        crawler.cleanup()
                    
                    logging.info(f"Waiting {args.interval} seconds until next run...")
                    time.sleep(args.interval)
                    
            except KeyboardInterrupt:
                logging.info("Monitor mode interrupted by user")
                
    finally:
        crawler.cleanup()


if __name__ == '__main__':
    main()