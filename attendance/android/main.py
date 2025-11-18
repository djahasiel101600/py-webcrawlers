import argparse
import json
import logging
import os
import time
from datetime import datetime
import getpass
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import subprocess

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
    
    def _find_chromium_binary(self):
        """Find Chromium binary in Ubuntu"""
        possible_paths = [
            '/usr/bin/chromium-browser',
            '/usr/bin/chromium',
            '/snap/bin/chromium'
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                logging.info(f"Found Chromium at: {path}")
                return path
        
        # Try which command
        try:
            result = subprocess.run(['which', 'chromium-browser'], 
                                  capture_output=True, text=True)
            if result.stdout.strip():
                return result.stdout.strip()
        except:
            pass
        
        logging.error("Chromium not found. Please install chromium-browser")
        return None

    def _find_chromedriver(self):
        """Find ChromeDriver"""
        possible_paths = [
            '/usr/local/bin/chromedriver',
            '/usr/bin/chromedriver'
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                logging.info(f"Found ChromeDriver at: {path}")
                return path
        
        # Try which command
        try:
            result = subprocess.run(['which', 'chromedriver'], 
                                  capture_output=True, text=True)
            if result.stdout.strip():
                return result.stdout.strip()
        except:
            pass
        
        # Last resort: try to download it
        return self._download_chromedriver()

    def _download_chromedriver(self):
        """Download ChromeDriver if not found"""
        try:
            logging.info("Downloading ChromeDriver...")
            import requests
            import zipfile
            
            # Download ChromeDriver for ARM64
            url = "https://storage.googleapis.com/chrome-for-testing-public/120.0.6099.109/linux64/chromedriver-linux64.zip"
            local_path = "/tmp/chromedriver.zip"
            
            # Download
            response = requests.get(url)
            with open(local_path, 'wb') as f:
                f.write(response.content)
            
            # Extract
            with zipfile.ZipFile(local_path, 'r') as zip_ref:
                zip_ref.extractall("/tmp/")
            
            # Move to bin
            os.system("mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/")
            os.system("chmod +x /usr/local/bin/chromedriver")
            
            logging.info("ChromeDriver downloaded successfully")
            return "/usr/local/bin/chromedriver"
            
        except Exception as e:
            logging.error(f"Failed to download ChromeDriver: {e}")
            return None

    def _create_driver_ubuntu(self):
        """Create Chromium driver for Ubuntu in proot-distro"""
        chromium_path = self._find_chromium_binary()
        chromedriver_path = self._find_chromedriver()
        
        if not chromium_path:
            raise Exception("Chromium not found. Run: apt install chromium-browser")
        
        options = Options()
        
        if self.headless:
            options.add_argument("--headless=new")
        
        # Essential options for proot-distro environment
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1280,720")
        options.add_argument("--remote-debugging-port=9222")
        
        # Performance optimizations
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins")
        options.add_argument("--disable-translate")
        
        # Set Chromium binary location
        options.binary_location = chromium_path
        
        try:
            logging.info(f"Using Chromium: {chromium_path}")
            
            if chromedriver_path:
                logging.info(f"Using ChromeDriver: {chromedriver_path}")
                service = Service(chromedriver_path)
                driver = webdriver.Chrome(service=service, options=options)
            else:
                logging.warning("ChromeDriver not found, trying without service")
                driver = webdriver.Chrome(options=options)
            
            driver.set_page_load_timeout(45)
            driver.implicitly_wait(10)
            
            logging.info("✓ Chromium driver created successfully")
            return driver
            
        except Exception as e:
            logging.error(f"Failed to create Chromium driver: {e}")
            
            # Try alternative approach
            try:
                logging.info("Trying alternative approach...")
                from selenium.webdriver.chrome.service import Service as ChromeService
                service = ChromeService()
                driver = webdriver.Chrome(service=service, options=options)
                return driver
            except Exception as e2:
                logging.error(f"Alternative approach failed: {e2}")
                raise

    def _login_with_selenium(self, driver, employee_id, password):
        """Login using Selenium"""
        try:
            logging.info("Opening login page...")
            driver.get(f"{self.auth_url}?ReturnUrl={self.base_url}/")
            
            wait = WebDriverWait(driver, 30)
            
            # Wait for login form elements
            employee_input = wait.until(
                EC.presence_of_element_located((By.NAME, "EmployeeID"))
            )
            password_input = wait.until(
                EC.presence_of_element_located((By.NAME, "Password"))
            )
            
            # Fill credentials
            employee_input.clear()
            employee_input.send_keys(employee_id)
            password_input.clear()
            password_input.send_keys(password)
            
            # Try to find and click submit button
            try:
                submit_btn = wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"))
                )
                submit_btn.click()
            except:
                # Fallback: press Enter
                from selenium.webdriver.common.keys import Keys
                password_input.send_keys(Keys.RETURN)
            
            # Wait for redirect to attendance portal
            wait.until(EC.url_contains(self.base_url))
            logging.info("✓ Login successful")
            
            # Additional wait for page stability
            time.sleep(3)
            return True
            
        except Exception as e:
            logging.error(f"Login failed: {e}")
            
            # Save screenshot and page source for debugging
            try:
                driver.save_screenshot("login_error.png")
                with open("login_page.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                logging.info("Saved debug files: login_error.png, login_page.html")
            except:
                pass
            return False

    def _load_attendance_data(self, driver):
        """Load and extract attendance data"""
        try:
            logging.info("Navigating to attendance page...")
            driver.get(f"{self.base_url}/Attendance")
            
            wait = WebDriverWait(driver, 30)
            
            # Wait for the data table
            wait.until(
                EC.presence_of_element_located((By.ID, "DataTables_Table_0"))
            )
            
            # Wait extra time for JavaScript to populate data
            logging.info("Waiting for data to load...")
            time.sleep(5)
            
            # Check if we have data rows
            rows = driver.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 tbody tr")
            logging.info(f"Found {len(rows)} rows in table")
            
            if not rows:
                logging.warning("No rows found. Waiting longer...")
                time.sleep(5)
                rows = driver.find_elements(By.CSS_SELECTOR, "#DataTables_Table_0 tbody tr")
                logging.info(f"After extra wait: {len(rows)} rows")
            
            # Get the page source
            html_content = driver.page_source
            return self.parse_attendance_html(html_content)
            
        except Exception as e:
            logging.error(f"Error loading attendance data: {e}")
            
            # Save page for debugging
            try:
                with open("attendance_page_debug.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                logging.info("Saved page source as attendance_page_debug.html")
            except:
                pass
            return None

    def get_attendance_data(self, employee_id, password):
        """Main method to get attendance data"""
        driver = None
        try:
            driver = self._create_driver_ubuntu()
            
            if self._login_with_selenium(driver, employee_id, password):
                attendance_data = self._load_attendance_data(driver)
                
                if attendance_data and attendance_data['records']:
                    self.save_as_csv(attendance_data['table_headers'], attendance_data['records'])
                    logging.info("✓ Attendance data retrieved successfully")
                else:
                    logging.warning("No attendance data found")
                
                return attendance_data
            else:
                logging.error("Login failed")
                return None
                
        except Exception as e:
            logging.error(f"Error in get_attendance_data: {e}")
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                    logging.info("Browser closed")
                except:
                    pass

    def parse_attendance_html(self, html_content):
        """Parse attendance table from HTML"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find the main table
        table = soup.find('table', {'id': 'DataTables_Table_0'})
        if not table:
            logging.error("Could not find attendance table with id 'DataTables_Table_0'")
            
            # Try to find any table
            tables = soup.find_all('table')
            if tables:
                table = tables[0]
                logging.warning("Using first table found (may not be correct)")
            else:
                logging.error("No tables found on page")
                return None

        # Extract headers
        headers = []
        thead = table.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]

        if not headers:
            logging.warning("No headers found, using default")
            headers = ['Column_' + str(i) for i in range(10)]

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
        """Save data to CSV file"""
        try:
            if not rows:
                logging.warning("No data to save")
                return
            
            # Create DataFrame
            df = pd.DataFrame(rows, columns=headers)
            
            # Generate filename
            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            # Save to CSV
            df.to_csv(filename, index=False, encoding='utf-8')
            logging.info(f"✓ Saved {len(rows)} records to {filename}")
            
            # Show preview
            print("\n" + "="*50)
            print("RECENT ATTENDANCE RECORDS:")
            print("="*50)
            print(df.head(10).to_string(index=False))
            print("="*50)
            
        except Exception as e:
            logging.error(f"Error saving CSV: {e}")

def main():
    parser = argparse.ArgumentParser(description="NIA Attendance Monitor - Ubuntu Termux")
    parser.add_argument('--visible', action='store_true', help='Show browser window (not headless)')
    parser.add_argument('--verbose', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    print("NIA Attendance Monitor - Ubuntu Termux Edition")
    print("="*50)
    
    # Get credentials
    employee_id = input("Enter your Employee ID: ")
    password = getpass.getpass("Enter your Password: ")
    
    # Create monitor instance
    monitor = NIAAttendanceMonitor(headless=not args.visible)
    
    print("\nStarting attendance check...")
    data = monitor.get_attendance_data(employee_id, password)
    
    if data:
        print("\n✅ SUCCESS: Attendance data retrieved successfully!")
        print(f"📊 Records found: {data['records_found']}")
    else:
        print("\n❌ FAILED: Could not retrieve attendance data")
        print("Check the debug files for more information")

if __name__ == "__main__":
    main()