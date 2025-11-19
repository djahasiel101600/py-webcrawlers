import requests
import json
from datetime import datetime
import re

class NIAAttendanceAPI:
    def __init__(self, base_url="https://attendance.caraga.nia.gov.ph"):
        self.base_url = base_url
        self.session = requests.Session()
        
        # Set common headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'X-Requested-With': 'XMLHttpRequest'
        })
    
    def login(self, employee_id, password):
        """Login to the NIA system"""
        login_url = "https://accounts.nia.gov.ph/Account/Login"
        
        # First, get the login page to obtain anti-forgery token
        response = self.session.get(login_url)
        
        # Extract the verification token (simplified - you might need to adjust based on actual HTML)
        token_match = re.search(r'name="__RequestVerificationToken".*?value="([^"]+)"', response.text)
        if not token_match:
            print("Could not find verification token")
            return False
        
        token = token_match.group(1)
        
        # Prepare login data
        login_data = {
            'EmployeeId': employee_id,
            'Password': password,
            'RememberMe': 'false',
            '__RequestVerificationToken': token
        }
        
        # Perform login
        response = self.session.post(login_url, data=login_data, allow_redirects=True)
        
        # Check if login was successful (adjust based on actual response)
        if response.status_code == 200 and employee_id in response.text:
            print("Login successful!")
            return True
        else:
            print("Login failed!")
            return False
    
    def get_attendance_data(self, year=2025, month="November", employee_id="222534", start=0, length=10):
        """Get attendance data for specified parameters"""
        url = f"{self.base_url}/Attendance/IndexData/{year}?month={month}&eid={employee_id}"
        
        # DataTables server-side processing parameters
        data = {
            "draw": "1",
            "columns[0][data]": "Id",
            "columns[0][name]": "",
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
            "columns[1][data]": "DateTimeStamp",
            "columns[1][name]": "",
            "columns[1][searchable]": "true",
            "columns[1][orderable]": "true",
            "columns[1][search][value]": "",
            "columns[1][search][regex]": "false",
            "columns[2][data]": "Temperature",
            "columns[2][name]": "",
            "columns[2][searchable]": "true",
            "columns[2][orderable]": "true",
            "columns[2][search][value]": "",
            "columns[2][search][regex]": "false",
            "columns[3][data]": "Name",
            "columns[3][name]": "",
            "columns[3][searchable]": "true",
            "columns[3][orderable]": "true",
            "columns[3][search][value]": "",
            "columns[3][search][regex]": "false",
            "columns[4][data]": "EmployeeID",
            "columns[4][name]": "",
            "columns[4][searchable]": "true",
            "columns[4][orderable]": "true",
            "columns[4][search][value]": "",
            "columns[4][search][regex]": "false",
            "columns[5][data]": "MachineName",
            "columns[5][name]": "",
            "columns[5][searchable]": "true",
            "columns[5][orderable]": "true",
            "columns[5][search][value]": "",
            "columns[5][search][regex]": "false",
            "order[0][column]": "1",
            "order[0][dir]": "desc",
            "start": str(start),
            "length": str(length),
            "search[value]": "",
            "search[regex]": "false"
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': f'{self.base_url}/Attendance'
        }
        
        try:
            response = self.session.post(url, data=data, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching attendance data: {e}")
            return None
    
    @staticmethod
    def parse_net_date(net_date_string):
        """Convert .NET Date format to Python datetime"""
        match = re.search(r'\/Date\((\d+)\)\/', net_date_string)
        if match:
            timestamp = int(match.group(1))
            return datetime.fromtimestamp(timestamp / 1000)
        return None
    
    def print_attendance_summary(self, data):
        """Print a formatted summary of attendance data"""
        if not data or 'data' not in data:
            print("No data received")
            return
        
        records = data['data']
        total_records = data.get('recordsTotal', 0)
        
        print(f"\n=== ATTENDANCE SUMMARY ===")
        print(f"Total Records: {total_records}")
        print(f"Displaying: {len(records)} records")
        print("=" * 50)
        
        for i, record in enumerate(records, 1):
            date_time = self.parse_net_date(record['DateTimeStamp'])
            date_str = date_time.strftime("%Y-%m-%d %H:%M:%S") if date_time else "Invalid Date"
            
            print(f"{i}. {date_str}")
            print(f"   Temperature: {record['Temperature']}°C")
            print(f"   Machine: {record['MachineName']}")
            print(f"   Status: {'Success' if record['AccessResult'] == 1 else 'Other'}")
            print(f"   Name: {record['Name']}")
            print("-" * 40)

def main():
    # Initialize the API client
    api = NIAAttendanceAPI()
    
    # If you need to login first (uncomment and add your credentials)
    if not api.login("222534", "#J1h1siel"):
        print("Cannot proceed without login")
        return
    
    # Fetch attendance data
    print("Fetching attendance data...")
    
    # You can customize these parameters
    attendance_data = api.get_attendance_data(
        year=2025,
        month="November", 
        employee_id="222534",
        start=0,      # Pagination start
        length=20     # Number of records
    )
    
    if attendance_data:
        # Print formatted summary
        api.print_attendance_summary(attendance_data)
        
        # Save raw data to file
        with open('attendance_data.json', 'w', encoding='utf-8') as f:
            json.dump(attendance_data, f, indent=2, ensure_ascii=False)
        print(f"\nRaw data saved to 'attendance_data.json'")
        
        # Additional analysis
        records = attendance_data['data']
        if records:
            first_record = records[0]
            last_record = records[-1]
            
            first_date = api.parse_net_date(first_record['DateTimeStamp'])
            last_date = api.parse_net_date(last_record['DateTimeStamp'])
            
            print(f"\nDate Range: {first_date.strftime('%Y-%m-%d')} to {last_date.strftime('%Y-%m-%d')}")
            
            # Calculate average temperature
            temps = [r['Temperature'] for r in records if r['Temperature']]
            if temps:
                avg_temp = sum(temps) / len(temps)
                print(f"Average Temperature: {avg_temp:.2f}°C")

if __name__ == "__main__":
    main()