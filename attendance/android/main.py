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
import requests
from bs4 import BeautifulSoup
import sys

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class NIAAttendanceMonitor:
    def __init__(self):
        self.base_url = "https://attendance.caraga.nia.gov.ph"
        self.auth_url = "https://accounts.nia.gov.ph/Account/Login"
        self.session = requests.Session()
        
        # Set realistic mobile headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Termux) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })

    def _login_with_requests(self, employee_id, password):
        """Login using requests session (no browser needed)"""
        try:
            logging.debug("Starting login process...")
            
            # First, get the login page to capture cookies and anti-forgery token
            login_page_response = self.session.get(
                f"{self.auth_url}?ReturnUrl={self.base_url}/",
                timeout=30
            )
            login_page_response.raise_for_status()
            
            # Parse for anti-forgery token
            soup = BeautifulSoup(login_page_response.text, 'html.parser')
            token_input = soup.find('input', {'name': '__RequestVerificationToken'})
            
            if not token_input:
                logging.error("Could not find anti-forgery token on login page")
                return False
            
            request_token = token_input['value']
            logging.debug("Found anti-forgery token")
            
            # Prepare login data
            login_data = {
                'EmployeeID': employee_id,
                'Password': password,
                '__RequestVerificationToken': request_token,
                'ReturnUrl': f'{self.base_url}/'
            }
            
            # Perform login
            login_response = self.session.post(
                self.auth_url,
                data=login_data,
                allow_redirects=True,
                timeout=30
            )
            login_response.raise_for_status()
            
            # Check if login was successful by looking for redirect to attendance portal
            if self.base_url in login_response.url:
                logging.info("✓ Login successful via requests")
                return True
            else:
                # Check for error messages in response
                error_soup = BeautifulSoup(login_response.text, 'html.parser')
                error_div = error_soup.find('div', class_=re.compile('error|alert|validation'))
                if error_div:
                    logging.error(f"Login failed: {error_div.get_text(strip=True)}")
                else:
                    logging.error("Login failed - not redirected to attendance portal")
                return False
                
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error during login: {e}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error during login: {e}")
            return False

    def _get_attendance_html(self):
        """Get attendance page HTML using requests session"""
        try:
            logging.debug("Fetching attendance page...")
            response = self.session.get(
                f"{self.base_url}/Attendance",
                timeout=30
            )
            response.raise_for_status()
            
            # Check if we're still logged in
            if 'login' in response.url.lower() or 'account' in response.url.lower():
                logging.error("Session expired or not logged in")
                return None
            
            logging.debug("Successfully fetched attendance page (%s chars)", len(response.text))
            return response.text
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error fetching attendance: {e}")
            return None
        except Exception as e:
            logging.error(f"Error fetching attendance page: {e}")
            return None

    def get_attendance_data(self, employee_id, password):
        """Get attendance data using requests session"""
        # Login first
        if not self._login_with_requests(employee_id, password):
            return None
        
        # Get attendance page
        html_content = self._get_attendance_html()
        if not html_content:
            return None
        
        # Parse the data
        attendance_data = self.parse_attendance_html(html_content)
        
        if attendance_data and attendance_data['records']:
            self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])
        
        return attendance_data

    def parse_attendance_html(self, html_content):
        """Parse attendance table HTML"""
        soup = BeautifulSoup(html_content, 'html.parser')
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        
        if not table:
            # Try to find any table with attendance data
            tables = soup.find_all('table')
            if tables:
                table = tables[0]
                logging.warning("Using first table found (could not find DataTables_Table_0)")
            else:
                logging.error("No attendance table found on page")
                # Debug: save HTML for inspection
                debug_filename = f"debug_page_{datetime.now().strftime('%H%M%S')}.html"
                with open(debug_filename, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                logging.info(f"Saved page content to {debug_filename} for debugging")
                return None

        # Extract table headers
        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]

        if not headers:
            # Try to guess headers from first row
            first_row = table.find('tr')
            if first_row:
                headers = [cell.get_text(strip=True) for cell in first_row.find_all(['th', 'td'])]

        logging.debug("Found table headers: %s", headers)

        # Extract table rows
        rows = []
        table_body = table.find('tbody')
        if table_body:
            for row in table_body.find_all('tr'):
                cells = row.find_all('td')
                if not cells:
                    continue
                row_data = [cell.get_text(strip=True) for cell in cells]
                rows.append(row_data)
        else:
            # If no tbody, get all rows that aren't in thead
            for row in table.find_all('tr'):
                if not row.find_parent('thead'):
                    cells = row.find_all('td')
                    if cells:  # Only data rows (skip header rows)
                        row_data = [cell.get_text(strip=True) for cell in cells]
                        rows.append(row_data)

        logging.debug("Attendance rows parsed: %s", len(rows))

        # Try to extract metadata
        generated_time = "Unknown"
        total_records = "Unknown"
        
        # Look for generated time in tfoot or elsewhere
        tfoot = table.find('tfoot')
        if tfoot:
            tfoot_cells = tfoot.find_all('td')
            if tfoot_cells:
                generated_time = tfoot_cells[0].get_text(strip=True)

        # Look for total records in caption or info text
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
        """Save attendance data as CSV file"""
        try:
            if not rows:
                logging.warning("No data to save as CSV")
                return
            
            # Create DataFrame
            df = pd.DataFrame(rows, columns=headers)
            
            # Generate filename with current date
            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            
            # Save to CSV
            df.to_csv(filename, index=False, encoding='utf-8')
            logging.info("✓ Attendance data saved as %s", filename)
            
            logging.debug("Recent attendance records preview:")
            print(df.head(10).to_string(index=False))
            
            # Show summary
            logging.debug("Total records: %s", len(rows))
            if len(headers) > 1 and 'Date Time' in headers:
                date_col = headers.index('Date Time')
                dates = [row[date_col] for row in rows if len(row) > date_col]
                if dates:
                    logging.debug("Date range: %s to %s", min(dates), max(dates))
            
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
            
            # Find column indices safely
            date_time_idx = None
            emp_id_idx = None
            
            for i, header in enumerate(headers):
                if 'date' in header.lower() and 'time' in header.lower():
                    date_time_idx = i
                elif 'employee' in header.lower() and 'id' in header.lower():
                    emp_id_idx = i
            
            # Fallback indices if not found
            if date_time_idx is None:
                date_time_idx = 1 if len(headers) > 1 else 0
            if emp_id_idx is None:
                emp_id_idx = 4 if len(headers) > 4 else 0

            # Filter records for this employee
            my_records = []
            for record in records:
                if len(record) > emp_id_idx:
                    record_emp_id = record[emp_id_idx]
                    if record_emp_id == employee_id:
                        my_records.append(record)
                    elif not record_emp_id:  # Handle empty employee ID
                        my_records.append(record)  # Include records without ID
                else:
                    # If record doesn't have enough columns, include it
                    my_records.append(record)
            
            logging.debug("ATTENDANCE ANALYSIS FOR EMPLOYEE %s", employee_id)
            logging.debug("Total records found: %s", len(my_records))
            
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
                        # Parse date string like "11/17/2025 12:59:09 PM"
                        record_date = datetime.strptime(date_str, '%m/%d/%Y %I:%M:%S %p').date()
                        if record_date == today:
                            today_records.append(record)
                    except ValueError:
                        try:
                            # Try without seconds
                            record_date = datetime.strptime(date_str, '%m/%d/%Y %I:%M %p').date()
                            if record_date == today:
                                today_records.append(record)
                        except ValueError:
                            # Try other common formats
                            try:
                                record_date = datetime.strptime(date_str.split()[0], '%m/%d/%Y').date()
                                if record_date == today:
                                    today_records.append(record)
                            except ValueError:
                                logging.debug(f"Could not parse date: {date_str}")
            
            logging.info("Records for today (%s): %s", today, len(today_records))
            
            # Show today's records
            if today_records:
                logging.info("Today's attendance:")
                for record in today_records:
                    time_in_record = record[date_time_idx] if len(record) > date_time_idx else "N/A"
                    temp = record[2] if len(record) > 2 else "N/A"
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
            line = "||".join(str(cell) for cell in row)
            hasher.update(line.encode('utf-8', errors='replace'))
        return hasher.hexdigest()

    def monitor_attendance(self, employee_id, password, interval_seconds=300, max_checks=None):
        """Continuous monitoring using requests (lightweight)"""
        logging.info("Starting continuous monitoring (interval: %s seconds)", interval_seconds)
        checks = 0
        last_hash = None

        try:
            while True:
                attendance_data = self.get_attendance_data(employee_id, password)
                
                if attendance_data and attendance_data['records']:
                    current_hash = self._hash_records(attendance_data['records'])
                    if last_hash is None:
                        last_hash = current_hash
                        logging.info("Initial snapshot captured (%s records)", attendance_data['records_found'])
                    elif current_hash != last_hash:
                        logging.info("Detected change in attendance records!")
                        last_hash = current_hash
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
                
        except KeyboardInterrupt:
            logging.info("Monitoring interrupted by user.")
        except Exception as e:
            logging.error(f"Monitoring error: {e}")

    def one_time_check(self, employee_id, password):
        """Perform a single attendance check with analysis"""
        attendance_data = self.get_attendance_data(employee_id, password)
        if attendance_data:
            analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
            if analysis:
                self.save_attendance_record(analysis)
                return analysis
            return attendance_data
        return None


def main():
    parser = argparse.ArgumentParser(description="NIA Attendance Monitor - Termux Version")
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
        '--verbose',
        action='store_true',
        help='Enable verbose (DEBUG) logging output'
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    monitor = NIAAttendanceMonitor()
    
    # Get credentials
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
                if result['today_records'] < 2:
                    print("⚠️  REMINDER: Make sure you have both Time In and Time Out records")
                else:
                    print("✓ Good attendance records for today")
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