#!/usr/bin/env python3
"""
Enhanced Termux-friendly NIA Attendance Crawler

This script uses multiple approaches to fetch attendance data:
1. Direct HTML table parsing
2. AJAX endpoint discovery and JSON parsing  
3. Selenium fallback (if available)

Usage examples:
  python3 attendance-crawler-termux.py --mode once
  python3 attendance-crawler-termux.py --mode monitor --interval 300
  python3 attendance-crawler-termux.py --verbose
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
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class EnhancedNIAcrawler:
    def __init__(self, base_url=None, auth_url=None, user_agent=None):
        self.base_url = base_url or "https://attendance.caraga.nia.gov.ph"
        self.auth_url = auth_url or "https://accounts.nia.gov.ph/Account/Login"
        self.user_agent = user_agent or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
        )
        self.session = None

    def _get_headers(self, ajax=False):
        headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        if ajax:
            headers.update({
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
            })
        return headers

    def init_session(self):
        """Initialize a new session with proper headers"""
        self.session = requests.Session()
        self.session.headers.update(self._get_headers())

    def login(self, employee_id: str, password: str) -> bool:
        """Attempt to login using requests. Returns True if login looks successful."""
        if not self.session:
            self.init_session()

        logging.info("Attempting to login...")
        
        # First, get the login page to extract form data
        try:
            resp = self.session.get(self.auth_url, timeout=30)
            if resp.status_code != 200:
                logging.error(f"Failed to fetch login page (status {resp.status_code})")
                return False
        except Exception as e:
            logging.error(f"Network error fetching login page: {e}")
            return False

        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Find login form
        form = soup.find('form')
        if not form:
            logging.error("No login form found on page")
            return False

        # Build payload with all form fields
        payload = {}
        for input_field in form.find_all('input'):
            name = input_field.get('name')
            value = input_field.get('value', '')
            if name:
                payload[name] = value

        # Update with credentials
        payload.update({
            'EmployeeID': employee_id,
            'Password': password
        })

        # Determine form action URL
        form_action = form.get('action', '')
        if form_action:
            post_url = urljoin(self.auth_url, form_action)
        else:
            post_url = self.auth_url

        logging.debug(f"Submitting login to: {post_url}")

        # Submit login form
        try:
            login_resp = self.session.post(
                post_url, 
                data=payload, 
                timeout=30, 
                allow_redirects=True
            )
            
            # Check if login was successful by testing access to attendance page
            test_resp = self.session.get(f"{self.base_url}/Attendance", timeout=30)
            
            if test_resp.status_code == 200:
                # Check for various success indicators
                success_indicators = [
                    'DataTables_Table_0' in test_resp.text,
                    'attendance' in test_resp.text.lower(),
                    'logout' in test_resp.text.lower(),
                    employee_id in test_resp.text
                ]
                
                if any(success_indicators):
                    logging.info("✓ Login successful")
                    return True
                else:
                    logging.warning("Login may have succeeded but attendance table not found")
                    return True  # Continue anyway - table might be loaded via AJAX
            else:
                logging.error(f"Failed to access attendance page after login (status {test_resp.status_code})")
                return False
                
        except Exception as e:
            logging.error(f"Login request failed: {e}")
            return False

    def fetch_attendance_data(self):
        """Main method to fetch attendance data using multiple approaches"""
        if not self.session:
            logging.error("No active session. Please login first.")
            return None

        logging.info("Fetching attendance data...")
        
        # Approach 1: Try direct AJAX endpoint discovery first (most reliable for JS sites)
        logging.debug("Attempting AJAX endpoint discovery...")
        ajax_data = self.discover_ajax_endpoint()
        if ajax_data:
            logging.info("✓ Successfully fetched data via AJAX endpoint")
            return ajax_data

        # Approach 2: Try parsing HTML table
        logging.debug("Attempting HTML table parsing...")
        html_data = self.fetch_via_html()
        if html_data:
            logging.info("✓ Successfully parsed HTML table")
            return html_data

        # Approach 3: Try enhanced script analysis
        logging.debug("Attempting enhanced script analysis...")
        script_data = self.analyze_page_scripts()
        if script_data:
            logging.info("✓ Successfully extracted data from scripts")
            return script_data

        logging.error("All data fetching methods failed")
        return None

    def discover_ajax_endpoint(self):
        """Discover and call AJAX endpoints that might contain attendance data"""
        try:
            # First, get the attendance page HTML to analyze
            resp = self.session.get(f"{self.base_url}/Attendance", timeout=30)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Common AJAX endpoint patterns for attendance systems
            endpoint_patterns = [
                r"/api/attendance",
                r"/Attendance/Get",
                r"/Data/GetAttendance",
                r"/Report/Attendance",
                r"\.json",
                r"\.ashx.*attendance",
                r"\.aspx.*attendance"
            ]

            # Look in script tags for AJAX calls
            for script in soup.find_all('script'):
                if script.string:
                    script_content = script.string
                    
                    # Look for fetch, $.ajax, $.getJSON calls
                    ajax_patterns = [
                        r"fetch\(['\"]([^'\"]+)['\"]",
                        r"\$\.ajax\([^)]*url\s*:\s*['\"]([^'\"]+)['\"]",
                        r"\$\.getJSON\(['\"]([^'\"]+)['\"]",
                        r"\$\.get\(['\"]([^'\"]+)['\"]",
                        r"url:\s*['\"]([^'\"]+)['\"]",
                    ]
                    
                    for pattern in ajax_patterns:
                        matches = re.findall(pattern, script_content, re.IGNORECASE)
                        for endpoint in matches:
                            # Filter for relevant endpoints
                            if any(ep in endpoint.lower() for ep in ['attendance', 'data', 'report', 'getdata']):
                                logging.debug(f"Found potential AJAX endpoint: {endpoint}")
                                data = self.call_ajax_endpoint(endpoint)
                                if data:
                                    return data

            # Also check for data attributes in HTML
            for div in soup.find_all(['div', 'table'], attrs={'data-url': True}):
                endpoint = div.get('data-url')
                if endpoint and any(ep in endpoint.lower() for ep in ['attendance', 'data']):
                    logging.debug(f"Found data-url endpoint: {endpoint}")
                    data = self.call_ajax_endpoint(endpoint)
                    if data:
                        return data

        except Exception as e:
            logging.debug(f"AJAX discovery error: {e}")

        return None

    def call_ajax_endpoint(self, endpoint):
        """Call a discovered AJAX endpoint and parse response"""
        try:
            # Normalize endpoint URL
            if endpoint.startswith('/'):
                url = urljoin(self.base_url, endpoint)
            elif not endpoint.startswith('http'):
                url = urljoin(self.base_url, '/' + endpoint)
            else:
                url = endpoint

            logging.debug(f"Calling AJAX endpoint: {url}")

            # Common parameters for DataTables and similar libraries
            params = {
                'draw': '1',
                'start': '0',
                'length': '10000',  # Get all records
                '_': str(int(time.time() * 1000))  # Cache buster
            }

            headers = self._get_headers(ajax=True)

            # Try both GET and POST
            for method in [self.session.get, self.session.post]:
                try:
                    if method == self.session.get:
                        response = method(url, params=params, headers=headers, timeout=30)
                    else:
                        response = method(url, data=params, headers=headers, timeout=30)

                    if response.status_code == 200:
                        content_type = response.headers.get('content-type', '')
                        if 'application/json' in content_type or response.text.strip().startswith(('{', '[')):
                            return self.parse_json_response(response.json())
                            
                except Exception as e:
                    logging.debug(f"Endpoint call failed with {method.__name__}: {e}")
                    continue

        except Exception as e:
            logging.debug(f"Error calling AJAX endpoint: {e}")

        return None

    def parse_json_response(self, json_data):
        """Parse JSON response from AJAX endpoint"""
        try:
            records = []
            headers = []

            # Handle different JSON response formats
            if isinstance(json_data, dict):
                # Format 1: { "data": [...] }
                if 'data' in json_data and isinstance(json_data['data'], list):
                    raw_data = json_data['data']
                # Format 2: { "aaData": [...] } (DataTables legacy)
                elif 'aaData' in json_data and isinstance(json_data['aaData'], list):
                    raw_data = json_data['aaData']
                # Format 3: { "rows": [...] }
                elif 'rows' in json_data and isinstance(json_data['rows'], list):
                    raw_data = json_data['rows']
                else:
                    # Try to find first list in dictionary values
                    raw_data = None
                    for value in json_data.values():
                        if isinstance(value, list):
                            raw_data = value
                            break
                    if raw_data is None:
                        return None
            elif isinstance(json_data, list):
                raw_data = json_data
            else:
                return None

            if not raw_data:
                return None

            # Extract headers and rows
            if isinstance(raw_data[0], dict):
                headers = list(raw_data[0].keys())
                for item in raw_data:
                    row = [str(item.get(header, '')) for header in headers]
                    records.append(row)
            elif isinstance(raw_data[0], list):
                # Array of arrays - generate generic headers
                headers = [f"Column_{i+1}" for i in range(len(raw_data[0]))]
                records = [[str(cell) for cell in row] for row in raw_data]
            else:
                return None

            return {
                'timestamp': datetime.now().isoformat(),
                'date': datetime.now().strftime('%Y-%m-%d'),
                'table_headers': headers,
                'records': records,
                'records_found': len(records),
                'total_records_caption': str(len(records)),
                'report_generated_time': 'Via AJAX',
                'source': 'ajax'
            }

        except Exception as e:
            logging.debug(f"JSON parsing error: {e}")
            return None

    def fetch_via_html(self):
        """Traditional HTML table parsing"""
        try:
            resp = self.session.get(f"{self.base_url}/Attendance", timeout=30)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Look for tables with common attendance table IDs/classes
            table_selectors = [
                'table#DataTables_Table_0',
                'table.dataTable',
                'table.attendance-table',
                'table#attendanceTable',
                'table'
            ]

            table = None
            for selector in table_selectors:
                table = soup.select_one(selector)
                if table:
                    break

            if not table:
                return None

            # Extract headers
            headers = []
            header_row = table.find('thead')
            if header_row:
                header_cells = header_row.find_all(['th', 'td'])
                headers = [cell.get_text(strip=True) for cell in header_cells if cell.get_text(strip=True)]

            # Extract rows
            records = []
            table_body = table.find('tbody')
            if table_body:
                for row in table_body.find_all('tr'):
                    cells = row.find_all(['td', 'th'])
                    if cells:
                        row_data = [cell.get_text(strip=True) for cell in cells]
                        records.append(row_data)

            if records:
                return {
                    'timestamp': datetime.now().isoformat(),
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'table_headers': headers,
                    'records': records,
                    'records_found': len(records),
                    'total_records_caption': str(len(records)),
                    'report_generated_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'source': 'html'
                }

        except Exception as e:
            logging.debug(f"HTML parsing error: {e}")

        return None

    def analyze_page_scripts(self):
        """Advanced script analysis for hidden data"""
        try:
            resp = self.session.get(f"{self.base_url}/Attendance", timeout=30)
            if resp.status_code != 200:
                return None

            # Look for JSON data in script tags
            script_patterns = [
                r"var\s+data\s*=\s*(\[.*?\]);",
                r"var\s+attendanceData\s*=\s*(\[.*?\]);",
                r"var\s+rows\s*=\s*(\[.*?\]);",
                r"data:\s*(\[.*?\])",
            ]

            for pattern in script_patterns:
                matches = re.findall(pattern, resp.text, re.DOTALL)
                for match in matches:
                    try:
                        # Clean the JSON string
                        json_str = match.replace("'", '"').replace(",\n", ",").replace("\n", "")
                        json_data = json.loads(json_str)
                        return self.parse_json_response(json_data)
                    except:
                        continue

        except Exception as e:
            logging.debug(f"Script analysis error: {e}")

        return None

    def save_data(self, data, prefix='attendance'):
        """Save data to CSV and JSON files"""
        if not data or not data.get('records'):
            logging.warning("No data to save")
            return None

        try:
            # Save as CSV
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_filename = f"{prefix}_{timestamp}.csv"
            
            df = pd.DataFrame(data['records'], columns=data['table_headers'])
            df.to_csv(csv_filename, index=False, encoding='utf-8')
            logging.info(f"✓ Data saved as CSV: {csv_filename}")

            # Save as JSON
            json_filename = f"{prefix}_{datetime.now().strftime('%Y%m')}.json"
            if os.path.exists(json_filename):
                with open(json_filename, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            else:
                existing_data = []

            existing_data.append(data)
            with open(json_filename, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)
            logging.info(f"✓ Data saved to JSON: {json_filename}")

            # Print preview
            print(f"\n=== DATA PREVIEW ({len(data['records'])} records) ===")
            print(df.head(10).to_string(index=False))
            
            return csv_filename

        except Exception as e:
            logging.error(f"Error saving data: {e}")
            return None

    def analyze_today_records(self, data, employee_id):
        """Analyze today's records for the given employee"""
        if not data or not data.get('records'):
            return None

        try:
            headers = data['table_headers']
            records = data['records']
            
            # Try to find relevant column indices
            date_idx = None
            emp_id_idx = None
            
            for i, header in enumerate(headers):
                header_lower = header.lower()
                if any(x in header_lower for x in ['date', 'time', 'timestamp']):
                    date_idx = i
                elif any(x in header_lower for x in ['employee', 'emp', 'id']):
                    emp_id_idx = i

            # Filter records for current employee
            employee_records = []
            if emp_id_idx is not None:
                employee_records = [r for r in records if len(r) > emp_id_idx and str(r[emp_id_idx]) == str(employee_id)]
            else:
                employee_records = records  # Assume all records belong to the employee

            # Filter for today's records
            today = datetime.now().date()
            today_records = []
            
            if date_idx is not None:
                for record in employee_records:
                    if len(record) > date_idx:
                        date_str = record[date_idx]
                        try:
                            # Try different date formats
                            for fmt in ['%m/%d/%Y %I:%M:%S %p', '%Y-%m-%d %H:%M:%S', '%m/%d/%Y', '%Y-%m-%d']:
                                try:
                                    record_date = datetime.strptime(date_str, fmt).date()
                                    if record_date == today:
                                        today_records.append(record)
                                    break
                                except ValueError:
                                    continue
                        except:
                            pass

            analysis = {
                'employee_id': employee_id,
                'total_records': len(employee_records),
                'today_records': len(today_records),
                'today_details': today_records,
                'analysis_timestamp': datetime.now().isoformat(),
                'data_source': data.get('source', 'unknown')
            }

            print(f"\n=== TODAY'S ANALYSIS ===")
            print(f"Total records: {analysis['total_records']}")
            print(f"Today's entries: {analysis['today_records']}")
            if today_records:
                print("Today's details:")
                for record in today_records:
                    print(f"  - {record}")

            return analysis

        except Exception as e:
            logging.error(f"Analysis error: {e}")
            return None

    def one_time_check(self, employee_id, password):
        """Perform a one-time attendance check"""
        logging.info("Starting one-time attendance check...")
        
        # Initialize and login
        self.init_session()
        if not self.login(employee_id, password):
            logging.error("Login failed")
            return False

        # Fetch attendance data
        data = self.fetch_attendance_data()
        if not data:
            logging.error("Failed to fetch attendance data")
            return False

        # Save and analyze data
        self.save_data(data)
        self.analyze_today_records(data, employee_id)
        
        return True

    def monitor_attendance(self, employee_id, password, interval=300, max_checks=None):
        """Monitor attendance for changes"""
        logging.info(f"Starting attendance monitor (interval: {interval}s)")
        
        self.init_session()
        if not self.login(employee_id, password):
            logging.error("Login failed - monitor aborted")
            return

        last_data_hash = None
        check_count = 0

        while True:
            try:
                logging.info(f"Check #{check_count + 1}")
                data = self.fetch_attendance_data()
                
                if data:
                    current_hash = self._hash_data(data)
                    
                    if last_data_hash is None:
                        last_data_hash = current_hash
                        logging.info(f"Initial baseline established: {len(data['records'])} records")
                        self.save_data(data, prefix='attendance_baseline')
                    elif current_hash != last_data_hash:
                        logging.info("✓ Change detected in attendance data!")
                        last_data_hash = current_hash
                        self.save_data(data, prefix='attendance_updated')
                        self.analyze_today_records(data, employee_id)
                    else:
                        logging.debug("No changes detected")
                else:
                    logging.warning("No data fetched this cycle")

                check_count += 1
                if max_checks and check_count >= max_checks:
                    logging.info("Reached maximum check count")
                    break

                logging.info(f"Waiting {interval} seconds until next check...")
                time.sleep(interval)

            except KeyboardInterrupt:
                logging.info("Monitor interrupted by user")
                break
            except Exception as e:
                logging.error(f"Monitor error: {e}")
                time.sleep(interval)  # Wait before retrying

    def _hash_data(self, data):
        """Create a hash of the data for change detection"""
        import hashlib
        hasher = hashlib.sha256()
        
        if data and data.get('records'):
            for record in data['records']:
                record_str = '|'.join(str(field) for field in record)
                hasher.update(record_str.encode('utf-8'))
        
        return hasher.hexdigest()


def main():
    parser = argparse.ArgumentParser(description='Enhanced NIA Attendance Crawler')
    parser.add_argument('--mode', choices=['once', 'monitor'], default='once',
                       help='Operation mode: one-time check or continuous monitoring')
    parser.add_argument('--interval', type=int, default=300,
                       help='Monitoring interval in seconds (default: 300)')
    parser.add_argument('--max-checks', type=int,
                       help='Maximum number of checks in monitor mode')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging for debugging')
    
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Verbose mode enabled")

    # Get credentials
    employee_id = input('Enter your Employee ID: ')
    password = getpass.getpass('Enter your Password: ')

    # Initialize crawler
    crawler = EnhancedNIAcrawler()

    try:
        if args.mode == 'once':
            success = crawler.one_time_check(employee_id, password)
            if success:
                print("\n=== CHECK COMPLETED SUCCESSFULLY ===")
            else:
                print("\n=== CHECK FAILED ===")
                sys.exit(1)
                
        elif args.mode == 'monitor':
            crawler.monitor_attendance(
                employee_id, 
                password, 
                interval=args.interval, 
                max_checks=args.max_checks
            )
            
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()