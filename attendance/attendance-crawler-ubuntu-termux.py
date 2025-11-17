#!/usr/bin/env python3
"""
Ubuntu-in-Termux (proot) friendly NIA Attendance Crawler with Selendroid

This version uses Selendroid for Android-native automation, which can be more
reliable in Termux environments and provides better mobile browser compatibility.

Usage examples:
  python3 attendance-crawler-selendroid.py --mode once
  python3 attendance-crawler-selendroid.py --mode monitor --interval 300

Requirements:
- Selendroid standalone server JAR file
- Android SDK tools (for appium or selendroid)
- Java Runtime Environment
"""

import argparse
import hashlib
import json
import logging
import os
import re
import time
import shutil
import subprocess
import signal
import requests
from datetime import datetime
from threading import Thread

import getpass
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class SelendroidNIA:
    def __init__(self, headless=False, selendroid_path=None, selendroid_port=8080):
        self.base_url = "https://attendance.caraga.nia.gov.ph"
        self.auth_url = "https://accounts.nia.gov.ph/Account/Login"
        self.headless = headless
        self.selendroid_path = selendroid_path
        self.selendroid_port = selendroid_port
        self.selendroid_process = None
        self.driver = None

    def _find_selendroid(self):
        """Find Selendroid standalone server JAR"""
        if self.selendroid_path and os.path.exists(self.selendroid_path):
            return self.selendroid_path
        
        candidates = [
            'selendroid-standalone.jar',
            '/usr/share/java/selendroid-standalone.jar',
            '/opt/selendroid/selendroid-standalone.jar',
            './selendroid-standalone.jar'
        ]
        
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        
        # Try to find in current directory
        for file in os.listdir('.'):
            if file.startswith('selendroid-standalone') and file.endswith('.jar'):
                return file
        
        return None

    def _start_selendroid_server(self):
        """Start Selendroid standalone server"""
        selendroid_jar = self._find_selendroid()
        if not selendroid_jar:
            raise WebDriverException("Selendroid standalone server JAR not found")
        
        logging.info(f"Starting Selendroid server on port {self.selendroid_port}")
        
        cmd = [
            'java', '-jar', selendroid_jar,
            '-port', str(self.selendroid_port)
        ]
        
        self.selendroid_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid
        )
        
        # Wait for server to start
        time.sleep(10)
        
        # Check if server is running
        try:
            response = requests.get(f"http://localhost:{self.selendroid_port}/wd/hub/status", timeout=10)
            if response.status_code == 200:
                logging.info("✓ Selendroid server started successfully")
                return True
        except Exception as e:
            logging.error(f"Selendroid server failed to start: {e}")
            self._stop_selendroid_server()
            return False

    def _stop_selendroid_server(self):
        """Stop Selendroid server"""
        if self.selendroid_process:
            try:
                os.killpg(os.getpgid(self.selendroid_process.pid), signal.SIGTERM)
                self.selendroid_process.wait(timeout=10)
                logging.info("✓ Selendroid server stopped")
            except Exception as e:
                logging.warning(f"Error stopping Selendroid server: {e}")
                try:
                    os.killpg(os.getpgid(self.selendroid_process.pid), signal.SIGKILL)
                except:
                    pass
            finally:
                self.selendroid_process = None

    def _create_driver(self):
        """Create Selendroid WebDriver instance"""
        if not self.selendroid_process:
            if not self._start_selendroid_server():
                raise WebDriverException("Failed to start Selendroid server")
        
        try:
            # Selendroid desired capabilities for Android browser
            desired_capabilities = {
                'browserName': 'android',
                'platformName': 'Android',
                'deviceName': 'android',
                'automaticWait': True,
                'automaticScreenshots': False,
                'seleniumProtocol': 'WebDriver'
            }
            
            self.driver = webdriver.Remote(
                command_executor=f"http://localhost:{self.selendroid_port}/wd/hub",
                desired_capabilities=desired_capabilities
            )
            
            self.driver.set_page_load_timeout(60)
            self.driver.implicitly_wait(10)
            
            logging.info("✓ Selendroid WebDriver created successfully")
            return self.driver
            
        except Exception as e:
            logging.error(f"Failed to create Selendroid driver: {e}")
            self._stop_selendroid_server()
            raise WebDriverException(f"Selendroid driver creation failed: {e}")

    def _login_with_selendroid(self, driver, employee_id, password):
        """Login using Selendroid automation"""
        logging.info('Opening login page with Selendroid...')
        driver.get(f"{self.auth_url}?ReturnUrl={self.base_url}/")
        
        wait = WebDriverWait(driver, 30)
        
        # Wait for and fill employee ID
        employee_input = wait.until(EC.presence_of_element_located((By.NAME, 'EmployeeID')))
        employee_input.clear()
        employee_input.send_keys(employee_id)
        
        # Wait for and fill password
        password_input = wait.until(EC.presence_of_element_located((By.NAME, 'Password')))
        password_input.clear()
        password_input.send_keys(password)
        
        # Submit form
        try:
            submit_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            submit_btn.click()
        except Exception:
            password_input.submit()
        
        # Wait for redirect to attendance page
        wait.until(EC.url_contains(self.base_url))
        logging.info('✓ Login successful via Selendroid')

    def _load_attendance_html(self, driver):
        """Load attendance page and extract HTML"""
        logging.debug('Navigating to attendance page...')
        driver.get(f"{self.base_url}/Attendance")
        
        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.ID, 'DataTables_Table_0')))
        
        # Wait for table rows to load
        try:
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '#DataTables_Table_0 tbody tr')) > 0)
        except TimeoutException:
            logging.warning('Attendance table loaded but contains no rows (yet). Proceeding with current content.')
        
        html_content = driver.page_source
        logging.debug('Captured attendance page HTML (%s chars)', len(html_content))
        return html_content

    def get_attendance_data(self, employee_id, password, reuse_driver=False):
        """Main method to get attendance data using Selendroid"""
        if not self.driver:
            try:
                self._create_driver()
            except WebDriverException as e:
                logging.error('Unable to start Selendroid driver: %s', e)
                return None
        
        try:
            self._login_with_selendroid(self.driver, employee_id, password)
            html_content = self._load_attendance_html(self.driver)
            attendance_data = self.parse_attendance_html(html_content)
            
            if attendance_data and attendance_data['records'] and not reuse_driver:
                self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])
            
            return attendance_data
            
        except TimeoutException as e:
            logging.error('Selendroid timed out while loading the page: %s', e)
            return None
        except Exception as e:
            logging.error('Error fetching attendance via Selendroid: %s', e)
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
            filename = f"attendance_selendroid_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
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
            except Exception as e:
                logging.warning(f"Error quitting driver: {e}")
        
        self._stop_selendroid_server()


def check_selendroid_requirements():
    """Check if required tools are available"""
    requirements_met = True
    
    # Check Java
    try:
        subprocess.run(['java', '-version'], capture_output=True, check=True)
        logging.info('✓ Java is available')
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.error('✗ Java is not installed or not in PATH')
        requirements_met = False
    
    # Check if Selendroid JAR exists
    selendroid_candidates = [
        'selendroid-standalone.jar',
        '/usr/share/java/selendroid-standalone.jar',
        '/opt/selendroid/selendroid-standalone.jar'
    ]
    
    selendroid_found = any(os.path.exists(candidate) for candidate in selendroid_candidates)
    
    if selendroid_found:
        logging.info('✓ Selendroid JAR found')
    else:
        logging.warning('⚠ Selendroid JAR not found in common locations')
        logging.info('Please download selendroid-standalone.jar and place it in the current directory')
        requirements_met = False
    
    return requirements_met


def main():
    parser = argparse.ArgumentParser(description='NIA Attendance Crawler with Selendroid')
    parser.add_argument('--mode', choices=['once', 'monitor'], default='once',
                       help='Run once or monitor continuously (default: once)')
    parser.add_argument('--interval', type=int, default=300,
                       help='Interval in seconds for monitor mode (default: 300)')
    parser.add_argument('--selendroid-path', type=str,
                       help='Path to selendroid-standalone.jar (optional)')
    parser.add_argument('--port', type=int, default=8080,
                       help='Port for Selendroid server (default: 8080)')
    parser.add_argument('--employee-id', type=str,
                       help='Employee ID (will prompt if not provided)')
    parser.add_argument('--password', type=str,
                       help='Password (will prompt if not provided)')
    
    args = parser.parse_args()

    # Check requirements
    if not check_selendroid_requirements():
        logging.error("System requirements not met. Please install missing components.")
        return

    # Get credentials
    employee_id = args.employee_id or input("Enter Employee ID: ")
    password = args.password or getpass.getpass("Enter Password: ")

    # Initialize Selendroid crawler
    crawler = SelendroidNIA(
        selendroid_path=args.selendroid_path,
        selendroid_port=args.port
    )

    try:
        if args.mode == 'once':
            logging.info("Running in single-shot mode with Selendroid...")
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