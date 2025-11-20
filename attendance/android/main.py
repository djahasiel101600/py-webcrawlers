import websocket
import re
import json
import requests
import threading
import time
import random
import argparse
import hashlib
import logging
import os
import csv
from datetime import datetime
import getpass
from urllib.parse import quote
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.logging import RichHandler
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
from rich import box
from typing import List, Optional, Dict, Any
import yaml
from dataclasses import dataclass

console = Console()

# Set up logging with hacker-style formatting
logging.basicConfig(
    level=logging.INFO,
    format="[dim]│[/dim] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True, show_path=False)]
)

class Config:
    def __init__(self):
        self.defaults = {
            'base_url': "https://attendance.caraga.nia.gov.ph",
            'auth_url': "https://accounts.nia.gov.ph/Account/Login",
            'enable_csv': False
        }
        self.config_path = os.path.expanduser('~/.nia_monitor_config.yaml')
    
    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    user_config = yaml.safe_load(f) or {}
                    return {**self.defaults, **user_config}
            except Exception as e:
                logging.warning(f"│ CONFIG LOAD ERROR: {e}")
        return self.defaults.copy()
    
    def save(self, config_data):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False)
        except Exception as e:
            logging.error(f"│ SAVE CONFIG ERROR: {e}")

@dataclass
class AttendanceRecord:
    date_time: datetime
    temperature: Optional[float]
    employee_id: str
    employee_name: str
    machine_name: str
    status: str
    
    @classmethod
    def from_api_data(cls, api_record: Dict[str, Any]) -> 'AttendanceRecord':
        """Create record from API JSON data"""
        date_time = cls.parse_net_date(api_record['DateTimeStamp'])
        temperature = float(api_record['Temperature']) if api_record['Temperature'] else None
        
        # Determine status from AccessResult
        status = "ACCESS_GRANTED" if api_record['AccessResult'] == 1 else "ACCESS_DENIED"
        
        return cls(
            date_time=date_time,
            temperature=temperature,
            employee_id=api_record['EmployeeID'],
            employee_name=api_record['Name'],
            machine_name=api_record['MachineName'],
            status=status
        )
    
    @staticmethod
    def parse_net_date(net_date_string):
        """Convert .NET Date format to Python datetime"""
        match = re.search(r'\/Date\((\d+)\)\/', net_date_string)
        if match:
            timestamp = int(match.group(1))
            return datetime.fromtimestamp(timestamp / 1000)
        return datetime.now()

class NIASignalRMonitor:
    def __init__(self, base_url, session_cookies, verbose=False):
        self.base_url = base_url
        self.session_cookies = session_cookies
        self.ws = None
        self.is_connected = False
        self.callbacks = []
        self.message_id = 0
        self.verbose = verbose
    
    def add_callback(self, callback):
        """Add a callback function for attendance updates"""
        if callable(callback):
            self.callbacks.append(callback)
    
    def on_message(self, ws, message):
        """Handle incoming WebSocket messages with hacker style"""
        try:
            data = json.loads(message)
            
            if isinstance(data, dict) and 'M' in data:
                methods = data.get('M', [])
                for method in methods:
                    method_name = method.get('H')
                    method_type = method.get('M')
                    method_args = method.get('A', [])
                    
                    if method_name == "biohub" and method_type in ["attendanceUpdate", "newRecord"]:
                        self._handle_attendance_update(method_args)
                        # Show data transmission indicator
                        console.print("│ [bright_black]📡 DATA STREAM: SignalR packet received[/bright_black]")
                        
        except json.JSONDecodeError:
            console.print("│ [red]⚠️  DATA CORRUPTION: Invalid JSON packet[/red]")
    
    def on_error(self, ws, error):
        """Handle WebSocket errors with hacker style"""
        if self.verbose:
            console.print(f"│ [red]🚨 CONNECTION ERROR: {error}[/red]")
        self.is_connected = False
    
    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket closure"""
        console.print("│ [yellow]🔌 SIGNALR: Connection terminated[/yellow]")
        self.is_connected = False
    
    def on_open(self, ws):
        """Handle WebSocket connection opened"""
        console.print("│ [green]🔓 SIGNALR: Secure channel established[/green]")
        self.is_connected = True
        self._send_join_message()
    
    def _handle_attendance_update(self, args):
        """Process real-time attendance updates"""
        if args:
            attendance_data = args[0] if isinstance(args, list) and len(args) > 0 else args
            
            console.print("│ [cyan]⚡ REAL-TIME: Processing biometric data...[/cyan]")
            
            for callback in self.callbacks:
                try:
                    callback(attendance_data)
                except Exception as e:
                    console.print(f"│ [red]⚠️  CALLBACK ERROR: {e}[/red]")
    
    def _send_join_message(self):
        """Send join message"""
        join_message = {
            "H": "biohub",
            "M": "Join", 
            "A": [],
            "I": self._get_next_message_id()
        }
        self._send_message(join_message)
        console.print("│ [blue]🔑 AUTH: Joining biohub channel...[/blue]")
    
    def _send_message(self, message):
        """Send message through WebSocket"""
        if self.ws and self.is_connected:
            try:
                message_str = json.dumps(message)
                self.ws.send(message_str)
            except Exception as e:
                console.print(f"│ [red]⚠️  TRANSMISSION FAILED: {e}[/red]")
    
    def _get_next_message_id(self):
        """Get next message ID"""
        self.message_id += 1
        return self.message_id
    
    def connect(self, connection_token):
        """Connect to SignalR WebSocket with hacker style"""
        try:
            console.print("│ [blue]🌐 INITIATING: SignalR handshake...[/blue]")
            
            # Build WebSocket URL with connection token
            websocket_url = self._build_websocket_url(connection_token)
            
            # Prepare headers with cookies
            cookie_header = '; '.join([f'{k}={v}' for k, v in self.session_cookies.items()])
            
            headers = {
                'Cookie': cookie_header,
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Origin': self.base_url.replace('https://', ''),
                'Referer': f'{self.base_url}/Attendance'
            }
            
            console.print(f"│ [dim]🔗 ENDPOINT: {self.base_url}[/dim]")
            console.print("│ [yellow]⏳ ESTABLISHING: Secure WebSocket...[/yellow]")
            
            # Create WebSocket connection
            self.ws = websocket.WebSocketApp(
                websocket_url,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_open=self.on_open,
                header=headers
            )
            
            # Run in background thread
            def run_websocket():
                self.ws.run_forever()
            
            thread = threading.Thread(target=run_websocket)
            thread.daemon = True
            thread.start()
            
            # Wait for connection with cool animation
            for i in range(15):
                if self.is_connected:
                    return True
                console.print(f"│ [dim]🔄 HANDSHAKE: {i+1}/15 attempts[/dim]", end="\r")
                time.sleep(1)
            
            return False
            
        except Exception as e:
            console.print(f"│ [red]🚨 CONNECTION FAILED: {e}[/red]")
            return False
    
    def _build_websocket_url(self, connection_token):
        """Build WebSocket URL with connection token"""
        encoded_token = quote(connection_token)
        connection_data = quote('[{"name":"biohub"}]')
        
        url = (f"wss://attendance.caraga.nia.gov.ph/signalr/connect"
               f"?transport=webSockets"
               f"&clientProtocol=2.1"
               f"&connectionToken={encoded_token}"
               f"&connectionData={connection_data}"
               f"&tid={random.randint(0, 10)}")
        
        return url
    
    def disconnect(self):
        """Disconnect WebSocket"""
        if self.ws:
            console.print("│ [yellow]🔒 DISCONNECTING: Secure channel...[/yellow]")
            self.ws.close()
            self.is_connected = False

class NIAAttendanceMonitor:
    def __init__(self, config=None):
        self.config = config or Config().load()
        self.base_url = self.config['base_url']
        self.auth_url = self.config['auth_url']
        self.session = requests.Session()
        self.state_file = os.path.expanduser('~/.nia_monitor_state.json')
        
        # Set common headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'X-Requested-With': 'XMLHttpRequest'
        })
        
        self._load_state()
    
    def _load_state(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    self.state = json.load(f)
                console.print("│ [green]📁 STATE: Session data loaded[/green]")
            else:
                self.state = {'last_check': None, 'known_records': []}
                console.print("│ [yellow]📁 STATE: New session initialized[/yellow]")
        except Exception as e:
            console.print(f"│ [red]⚠️  STATE LOAD ERROR: {e}[/red]")
            self.state = {'last_check': None, 'known_records': []}
    
    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            console.print(f"│ [red]⚠️  STATE SAVE ERROR: {e}[/red]")
    
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
        
        if new_records:
            console.print(f"│ [green]📈 DETECTED: {len(new_records)} new biometric entries[/green]")
        
        return {
            'new_records': new_records,
            'missing_records': missing_records,
            'total_current': len(current_records),
            'changes_detected': len(new_records) > 0 or len(missing_records) > 0
        }

    def login(self, employee_id, password):
        """Login to the NIA system with hacker style"""
        try:
            console.print("│ [blue]🔐 AUTH: Accessing NIA portal...[/blue]")
            
            # Get login page for token
            response = self.session.get(self.auth_url)
            
            # Extract verification token
            token_match = re.search(r'name="__RequestVerificationToken".*?value="([^"]+)"', response.text)
            if not token_match:
                console.print("│ [red]🚨 AUTH: Security token not found[/red]")
                return False
            
            token = token_match.group(1)
            console.print("│ [green]🔑 AUTH: Security token acquired[/green]")
            
            # Prepare login data
            login_data = {
                'EmployeeId': employee_id,
                'Password': password,
                'RememberMe': 'false',
                '__RequestVerificationToken': token
            }
            
            console.print("│ [yellow]⏳ AUTH: Verifying credentials...[/yellow]")
            
            # Perform login
            response = self.session.post(self.auth_url, data=login_data, allow_redirects=True)
            
            # Check if login was successful
            if response.status_code == 200 and employee_id in response.text:
                console.print("│ [green]✅ AUTH: Access granted[/green]")
                return True
            else:
                console.print("│ [red]🚨 AUTH: Access denied - invalid credentials[/red]")
                return False
            
        except Exception as e:
            console.print(f"│ [red]🚨 AUTH: Connection failed - {e}[/red]")
            return False
    
    def get_attendance_data(self, employee_id, year=None, month=None, length=50):
        """Get attendance data via API with hacker style"""
        if not year:
            year = datetime.now().year
        if not month:
            month = datetime.now().strftime("%B")
        
        url = f"{self.base_url}/Attendance/IndexData/{year}?month={month}&eid={employee_id}"
        
        console.print(f"│ [blue]📡 QUERY: Fetching attendance records...[/blue]")
        
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
            "start": "0",
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
            api_data = response.json()
            
            console.print(f"│ [green]✅ DATA: {len(api_data.get('data', []))} records retrieved[/green]")
            
            # Convert to our record format
            records = [AttendanceRecord.from_api_data(record) for record in api_data.get('data', [])]
            
            # Process changes and optional CSV saving
            return self._process_attendance_data(records, employee_id, api_data)
            
        except requests.exceptions.RequestException as e:
            console.print(f"│ [red]🚨 API ERROR: {e}[/red]")
            return None

    def _process_attendance_data(self, records, employee_id, api_data):
        """Process attendance data with change detection"""
        if records:
            changes = self.detect_changes(records)
            
            # Conditional CSV saving
            if self.config.get('enable_csv', False):
                self.save_as_csv(records, employee_id, changes)
        
        return {
            'records': records,
            'total_records': api_data.get('recordsTotal', 0),
            'timestamp': datetime.now().isoformat()
        }

    def save_as_csv(self, records, employee_id, changes):
        """Save attendance data as CSV (optional)"""
        if not self.config.get('enable_csv', False):
            return None
            
        try:
            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                # Write metadata as comments
                csvfile.write("# NIA Attendance Export (API)\n")
                csvfile.write(f"# Generated: {datetime.now().isoformat()}\n")
                csvfile.write(f"# Employee: {employee_id}\n")
                csvfile.write(f"# Records: {len(records)}\n")
                csvfile.write(f"# New Records: {len(changes['new_records'])}\n#\n")
                
                writer = csv.writer(csvfile)
                # Write headers
                writer.writerow(['Date Time', 'Temperature', 'Employee ID', 'Employee Name', 'Machine Name', 'Status'])
                
                # Write all records
                for record in records:
                    writer.writerow([
                        record.date_time.strftime('%Y-%m-%d %H:%M:%S'),
                        record.temperature or '',
                        record.employee_id,
                        record.employee_name,
                        record.machine_name,
                        record.status
                    ])

            console.print(f"│ [green]💾 EXPORT: Data saved to {filename}[/green]")
            return filename

        except Exception as e:
            console.print(f"│ [red]⚠️  EXPORT ERROR: {e}[/red]")
            return None
    
    def analyze_attendance_patterns(self, attendance_data, employee_id):
        """Analyze attendance patterns with hacker style"""
        try:
            if not attendance_data or 'records' not in attendance_data:
                return None
            
            records = attendance_data['records']
            
            if not records:
                console.print("│ [yellow]📊 ANALYSIS: No records to analyze[/yellow]")
                return None
            
            # Filter records for this employee
            my_records = [r for r in records if r.employee_id == employee_id]
            failed_records = [r for r in my_records if r.status == "ACCESS_DENIED"]
            
            if not my_records:
                console.print("│ [yellow]📊 ANALYSIS: No personal records found[/yellow]")
                return None
            
            # Parse today's records
            today = datetime.now().date()
            today_records = [r for r in my_records if r.date_time.date() == today]
            
            # Analysis with hacker style
            console.print("│ [blue]🔍 ANALYSIS: Scanning biometric patterns...[/blue]")
            
            if today_records:
                if len(today_records) < 2:
                    console.print("│ [yellow]⚠️  PATTERN: Incomplete session detected[/yellow]")
                elif len(today_records) % 2 != 0:
                    console.print("│ [yellow]⚠️  PATTERN: Missing exit record suspected[/yellow]")
                else:
                    console.print("│ [green]✅ PATTERN: Session records complete[/green]")
            else:
                console.print("│ [red]🚨 PATTERN: No activity detected today[/red]")
            
            return {
                'employee_id': employee_id,
                'total_records': len(my_records),
                'total_all_records': len(records),
                'today_records': len(today_records),
                'today_details': today_records,
                'failed_records': len(failed_records)
            }
            
        except Exception as e:
            console.print(f"│ [red]🚨 ANALYSIS ERROR: {e}[/red]")
            return None

    def get_signalr_connection_token(self):
        """Get SignalR connection token with hacker style"""
        try:
            console.print("│ [blue]🔧 SIGNALR: Acquiring connection token...[/blue]")
            
            # Try to get the attendance page which should set the proper cookies
            response = self.session.get(f"{self.base_url}/Attendance")
            
            # Method 1: Check if token is in Set-Cookie header
            if 'Set-Cookie' in response.headers:
                set_cookie = response.headers['Set-Cookie']
                
                # Look for connection token in cookies
                token_patterns = [
                    r'connectionToken=([^;]+)',
                    r'SignalR\.ConnectionToken=([^;]+)',
                    r'__SignalRToken=([^;]+)',
                ]
                
                for pattern in token_patterns:
                    match = re.search(pattern, set_cookie)
                    if match:
                        token = match.group(1)
                        console.print(f"│ [green]✅ TOKEN: Acquired from headers[/green]")
                        return token
            
            # Method 2: SignalR negotiation
            console.print("│ [yellow]🔧 SIGNALR: Attempting negotiation protocol...[/yellow]")
            return self._try_signalr_negotiation()
            
        except Exception as e:
            console.print(f"│ [red]🚨 TOKEN ERROR: {e}[/red]")
            return None

    def _try_signalr_negotiation(self):
        """Try to negotiate with SignalR server to get connection token"""
        try:
            # SignalR negotiation URL (common pattern)
            negotiate_url = f"{self.base_url}/signalr/negotiate"
            
            # Common SignalR negotiation parameters
            params = {
                'clientProtocol': '2.1',
                'connectionData': '[{"name":"biohub"}]',
                '_': str(int(time.time() * 1000))
            }
            
            headers = {
                'Referer': f'{self.base_url}/Attendance',
                'X-Requested-With': 'XMLHttpRequest'
            }
            
            console.print("│ [blue]🔧 SIGNALR: Negotiating handshake...[/blue]")
            
            response = self.session.get(negotiate_url, params=params, headers=headers)
            
            if response.status_code == 200:
                negotiation_data = response.json()
                
                # The connection token should be in the response
                if 'ConnectionToken' in negotiation_data:
                    token = negotiation_data['ConnectionToken']
                    console.print("│ [green]✅ TOKEN: Negotiation successful[/green]")
                    return token
                elif 'Url' in negotiation_data:
                    # Some SignalR setups return a URL with the token
                    url = negotiation_data['Url']
                    token_match = re.search(r'connectionToken=([^&]+)', url)
                    if token_match:
                        token = token_match.group(1)
                        console.print("│ [green]✅ TOKEN: Extracted from URL[/green]")
                        return token
            else:
                console.print(f"│ [red]🚨 NEGOTIATION FAILED: HTTP {response.status_code}[/red]")
            
            return None
            
        except Exception as e:
            console.print(f"│ [red]🚨 NEGOTIATION ERROR: {e}[/red]")
            return None

    def _create_hacker_table(self, records, title="BIOMETRIC DATA"):
        """Create a compact hacker-style table"""
        if not records:
            return None
            
        # Compact table for mobile
        table = Table(
            show_header=True, 
            header_style="bold bright_white",
            box=box.SIMPLE_HEAD,
            width=60,
            show_lines=False
        )
        
        table.add_column("#", style="dim", width=3)
        table.add_column("TIME", style="green", width=8)
        table.add_column("TEMP", style="yellow", width=5)
        table.add_column("STATUS", style="bright_white", width=12)
        table.add_column("AUTH", style="bright_white", width=6)
        
        for idx, record in enumerate(records, start=1):
            time_str = record.date_time.strftime("%H:%M")
            temp_str = f"{record.temperature:.1f}" if record.temperature else "N/A"
            
            # Hacker-style status indicators
            if record.status == "ACCESS_GRANTED":
                status_display = "GRANTED"
                auth_display = "✅"
                row_style = "bright_green"
            else:
                status_display = "DENIED"
                auth_display = "❌"
                row_style = "bright_red"
            
            table.add_row(
                str(idx), 
                time_str, 
                temp_str, 
                status_display, 
                auth_display,
                style=row_style
            )
        
        # Wrap table in a panel
        panel = Panel(
            Align.center(table),
            title=f"🔒 {title}",
            title_align="center",
            border_style="bright_blue",
            width=66
        )
        
        return panel

    def start_signalr_monitor(self, employee_id, password, on_attendance_update, verbose=False):
        """Start real-time SignalR WebSocket monitoring with hacker aesthetic"""
        console.print("\n" + "═" * 59)
        console.print(Align.center("🚀 NIA ATTENDANCE MONITOR - REAL-TIME MODE"))
        console.print(Align.center("🔐 SECURE CONNECTION INITIATED"))
        console.print("═" * 59)
        
        # First, login to get session cookies
        if not self.login(employee_id, password):
            console.print("│ [red]🚨 ABORT: Authentication failed[/red]")
            return False
        
        # Get current attendance data to display initially
        console.print("│ [blue]📡 INIT: Loading current attendance state...[/blue]")
        current_attendance = self.get_attendance_data(employee_id)
        
        # Display current attendance in hacker style
        if current_attendance:
            self._display_current_attendance_hacker(current_attendance, employee_id)
        else:
            console.print("│ [red]🚨 ERROR: No attendance data available[/red]")
        
        # Get connection token
        connection_token = self.get_signalr_connection_token()
        
        if not connection_token:
            console.print("│ [yellow]⚠️  SIGNALR: Falling back to polling mode[/yellow]")
            return self.real_time_monitor(employee_id, password)
        
        # Get cookies and create monitor
        cookies_dict = {c.name: c.value for c in self.session.cookies}
        signalr_monitor = NIASignalRMonitor(self.base_url, cookies_dict, verbose=verbose)
        signalr_monitor.add_callback(on_attendance_update)
        
        console.print("│ [blue]🌐 SIGNALR: Establishing real-time channel...[/blue]")
        
        if signalr_monitor.connect(connection_token):
            console.print("│ [59n]✅ SIGNALR: Real-time channel active[/green]")
            console.print("│ [dim]💡 CONTROLS: Press Ctrl+C to terminate connection[/dim]")
            console.print("─" * 59)
            
            try:
                # Keep main thread alive
                while signalr_monitor.is_connected:
                    time.sleep(1)
                        
            except KeyboardInterrupt:
                console.print("\n│ [yellow]⚠️  USER: Termination signal received[/yellow]")
                
            finally:
                signalr_monitor.disconnect()
        
        else:
            console.print("│ [red]🚨 SIGNALR: Connection failed[/red]")
            console.print("│ [yellow]🔄 FALLBACK: Activating polling mode...[/yellow]")
            return self.real_time_monitor(employee_id, password)
        
        console.print("│ [green]✅ SYSTEM: Monitor terminated successfully[/green]")
        return True

    def _display_current_attendance_hacker(self, attendance_data, employee_id):
        """Display current day's attendance in hacker style"""
        analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
        
        if not analysis:
            console.print("│ [yellow]📭 STATUS: No analyzable data available[/yellow]")
            return
        
        today_records = analysis.get('today_details', [])
        
        # Display summary in hacker style
        summary_text = Text()
        summary_text.append("📊 TODAY'S ACTIVITY: ", style="bold bright_white")
        summary_text.append(f"{len(today_records)} records", style="green")
        summary_text.append(" | ", style="dim")
        summary_text.append(f"{analysis.get('failed_records', 0)} denied", style="red" if analysis.get('failed_records', 0) > 0 else "dim")
        
        console.print(Panel(
            summary_text,
            border_style="bright_blue",
            width=66
        ))
        
        # Display records in compact hacker table
        if today_records:
            table_panel = self._create_hacker_table(today_records, "TODAY'S BIOMETRIC LOG")
            console.print(table_panel)
            
            # Quick analysis
            if len(today_records) == 0:
                status = "🚨 NO ACTIVITY DETECTED"
                style = "bright_red"
            elif len(today_records) == 1:
                status = "⏳ AWAITING EXIT SCAN"
                style = "bright_yellow"
            elif len(today_records) % 2 == 0:
                status = "✅ SESSION COMPLETE"
                style = "bright_green"
            else:
                status = "⚠️  INCOMPLETE SESSION"
                style = "bright_yellow"
                
            console.print(Panel(
                Align.center(Text(status, style=style)),
                border_style=style,
                width=66
            ))
        else:
            console.print(Panel(
                Align.center("📭 NO RECORDS FOUND FOR TODAY"),
                border_style="yellow",
                width=66
            ))
        
        console.print("│ [dim]🔍 SYSTEM: Monitoring for real-time updates...[/dim]")

    def real_time_monitor(self, employee_id, password, poll_interval=10):
        """Real-time monitoring with frequent API polls - hacker style"""
        console.print("\n" + "═" * 59)
        console.print(Align.center("🔄 NIA ATTENDANCE MONITOR - POLLING MODE"))
        console.print(Align.center(f"📡 POLLING INTERVAL: {poll_interval}s"))
        console.print("═" * 59)
        
        if not self.login(employee_id, password):
            return
        
        last_records_count = 0
        check_count = 0
        
        try:
            while True:
                attendance_data = self.get_attendance_data(employee_id)
                check_count += 1
                
                if attendance_data:
                    analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
                    current_count = len(attendance_data.get('records', []))
                    
                    # Clear and update display
                    console.clear59
                    console.print(Align.center(f"🔍 LIVE MONITOR - SCAN #{check_count}"))
                    console.print("─" * 59)
                    
                    # Show real-time status
                    console.print(f"│ [dim]🕒 LAST SCAN: {datetime.now().strftime('%H:%M:%S')}[/dim]")
                    
                    if analysis and analysis.get('today_details'):
                        today_records = analysis['today_details']
                        
                        # Real-time alerts for new records
                        if current_count > last_records_count and last_records_count > 0:
                            new_records = current_count - last_records_count
                            console.print(Panel(
                                Align.center(f"🚨 {new_records} NEW BIOMETRIC ENTRIES DETECTED!"),
                                border_style="bright_green",
                                width=66
                            ))
                        
                        last_records_count = current_count
                        
                        # Display today's records in hacker table
                        table_panel = self._create_hacker_table(today_records, "LIVE BIOMETRIC FEED")
                        console.print(table_panel)
                        
                        # Real-time insights
                        insights = []
                        if len(today_records) == 0:
                            insights.append("🚨 NO ACTIVITY TODAY")
                        elif len(today_records) == 1:
                            insights.append("⏳ AWAITING EXIT SCAN")
                        elif len(today_records) % 2 == 0:
                            insights.append(f"✅ {len(today_records)//2} COMPLETE SESSIONS")
                        else:
                            insights.append("⚠️  INCOMPLETE SESSION")
                            
                        console.print(Panel(
                            " | ".join(insights),
                            border_style="bright_blue",
                            width=66
                        ))
                            
                    else:
                        console.print(Panel(
                            Align.center("📭 NO RECORDS FOR TODAY"),
                            border_style="yellow",
                            width=66
                        ))
                        last_records_count = 0
                else:
                    console.print(Panel(
                        Align.center("❌ DATA ACQUISITION FAILED"),
                        border_style="red",
                        width=66
                    ))
                
                # Countdown with hacker style
                console.print("\n│ [dim]💡 CONTROLS: Press Q to terminate monitoring[/dim]")
                start_time = time.time()

                while time.time() - start_time < poll_interval:
                    remaining = poll_interval - int(time.time() - start_time)
                    if remaining <= 0:
                        break
                        
                    # Cool countdown display
                    countdown_text = f"⏳ NEXT SCAN IN {remaining:02d}s"
                    console.print(f"│ [cyan]{countdown_text}[/cyan]", end="\r")
                    
                    # Check for quit command
                    try:
                        import select
                        import sys
                        if sys.stdin in select.select([sys.stdin], [], [], 1)[0]:
                            key = sys.stdin.readline().strip().lower()
                            if key == 'q':
                                raise KeyboardInterrupt
                    except (ImportError, KeyboardInterrupt):
                        console.print()
                        raise KeyboardInterrupt
                    except:
                        time.sleep(1)

                console.print()  # New line after countdown
                    
        except KeyboardInterrupt:
            console.print("\n│ [yellow]⚠️  USER: Termination signal received[/yellow]")
        
        console.print("│ [green]✅ SYSTEM: Polling monitor terminated[/green]")

def handle_signalr_attendance_update(attendance_data):
    """Callback for real-time updates - hacker style"""
    console.print()
    console.print("═" * 59)
    console.print(Align.center("⚡ REAL-TIME BIOMETRIC UPDATE"))
    console.print("─" * 59)
    
    if isinstance(attendance_data, dict):
        employee_name = attendance_data.get('Name', 'UNKNOWN_USER')
        date_time_str = attendance_data.get('DateTimeStamp', '')
        temperature = attendance_data.get('Temperature')
        status = "ACCESS_GRANTED" if attendance_data.get('AccessResult') == 1 else "ACCESS_DENIED"
        
        # Parse .NET date
        date_time = AttendanceRecord.parse_net_date(date_time_str)
        
        # Create hacker-style update display
        update_panel = Panel(
            Align.left(
                Text().append("👤 USER: ", style="bold").append(f"{employee_name}\n", style="bright_white")
                .append("🕒 TIME: ", style="bold").append(f"{date_time.strftime('%H:%M:%S')}\n", style="green")
                .append("🌡️  TEMP: ", style="bold").append(f"{temperature}°C\n" if temperature else "N/A\n", style="yellow")
                .append("🔐 ACCESS: ", style="bold").append(
                    f"{status}", 
                    style="bright_green" if status == "ACCESS_GRANTED" else "bright_red"
                )
            ),
            title="🚨 LIVE BIOMETRIC EVENT",
            border_style="bright_blue" if status == "ACCESS_GRANTED" else "bright_red",
            width=66
        )
        
        console.print(update_panel)
        
    console.print(f"│ [dim]📡 SIGNAL: {datetime.now().strftime('%H:%M:%S')}[/dim]")
    console.print("│ [dim]🔍 SYSTEM: Continuing surveillance...[/dim]")
    console.print("─" * 59)

def main():
    console.print("\n")
    console.print(Align.center("┌─────────────────────────────────────────────────────┐"))
    console.print(Align.center("│              NIA ATTENDANCE MONITOR v2.0            │"))
    console.print(Align.center("│             [red]SECURE BIOMETRIC SURVEILLANCE[/red]          │"))
    console.print(Align.center("└─────────────────────────────────────────────────────┘"))
    console.print()
    
    config = Config().load()
    
    parser = argparse.ArgumentParser(
        description="NIA Attendance Monitor - API & Real-Time Version",
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
        default=300,
        help='Monitoring interval (seconds)'
    )
    parser.add_argument(
        '--enable-csv',
        action='store_true',
        help='Enable CSV export (disabled by default)'
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

    monitor = NIAAttendanceMonitor(config=config)
    
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
        # Modified menu with hacker style
        console.print("\n[bold bright_white]OPERATION MODES:[/bold bright_white]")
        console.print("[bright_cyan]1.[/bright_cyan] 🔍 Quick System Scan")
        console.print("[bright_cyan]2.[/bright_cyan] 📡 Real-time Surveillance") 
        console.print("[bright_cyan]3.[/bright_cyan] ⚙️  System Configuration")
        
        choice = Prompt.ask(
            "\n[bright_white]SELECT OPERATION[/bright_white]", 
            choices=["1", "2", "3"], 
            default="1"
        )

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
                
                for idx, record in enumerate(today_details, start=1):
                    time_str = record.date_time.strftime("%H:%M:%S")
                    temp_str = f"{record.temperature:.1f}" if record.temperature else "N/A"
                    status = record.status
                    
                    row_style = "red" if status == "FAILED" else None
                    table.add_row(str(idx), time_str, temp_str, status, style=row_style)
                
                console.print(table)
        else:
            console.print("[red]Check failed![/red]")
    
    elif choice == "2":
        if args.interactive:
            console.print("\n[bold]Real-Time Monitoring Mode:[/bold]")
            console.print("1. 📡 SignalR WebSocket (True Real-Time)")
            console.print("2. 🔄 Smart Polling (10s intervals)") 
            console.print("3. ⏰ Standard Interactive (Manual refresh)")
            
            realtime_choice = Prompt.ask("Choose real-time mode", choices=["1", "2", "3"], default="1")
            
            if realtime_choice == "1":
                # TRUE REAL-TIME with SignalR
                monitor.start_signalr_monitor(employee_id, password, handle_signalr_attendance_update)
            elif realtime_choice == "2":
                monitor.real_time_monitor(employee_id, password, poll_interval=10)
            elif realtime_choice == "3":
                monitor.interactive_monitor(employee_id, password, args.interval)
        else:
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