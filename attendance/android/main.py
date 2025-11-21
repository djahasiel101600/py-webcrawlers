# main.py
import websocket
import requests
import json
import re
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

# Set up logging with Rich handler for mobile-friendly output
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
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
        self.should_reconnect = True
        self.callbacks = []
        self.message_id = 0
        self.verbose = verbose
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.last_message_time = time.time()
        self.connection_id = None
        self.connection_token = None
        self.employee_id = None
        self.password = None
        self.consecutive_failures = 0  # Track consecutive connection failures
        
    def add_callback(self, callback):
        """Add a callback function for attendance updates"""
        if callable(callback):
            self.callbacks.append(callback)
    
    def on_message(self, ws, message):
        """Fixed message handling for actual NIA SignalR format"""
        try:
            self.last_message_time = time.time()
            data = json.loads(message)
            
            # Show raw message for debugging
            # console.print(f"│ [dim]📨 SIGNALR: {json.dumps(data)[:150]}...[/dim]")
            
            if isinstance(data, dict):
                # Update connection ID
                if 'C' in data:
                    self.connection_id = data['C']
                    console.print(f"│ [green]🔗 Connection: {self.connection_id}[/green]")
                
                # Process methods - FIXED FOR ACTUAL FORMAT
                if 'M' in data and isinstance(data['M'], list):
                    for method in data['M']:
                        hub_name = method.get('H', 'Unknown')
                        method_type = method.get('M', 'Unknown')
                        method_args = method.get('A', [])
                        
                        console.print(f"│ [cyan]🎯 HUB: {hub_name} | METHOD: {method_type}[/cyan]")
                        
                        # FIXED: Handle "BioHub" hub with "update" method
                        if hub_name == "BioHub" and method_type == "update":
                            console.print(f"│ [bright_green]🚨 ATTENDANCE UPDATE DETECTED![/bright_green]")
                            
                            # The data might be in a different format
                            # Let's try to fetch fresh data when we get this signal
                            self._handle_biohub_update()
                            
            elif isinstance(data, list):
                console.print(f"│ [yellow]📦 ARRAY DATA: {json.dumps(data)[:100]}...[/yellow]")
                
        except json.JSONDecodeError:
            if self.verbose:
                console.print(f"│ [red]❌ Invalid JSON[/red]")

    def _handle_biohub_update(self):
        """Handle BioHub update signal - fetch fresh data"""
        console.print("│ [blue]🔄 BioHub signal received, processing update...[/blue]")
        
        # Since the SignalR message doesn't contain the actual data,
        # we need to notify callbacks to refresh their data
        for callback in self.callbacks:
            try:
                # Pass a special signal to indicate refresh needed
                callback({'type': 'refresh_signal', 'timestamp': datetime.now().isoformat()})
            except Exception as e:
                if self.verbose:
                    console.print(f"│ [red]⚠️  CALLBACK ERROR: {e}[/red]")   

    def on_error(self, ws, error):
        """Enhanced error handling with specific error types"""
        if self.verbose:
            console.print(f"│ [red]🚨 CONNECTION ERROR: {error}[/red]")
        
        self.is_connected = False
        self.consecutive_failures += 1
        
        # Check if this is a socket-related error
        socket_errors = ['socket', 'already', 'opened', 'closed', 'broken']
        is_socket_error = any(socket_err in str(error).lower() for socket_err in socket_errors)
        
        if is_socket_error:
            console.print("│ [yellow]⚠️  Socket error detected, will attempt cleanup...[/yellow]")
        
        # Smart reconnection logic
        if self.should_reconnect and self.reconnect_attempts < self.max_reconnect_attempts:
            if self.consecutive_failures >= 2 or is_socket_error:
                # If socket errors or multiple failures, try full re-authentication
                console.print("│ [yellow]⚠️  Attempting re-authentication...[/yellow]")
                self._schedule_reconnect(full_reauth=True)
            else:
                # First failure, try simple reconnect with cleanup
                self._schedule_reconnect()

    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket closure with reconnection logic"""
        console.print(f"│ [yellow]🔌 CONNECTION CLOSED: Code {close_status_code}[/yellow]")
        self.is_connected = False
        
        if (close_status_code == 1000 or 
            self.reconnect_attempts >= self.max_reconnect_attempts or
            not self.should_reconnect):
            return
            
        self._schedule_reconnect()
    
    def on_open(self, ws):
        """Enhanced connection opened handler"""
        console.print("│ [green]🔓 SIGNALR: Secure channel established[/green]")
        self.is_connected = True
        self.reconnect_attempts = 0
        self.consecutive_failures = 0  # Reset on successful connection
        self.last_message_time = time.time()
        self._send_join_message()
        self._start_keep_alive()
    
    def _schedule_reconnect(self, employee_id=None, password=None, full_reauth=False):
        """Enhanced reconnection with optional full re-authentication"""
        if not self.should_reconnect:
            return
            
        self.reconnect_attempts += 1
        delay = min(30, 2 ** self.reconnect_attempts)

        if full_reauth:
            console.print(f"│ [yellow]🔄 RE-AUTH: Full re-authentication in {delay}s (Attempt {self.reconnect_attempts}/10)[/yellow]")
        else:
            console.print(f"│ [yellow]🔄 RECONNECT: Attempt {self.reconnect_attempts}/10 in {delay}s[/yellow]")
        
        # Store credentials for re-authentication if provided
        if employee_id and password:
            self.employee_id = employee_id
            self.password = password
        
        threading.Timer(delay, self._reconnect, kwargs={'full_reauth': full_reauth}).start()

    def _reconnect(self, full_reauth=False):
        """Enhanced reconnect with proper socket cleanup"""
        if not self.should_reconnect or self.reconnect_attempts >= self.max_reconnect_attempts:
            console.print("│ [red]🚨 RECONNECT: Maximum attempts reached[/red]")
            return
        
        # Ensure the previous WebSocket is properly closed
        if self.ws and hasattr(self.ws, 'sock') and self.ws.sock:
            try:
                console.print("│ [dim]🔧 Cleaning up previous WebSocket connection...[/dim]")
                self.ws.close()
                # Give it a moment to close properly
                time.sleep(1)
            except Exception as e:
                if self.verbose:
                    console.print(f"│ [yellow]⚠️  Cleanup warning: {e}[/yellow]")
        
        if full_reauth and hasattr(self, 'employee_id') and hasattr(self, 'password'):
            console.print("│ [blue]🔄 RE-AUTH: Performing full re-authentication...[/blue]")
            # Trigger full re-authentication
            for callback in self.callbacks:
                try:
                    callback({'type': 'reauth_required', 'employee_id': self.employee_id, 'password': self.password})
                except Exception as e:
                    if self.verbose:
                        console.print(f"│ [red]⚠️  REAUTH CALLBACK ERROR: {e}[/red]")
        else:
            console.print("│ [blue]🔄 RECONNECT: Attempting to re-establish connection...[/blue]")
            # Use a small delay before reconnecting to ensure clean state
            time.sleep(2)
            self.connect(self.connection_token)

    def _start_keep_alive(self):
        """Start keep-alive monitoring"""
        def keep_alive_monitor():
            while self.is_connected and self.should_reconnect:
                time.sleep(30)
                
                if not self.is_connected:
                    break
                    
                idle_time = time.time() - self.last_message_time
                if idle_time > 120:
                    console.print("│ [yellow]⚠️  KEEP-ALIVE: Connection appears idle, forcing reconnect[/yellow]")
                    self.ws.close()
                    break
                    
                if idle_time > 60:
                    self._send_keep_alive()
        
        threading.Thread(target=keep_alive_monitor, daemon=True).start()
    
    def _send_keep_alive(self):
        """Send keep-alive message"""
        try:
            ping_message = {"H": "biohub", "M": "ping", "A": [], "I": self._get_next_message_id()}
            self._send_message(ping_message)
        except:
            pass
    
    def _handle_attendance_update(self, args):
        """Process real-time attendance updates"""
        if args:
            attendance_data = args[0] if isinstance(args, list) and len(args) > 0 else args
            
            console.print("│ [cyan]⚡ REAL-TIME: Processing biometric data...[/cyan]")
            
            for callback in self.callbacks:
                try:
                    callback(attendance_data)
                except Exception as e:
                    if self.verbose:
                        console.print(f"│ [red]⚠️  CALLBACK ERROR: {e}[/red]")
    
    def _send_join_message(self):
        """Send join message"""
        join_message = {
            "H": "BioHub",
            "M": "Join", 
            "A": [],
            "I": self._get_next_message_id()
        }
        self._send_message(join_message)
    
    def _send_message(self, message):
        """Send message through WebSocket"""
        if self.ws and self.is_connected:
            try:
                message_str = json.dumps(message)
                self.ws.send(message_str)
                return True
            except Exception as e:
                if self.verbose:
                    console.print(f"│ [red]⚠️  TRANSMISSION FAILED: {e}[/red]")
                return False
        return False
    
    def _get_next_message_id(self):
        """Get next message ID"""
        self.message_id += 1
        return self.message_id
    
    def connect(self, connection_token):
        """Connect to SignalR WebSocket with improved state management"""
        try:
            # Reset connection state
            self.is_connected = False
            self.connection_token = connection_token
            
            console.print("│ [blue]🌐 INITIATING: SignalR handshake...[/blue]")
            
            # Ensure any existing connection is closed
            if self.ws:
                try:
                    self.ws.close()
                except:
                    pass
                self.ws = None
            
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
            
            # Create new WebSocket connection
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
                try:
                    self.ws.run_forever(ping_interval=30, ping_timeout=10)
                except Exception as e:
                    if self.verbose:
                        console.print(f"│ [red]🚨 WebSocket thread error: {e}[/red]")
            
            thread = threading.Thread(target=run_websocket)
            thread.daemon = True
            thread.start()
            
            # Wait for connection with timeout
            for i in range(20):  # Increased timeout to 20 seconds
                if self.is_connected:
                    return True
                if not self.should_reconnect:
                    return False
                time.sleep(1)
            
            # If we get here, connection timed out
            console.print("│ [red]🚨 CONNECTION: Timeout waiting for connection[/red]")
            return False
            
        except Exception as e:
            console.print(f"│ [red]🚨 CONNECTION FAILED: {e}[/red]")
            return False
 
    def _build_websocket_url(self, connection_token):
        """Build WebSocket URL with connection token"""
        encoded_token = quote(connection_token)
        connection_data = quote('[{"name":"BioHub"}]')
        
        url = (f"wss://attendance.caraga.nia.gov.ph/signalr/connect"
               f"?transport=webSockets"
               f"&clientProtocol=2.1"
               f"&connectionToken={encoded_token}"
               f"&connectionData={connection_data}"
               f"&tid={random.randint(0, 10)}")
        
        return url
    
    def disconnect(self):
        """Disconnect WebSocket gracefully with proper cleanup"""
        console.print("│ [yellow]🔒 DISCONNECTING: Secure channel...[/yellow]")
        self.should_reconnect = False
        self.is_connected = False
        
        if self.ws:
            try:
                # Set a short timeout for graceful closure
                self.ws.close()
                # Wait a moment for the connection to close
                time.sleep(2)
            except Exception as e:
                if self.verbose:
                    console.print(f"│ [yellow]⚠️  Disconnect warning: {e}[/yellow]")
            finally:
                self.ws = None

class NIAAttendanceMonitor:
    def __init__(self, config=None):
        self.config = config or Config().load()
        self.base_url = self.config['base_url']
        self.auth_url = self.config['auth_url']
        self.session = requests.Session()
        self.state_file = os.path.expanduser('~/.nia_monitor_state.json')
        
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
        """Login to the NIA system"""
        try:
            console.print("│ [blue]🔐 AUTH: Accessing NIA portal...[/blue]")
            
            response = self.session.get(self.auth_url)
            
            token_match = re.search(r'name="__RequestVerificationToken".*?value="([^"]+)"', response.text)
            if not token_match:
                console.print("│ [red]🚨 AUTH: Security token not found[/red]")
                return False
            
            token = token_match.group(1)
            
            login_data = {
                'EmployeeId': employee_id,
                'Password': password,
                'RememberMe': 'false',
                '__RequestVerificationToken': token
            }
            
            console.print("│ [yellow]⏳ AUTH: Verifying credentials...[/yellow]")
            
            response = self.session.post(self.auth_url, data=login_data, allow_redirects=True)
            
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
        """Get attendance data via API"""
        if not year:
            year = datetime.now().year
        if not month:
            month = datetime.now().strftime("%B")
        
        url = f"{self.base_url}/Attendance/IndexData/{year}?month={month}&eid={employee_id}"
        
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
            
            records = [AttendanceRecord.from_api_data(record) for record in api_data.get('data', [])]
            
            return self._process_attendance_data(records, employee_id, api_data)
            
        except requests.exceptions.RequestException as e:
            console.print(f"│ [red]🚨 API ERROR: {e}[/red]")
            return None

    def _process_attendance_data(self, records, employee_id, api_data):
        """Process attendance data with change detection"""
        if records:
            changes = self.detect_changes(records)
            
            if self.config.get('enable_csv', False):
                self.save_as_csv(records, employee_id, changes)
        
        return {
            'records': records,
            'total_records': api_data.get('recordsTotal', 0),
            'timestamp': datetime.now().isoformat()
        }

    def save_as_csv(self, records, employee_id, changes):
        """Save attendance data as CSV"""
        if not self.config.get('enable_csv', False):
            return None
            
        try:
            filename = f"attendance_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                csvfile.write("# NIA Attendance Export (API)\n")
                csvfile.write(f"# Generated: {datetime.now().isoformat()}\n")
                csvfile.write(f"# Employee: {employee_id}\n")
                csvfile.write(f"# Records: {len(records)}\n")
                csvfile.write(f"# New Records: {len(changes['new_records'])}\n#\n")
                
                writer = csv.writer(csvfile)
                writer.writerow(['Date Time', 'Temperature', 'Employee ID', 'Employee Name', 'Machine Name', 'Status'])
                
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
        """Analyze attendance patterns"""
        try:
            if not attendance_data or 'records' not in attendance_data:
                return None
            
            records = attendance_data['records']
            
            if not records:
                console.print("│ [yellow]📊 ANALYSIS: No records to analyze[/yellow]")
                return None
            
            my_records = [r for r in records if r.employee_id == employee_id]
            failed_records = [r for r in my_records if r.status == "ACCESS_DENIED"]
            
            if not my_records:
                console.print("│ [yellow]📊 ANALYSIS: No personal records found[/yellow]")
                return None
            
            today = datetime.now().date()
            today_records = [r for r in my_records if r.date_time.date() == today]
            
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
        """Get SignalR connection token"""
        try:
            console.print("│ [blue]🔧 SIGNALR: Acquiring connection token...[/blue]")
            
            response = self.session.get(f"{self.base_url}/Attendance")
            
            if 'Set-Cookie' in response.headers:
                set_cookie = response.headers['Set-Cookie']
                
                token_patterns = [
                    r'connectionToken=([^;]+)',
                    r'SignalR\.ConnectionToken=([^;]+)',
                    r'__SignalRToken=([^;]+)',
                ]
                
                for pattern in token_patterns:
                    match = re.search(pattern, set_cookie)
                    if match:
                        token = match.group(1)
                        console.print("│ [green]✅ TOKEN: Acquired from headers[/green]")
                        return token
            
            console.print("│ [yellow]🔧 SIGNALR: Attempting negotiation protocol...[/yellow]")
            return self._try_signalr_negotiation()
            
        except Exception as e:
            console.print(f"│ [red]🚨 TOKEN ERROR: {e}[/red]")
            return None

    def _try_signalr_negotiation(self):
        """Try to negotiate with SignalR server"""
        try:
            negotiate_url = f"{self.base_url}/signalr/negotiate"
            
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
                
                if 'ConnectionToken' in negotiation_data:
                    token = negotiation_data['ConnectionToken']
                    console.print("│ [green]✅ TOKEN: Negotiation successful[/green]")
                    return token
                elif 'Url' in negotiation_data:
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
        
        panel = Panel(
            Align.center(table),
            title=f"🔒 {title}",
            title_align="center",
            border_style="bright_blue",
            width=66
        )
        
        return panel

    def _display_current_attendance_hacker(self, attendance_data, employee_id):
        """Display current day's attendance in hacker style"""
        analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
        
        if not analysis:
            console.print("│ [yellow]📭 STATUS: No analyzable data available[/yellow]")
            return
        
        today_records = analysis.get('today_details', [])
        
        summary_text = Text()
        summary_text.append("📊 TODAY'S ACTIVITY: ", style="bold")
        summary_text.append(f"{len(today_records)} records", style="green")
        summary_text.append(" | ", style="dim")
        summary_text.append(f"{analysis.get('failed_records', 0)} denied", style="red" if analysis.get('failed_records', 0) > 0 else "dim")
        
        console.print(Panel(
            summary_text,
            border_style="bright_blue",
            width=66
        ))
        
        if today_records:
            table_panel = self._create_hacker_table(today_records, "TODAY'S BIOMETRIC LOG")
            console.print(table_panel)
            
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

    # ==================== LIVE DISPLAY METHODS ====================

    def start_live_dashboard(self, employee_id, password, on_attendance_update, verbose=False):
        """True live dashboard with automatic updates"""
        console.print("\n" + "═" * 59)
        console.print(Align.center("🚀 NIA ATTENDANCE - LIVE DASHBOARD"))
        console.print(Align.center("📊 REAL-TIME UPDATES • AUTO-REFRESH"))
        console.print("═" * 59)
        
        if not self.login(employee_id, password):
            console.print("│ [red]🚨 ABORT: Authentication failed[/red]")
            return False
        
        last_records = []
        update_count = 0
        signalr_updates = 0
        
        def refresh_live_display():
            nonlocal update_count, last_records
            
            current_attendance = self.get_attendance_data(employee_id)
            if not current_attendance or 'records' not in current_attendance:
                return False
            
            current_records = current_attendance['records']
            
            new_records = []
            if last_records:
                current_hashes = [self._hash_record(r) for r in current_records]
                last_hashes = [self._hash_record(r) for r in last_records]
                new_records = [r for r in current_records if self._hash_record(r) not in last_hashes]
            
            last_records = current_records
            update_count += 1
            
            console.clear()
            
            console.print(Align.center(f"🚀 NIA ATTENDANCE - LIVE DASHBOARD • Update #{update_count}"))
            console.print("═" * 59)
            
            status_elements = []
            status_elements.append(f"🕒 {datetime.now().strftime('%H:%M:%S')}")
            status_elements.append(f"🔄 {update_count} updates")
            status_elements.append(f"📡 {signalr_updates} real-time")
            if new_records:
                status_elements.append(f"🆕 {len(new_records)} new")
            
            console.print(f"│ [cyan]{' | '.join(status_elements)}[/cyan]")
            console.print("─" * 59)
            
            self._display_current_attendance_hacker(current_attendance, employee_id)
            
            if new_records:
                console.print("│ [green]🎉 NEW RECORDS DETECTED:[/green]")
                for record in new_records[-3:]:
                    time_str = record.date_time.strftime('%H:%M:%S')
                    status_icon = "✅" if record.status == "ACCESS_GRANTED" else "❌"
                    console.print(f"│   {status_icon} {record.employee_name} at {time_str}")
            
            console.print("─" * 59)
            console.print("│ [dim]💡 Live updates active • Ctrl+C to stop[/dim]")
            
            return True
        
        def enhanced_attendance_update(attendance_data):
            nonlocal signalr_updates
            signalr_updates += 1
            
            console.print(f"│ [bright_green]🎯 LIVE: {attendance_data.get('Name', 'Unknown')} scanned at {datetime.now().strftime('%H:%M:%S')}[/bright_green]")
            refresh_live_display()
        
        console.print("│ [blue]📡 INIT: Starting live dashboard...[/blue]")
        refresh_live_display()
        
        connection_token = self.get_signalr_connection_token()
        signalr_monitor = None
        
        if connection_token:
            cookies_dict = {c.name: c.value for c in self.session.cookies}
            signalr_monitor = NIASignalRMonitor(self.base_url, cookies_dict, verbose=verbose)
            # In your start_signalr_monitor method, update the callback setup:
            signalr_monitor.add_callback(lambda data: handle_signalr_attendance_update(
                data, 
                monitor=self,  # Pass monitor instance for re-authentication
                employee_id=employee_id, 
                password=password
            ))
            
            if signalr_monitor.connect(connection_token):
                console.print("│ [green]✅ SIGNALR: Real-time feed active[/green]")
            else:
                console.print("│ [yellow]⚠️  SIGNALR: Using auto-refresh only[/yellow]")
                signalr_monitor = None
        else:
            console.print("│ [yellow]⚠️  SIGNALR: Using auto-refresh only[/yellow]")
        
        console.print("│ [dim]🔄 Starting auto-refresh every 30 seconds...[/dim]")
        
        try:
            last_auto_refresh = time.time()
            
            while True:
                current_time = time.time()
                
                if current_time - last_auto_refresh >= 30:
                    if signalr_monitor and signalr_monitor.is_connected:
                        last_auto_refresh = current_time
                    else:
                        refresh_live_display()
                        last_auto_refresh = current_time
                
                time.sleep(1)
                
        except KeyboardInterrupt:
            console.print("\n│ [yellow]🛑 LIVE DASHBOARD: Stopping...[/yellow]")
        
        finally:
            if signalr_monitor:
                signalr_monitor.disconnect()
        
        console.print("│ [green]✅ LIVE DASHBOARD: Stopped[/green]")
        return True

    def start_animated_live_display(self, employee_id, password, on_attendance_update, verbose=False):
        """Animated live display with visual indicators"""
        console.print("\n" + "═" * 59)
        console.print(Align.center("🌐 NIA ATTENDANCE - LIVE MONITOR"))
        console.print(Align.center("📡 REAL-TIME • ANIMATED • AUTO-UPDATING"))
        console.print("═" * 59)
        
        if not self.login(employee_id, password):
            return False
        
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spinner_index = 0
        last_update = time.time()
        update_count = 0
        
        def get_live_indicator():
            nonlocal spinner_index
            spinner_index = (spinner_index + 1) % len(spinner)
            elapsed = time.time() - last_update
            if elapsed < 5:
                return f"[green]{spinner[spinner_index]} LIVE[/green]"
            elif elapsed < 15:
                return f"[yellow]{spinner[spinner_index]} CONNECTING[/yellow]"
            else:
                return f"[red]{spinner[spinner_index]} OFFLINE[/red]"
        
        def refresh_animated_display():
            nonlocal last_update, update_count
            
            current_attendance = self.get_attendance_data(employee_id)
            if not current_attendance:
                return False
            
            console.clear()
            
            console.print(Align.center(f"🌐 NIA ATTENDANCE - LIVE MONITOR"))
            console.print(Align.center(f"{get_live_indicator()} • Update #{update_count}"))
            console.print("═" * 59)
            
            stats = [
                f"📅 {datetime.now().strftime('%Y-%m-%d')}",
                f"🕒 {datetime.now().strftime('%H:%M:%S')}", 
                f"🔄 {update_count}",
                f"👤 {employee_id}"
            ]
            console.print(f"│ [cyan]{' | '.join(stats)}[/cyan]")
            console.print("─" * 59)
            
            self._display_current_attendance_hacker(current_attendance, employee_id)
            
            console.print("─" * 59)
            elapsed = time.time() - last_update
            status = "EXCELLENT" if elapsed < 2 else "GOOD" if elapsed < 5 else "SLOW"
            console.print(f"│ [dim]📊 Connection: {status} | Last update: {elapsed:.1f}s ago[/dim]")
            console.print(f"│ [dim]💡 Auto-refresh: 30s | Real-time: ACTIVE | Ctrl+C to stop[/dim]")
            
            last_update = time.time()
            update_count += 1
            return True
        
        def animated_attendance_update(attendance_data):
            nonlocal last_update
            last_update = time.time()
            
            employee_name = attendance_data.get('Name', 'Unknown')
            status = "ACCESS_GRANTED" if attendance_data.get('AccessResult') == 1 else "ACCESS_DENIED"
            icon = "✅" if status == "ACCESS_GRANTED" else "❌"
            
            console.print(f"│ [bright_green]🎯 {icon} {employee_name} - {datetime.now().strftime('%H:%M:%S')}[/bright_green]")
            refresh_animated_display()
        
        refresh_animated_display()
        
        connection_token = self.get_signalr_connection_token()
        signalr_monitor = None
        
        if connection_token:
            cookies_dict = {c.name: c.value for c in self.session.cookies}
            signalr_monitor = NIASignalRMonitor(self.base_url, cookies_dict, verbose=verbose)
            # In your start_signalr_monitor method, update the callback setup:
            signalr_monitor.add_callback(lambda data: handle_signalr_attendance_update(
                data, 
                monitor=self,  # Pass monitor instance for re-authentication
                employee_id=employee_id, 
                password=password
            ))
            signalr_monitor.connect(connection_token)
        
        console.print("│ [green]🚀 LIVE DISPLAY: Active[/green]")
        
        try:
            while True:
                if time.time() - last_update > 30:
                    refresh_animated_display()
                
                time.sleep(0.5)
                
        except KeyboardInterrupt:
            console.print("\n│ [yellow]🛑 Stopping live display...[/yellow]")
        
        finally:
            if signalr_monitor:
                signalr_monitor.disconnect()
        
        console.print("│ [green]✅ Live display stopped[/green]")
        return True

    def start_live_stream(self, employee_id, password, on_attendance_update, verbose=False):
        """Minimalist live stream that shows only new events"""
        console.print("\n" + "═" * 59)
        console.print(Align.center("📡 NIA ATTENDANCE - LIVE STREAM"))
        console.print(Align.center("🎯 REAL-TIME EVENTS ONLY"))
        console.print("═" * 59)
        
        if not self.login(employee_id, password):
            return False
        
        current_data = self.get_attendance_data(employee_id)
        if current_data:
            known_records = set(self._hash_record(r) for r in current_data['records'])
        else:
            known_records = set()
        
        event_count = 0
        
        def handle_live_event(attendance_data):
            nonlocal event_count, known_records
            
            event_count += 1
            employee_name = attendance_data.get('Name', 'Unknown')
            date_time_str = attendance_data.get('DateTimeStamp', '')
            temperature = attendance_data.get('Temperature')
            status = "ACCESS_GRANTED" if attendance_data.get('AccessResult') == 1 else "ACCESS_DENIED"
            
            date_time = AttendanceRecord.parse_net_date(date_time_str)
            
            event_record = AttendanceRecord.from_api_data(attendance_data)
            record_hash = self._hash_record(event_record)
            
            if record_hash not in known_records:
                known_records.add(record_hash)
                
                console.print(f"│ [bright_cyan]🎯 EVENT #{event_count}[/bright_cyan]")
                console.print(f"│   👤 [bold]{employee_name}[/bold]")
                console.print(f"│   🕒 {date_time.strftime('%H:%M:%S')}")
                if temperature:
                    console.print(f"│   🌡️  {temperature}°C")
                console.print(f"│   🔐 [{'green' if status == 'ACCESS_GRANTED' else 'red'}]{status}[/{'green' if status == 'ACCESS_GRANTED' else 'red'}]")
                console.print(f"│   📍 {attendance_data.get('MachineName', 'Unknown')}")
                console.print("│ ──────────────────────────────────────────")
            
            if event_count % 10 == 0:
                console.print(f"│ [dim]📊 Stream active: {event_count} events received[/dim]")
        
        connection_token = self.get_signalr_connection_token()
        signalr_monitor = None
        
        if connection_token:
            cookies_dict = {c.name: c.value for c in self.session.cookies}
            signalr_monitor = NIASignalRMonitor(self.base_url, cookies_dict, verbose=verbose)
            # In your start_signalr_monitor method, update the callback setup:
            signalr_monitor.add_callback(lambda data: handle_signalr_attendance_update(
                data, 
                monitor=self,  # Pass monitor instance for re-authentication
                employee_id=employee_id, 
                password=password
            ))
            
            if signalr_monitor.connect(connection_token):
                console.print("│ [green]✅ LIVE STREAM: Started[/green]")
                console.print("│ [dim]💡 Waiting for real-time events...[/dim]")
                console.print("│ [dim]💡 Press Ctrl+C to stop stream[/dim]")
                console.print("─" * 59)
            else:
                console.print("│ [red]❌ LIVE STREAM: Failed to connect[/red]")
                return False
        else:
            console.print("│ [red]❌ LIVE STREAM: No connection token[/red]")
            return False
        
        try:
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            console.print("\n│ [yellow]🛑 LIVE STREAM: Stopping...[/yellow]")
        
        finally:
            if signalr_monitor:
                signalr_monitor.disconnect()
        
        console.print(f"│ [green]✅ LIVE STREAM: Ended with {event_count} events[/green]")
        return True

    def start_signalr_monitor(self, employee_id, password, on_attendance_update, verbose=False):
        """ULTRA-SIMPLE version that definitely works"""
        console.print("\n" + "═" * 59)
        console.print(Align.center("🚀 NIA ATTENDANCE MONITOR - SIMPLE MODE"))
        console.print("═" * 59)
        
        if not self.login(employee_id, password):
            console.print("│ [red]🚨 ABORT: Authentication failed[/red]")
            return False
        
        def refresh_display():
            current_attendance = self.get_attendance_data(employee_id)
            if current_attendance:
                console.clear()
                self._display_current_attendance_hacker(current_attendance, employee_id)
                return True
            return False
        
        # Initial display
        refresh_display()
        
        # SignalR setup
        connection_token = self.get_signalr_connection_token()
        signalr_monitor = None
        if connection_token:
            cookies_dict = {c.name: c.value for c in self.session.cookies}
            signalr_monitor = NIASignalRMonitor(self.base_url, cookies_dict, verbose=verbose)
            # In your start_signalr_monitor method, update the callback setup:
            signalr_monitor.add_callback(lambda data: handle_signalr_attendance_update(
                data, 
                monitor=self,  # Pass monitor instance for re-authentication
                employee_id=employee_id, 
                password=password
            ))
            signalr_monitor.connect(connection_token)
        
        console.print("│ [cyan]💡 Commands: R=Refresh C=Status L=Test Q=Quit[/cyan]")
        console.print("─" * 59)
        
        try:
            while True:
                # SIMPLE INPUT - This should work everywhere
                try:
                    user_input = input("│ Command (R/C/L/Q): ").strip().lower()
                    
                    if user_input == 'q':
                        break
                    elif user_input == 'r':
                        console.print("│ [yellow]🔄 Refreshing data...[/yellow]")
                        refresh_display()
                        console.print("│ [cyan]💡 Commands: R=Refresh C=Status L=Test Q=Quit[/cyan]")
                        console.print("─" * 59)
                    elif user_input == 'c':
                        console.print("│ [blue]🔍 Connection Status:[/blue]")
                        if signalr_monitor:
                            status = "✅ Connected" if signalr_monitor.is_connected else "❌ Disconnected"
                            # console.print(f"│   SignalR: {status}")
                        else:
                            console.print("│   SignalR: ❌ Not available")
                        console.print("│   API: ✅ Active")
                    elif user_input == 'l':
                        console.print("│ [cyan]🧪 Live test event sent[/cyan]")
                        test_data = {
                            'Name': 'TEST USER',
                            'DateTimeStamp': f'/Date({int(time.time()*1000)})/',
                            'Temperature': 36.5,
                            'AccessResult': 1
                        }
                        on_attendance_update(test_data)
                    else:
                        console.print("│ [yellow]⚠️  Use R, C, L, or Q[/yellow]")
                        
                except KeyboardInterrupt:
                    break
                except EOFError:
                    # Handle cases where input might not be available
                    time.sleep(5)
                    continue
                    
        except KeyboardInterrupt:
            pass
        finally:
            if signalr_monitor:
                signalr_monitor.disconnect()
        
        console.print("│ [green]✅ Monitor stopped[/green]")
        return True
    def real_time_monitor(self, employee_id, password, poll_interval=10):
        """Real-time monitoring with frequent API polls"""
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
                    
                    console.clear()
                    console.print(Align.center(f"🔍 LIVE MONITOR - SCAN #{check_count}"))
                    console.print("─" * 59)
                    
                    console.print(f"│ [dim]🕒 LAST SCAN: {datetime.now().strftime('%H:%M:%S')}[/dim]")
                    
                    if analysis and analysis.get('today_details'):
                        today_records = analysis['today_details']
                        
                        if current_count > last_records_count and last_records_count > 0:
                            new_records = current_count - last_records_count
                            console.print(Panel(
                                Align.center(f"🚨 {new_records} NEW BIOMETRIC ENTRIES DETECTED!"),
                                border_style="bright_green",
                                width=66
                            ))
                        
                        last_records_count = current_count
                        
                        table_panel = self._create_hacker_table(today_records, "LIVE BIOMETRIC FEED")
                        console.print(table_panel)
                        
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
                
                console.print("\n│ [dim]💡 CONTROLS: Press Q to terminate monitoring[/dim]")
                start_time = time.time()

                while time.time() - start_time < poll_interval:
                    remaining = poll_interval - int(time.time() - start_time)
                    if remaining <= 0:
                        break
                        
                    console.print(f"│ [cyan]⏳ NEXT SCAN IN {remaining:02d}s[/cyan]", end="\r")
                    
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

                console.print()
                    
        except KeyboardInterrupt:
            console.print("\n│ [yellow]⚠️  USER: Termination signal received[/yellow]")
        
        console.print("│ [green]✅ SYSTEM: Polling monitor terminated[/green]")

    def interactive_monitor(self, employee_id, password, interval_seconds=300):
        """Interactive monitoring with API"""
        console.print("\n" + "═" * 59)
        console.print(Align.center("🚀 NIA ATTENDANCE MONITOR - INTERACTIVE MODE"))
        console.print("═" * 59)
        
        if not self.login(employee_id, password):
            console.print("│ [red]Login failed![/red]")
            return
        
        check_count = 0
        
        while True:
            console.clear()
            console.print(Align.center(f"🔍 INTERACTIVE MONITOR - CHECK #{check_count + 1}"))
            console.print("─" * 59)
            
            console.print("│ [yellow]🔄 Fetching attendance data...[/yellow]")
            
            attendance_data = self.get_attendance_data(employee_id)
            
            if attendance_data:
                check_count += 1
                analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
                
                if analysis and analysis.get('today_details'):
                    table = Table(show_header=True, header_style="bold cyan", width=60)
                    table.add_column("#", justify="right", style="white", width=4)
                    table.add_column("Time", style="green", width=15)
                    table.add_column("Temp", style="yellow", width=6)
                    table.add_column("Status", style="magenta", width=8)
                    
                    for idx, record in enumerate(analysis['today_details'], start=1):
                        time_str = record.date_time.strftime("%H:%M:%S")
                        temp_str = f"{record.temperature:.1f}" if record.temperature else "N/A"
                        status = record.status
                        
                        row_style = "red" if status == "ACCESS_DENIED" else None
                        table.add_row(str(idx), time_str, temp_str, status, style=row_style)
                    
                    console.print(table)
                    console.print(f"│ [green]✅ {len(analysis['today_details'])} records today[/green]")
                else:
                    console.print("│ [yellow]📭 No records for today[/yellow]")
            else:
                console.print("│ [red]❌ Failed to fetch data[/red]")
            
            console.print(f"│ [dim]🕒 Check #{check_count} at {datetime.now().strftime('%H:%M:%S')}[/dim]")
            console.print("│ [bold]R[/bold]efresh [bold]S[/bold]ave [bold]Q[/bold]uit")
            
            try:
                key = console.input("\n│ Command: ").lower().strip()
                
                if key == 'q':
                    break
                elif key == 's':
                    if attendance_data and self.config.get('enable_csv', False):
                        full_data = self.get_attendance_data(employee_id, length=100)
                        if full_data:
                            console.print("│ [green]✅ Data saved[/green]")
                        console.input("│ Press Enter to continue...")
                    else:
                        console.print("│ [yellow]⚠️  CSV export disabled[/yellow]")
                        console.input("│ Press Enter to continue...")
                elif key == 'r':
                    continue
                else:
                    console.print("│ [yellow]⚠️  Use R, S, or Q[/yellow]")
                    console.input("│ Press Enter to continue...")
                    
            except KeyboardInterrupt:
                console.print("\n│ [yellow]🛑 Stopping monitor...[/yellow]")
                break
        
        console.print("│ [green]✅ Interactive monitor stopped[/green]")

    def one_time_check(self, employee_id, password):
        """Single check with API"""
        if not self.login(employee_id, password):
            return None
            
        attendance_data = self.get_attendance_data(employee_id)
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

    def monitor_attendance(self, employee_id, password, interval_seconds=300, max_checks=None, interactive=False):
        """Monitor attendance with optional interactive mode"""
        if interactive:
            return self.interactive_monitor(employee_id, password, interval_seconds)
        
        logging.info(f"Monitoring every {interval_seconds}s")
        checks = 0
        
        if not self.login(employee_id, password):
            return
        
        try:
            while True:
                attendance_data = self.get_attendance_data(employee_id)
                
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
        """Save attendance data to JSON backup"""
        try:
            filename = f"nia_attendance_backup_{datetime.now().strftime('%Y%m')}.json"
            
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = []
            
            data.append(attendance_data)
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            console.print(f"│ [green]✅ Saved to {filename}[/green]")
            
        except Exception as e:
            console.print(f"│ [red]⚠️  Save error: {e}[/red]")

    def reauthenticate_and_restart_monitor(self, employee_id, password, on_attendance_update, verbose=False):
        """Full re-authentication and monitor restart"""
        console.print("│ [yellow]🔄 RE-AUTH: Starting full re-authentication process...[/yellow]")
        
        # Disconnect existing monitor
        if hasattr(self, 'signalr_monitor') and self.signalr_monitor:
            self.signalr_monitor.disconnect()
        
        # Clear session to ensure fresh login
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'X-Requested-With': 'XMLHttpRequest'
        })
        
        # Perform fresh login
        console.print("│ [blue]🔐 RE-AUTH: Performing fresh login...[/blue]")
        if not self.login(employee_id, password):
            console.print("│ [red]🚨 RE-AUTH: Login failed![/red]")
            return False
        
        # Get new connection token
        console.print("│ [blue]🔧 RE-AUTH: Acquiring new connection token...[/blue]")
        connection_token = self.get_signalr_connection_token()
        
        if not connection_token:
            console.print("│ [red]🚨 RE-AUTH: Failed to get new connection token[/red]")
            return False
        
        # Create new SignalR monitor with fresh session
        cookies_dict = {c.name: c.value for c in self.session.cookies}
        self.signalr_monitor = NIASignalRMonitor(self.base_url, cookies_dict, verbose=verbose)
        # In your start_signalr_monitor method, update the callback setup:
        self.signalr_monitor.add_callback(lambda data: handle_signalr_attendance_update(
            data, 
            monitor=self,  # Pass monitor instance for re-authentication
            employee_id=employee_id, 
            password=password
        ))
        
        # Store credentials for future re-authentication
        self.signalr_monitor.employee_id = employee_id
        self.signalr_monitor.password = password
        
        console.print("│ [blue]🌐 RE-AUTH: Establishing new real-time connection...[/blue]")
        
        if self.signalr_monitor.connect(connection_token):
            console.print("│ [green]✅ RE-AUTH: Successfully reconnected with fresh session![/green]")
            return True
        else:
            console.print("│ [red]🚨 RE-AUTH: Failed to establish new connection[/red]")
            return False

def handle_signalr_attendance_update(attendance_data, monitor=None, employee_id=None, password=None):
    """Enhanced callback that handles re-authentication requests"""
    
    if isinstance(attendance_data, dict):
        # Handle re-authentication requests
        if attendance_data.get('type') == 'reauth_required':
            console.print()
            console.print("═" * 70)
            console.print(Align.center("🔄 RE-AUTHENTICATION REQUESTED"))
            console.print("─" * 70)
            console.print(f"│ [yellow]⚠️  Connection issues detected, re-authenticating...[/yellow]")
            
            if monitor and employee_id and password:
                success = monitor.reauthenticate_and_restart_monitor(employee_id, password, handle_signalr_attendance_update)
                if success:
                    console.print(f"│ [green]✅ Re-authentication successful![/green]")
                else:
                    console.print(f"│ [red]❌ Re-authentication failed[/red]")
            else:
                console.print(f"│ [red]❌ Cannot re-authenticate: missing credentials[/red]")
            
            console.print("─" * 70)
            return
        
        # Handle refresh signals (existing functionality)
        elif attendance_data.get('type') == 'refresh_signal':
            console.print()
            console.print("═" * 70)
            console.print(Align.center("🔄 BIOHUB REFRESH SIGNAL"))
            console.print("─" * 70)
            console.print(f"│ [bright_green]🎯 ATTENDANCE: New scan detected![/bright_green]")
            console.print(f"│ [dim]📡 Signal received at: {datetime.now().strftime('%H:%M:%S')}[/dim]")
            console.print("│ [yellow]💡 The system should refresh automatically...[/yellow]")
            console.print("─" * 70)
            return
    
    if isinstance(attendance_data, dict):
        employee_name = attendance_data.get('Name', 'UNKNOWN_USER')
        date_time_str = attendance_data.get('DateTimeStamp', '')
        temperature = attendance_data.get('Temperature')
        status = "ACCESS_GRANTED" if attendance_data.get('AccessResult') == 1 else "ACCESS_DENIED"
        
        date_time = AttendanceRecord.parse_net_date(date_time_str)
        
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
    # Show startup banner
    console.print("\n")
    console.print(Align.center("┌─────────────────────────────────────────────────────┐"))
    console.print(Align.center("│              NIA ATTENDANCE MONITOR v3.0            │"))
    console.print(Align.center("│               [red]SECURE BIOMETRIC SURVEILLANCE[/red]            │"))
    console.print(Align.center("└─────────────────────────────────────────────────────┘"))
    console.print()
    
    config = Config().load()
    
    parser = argparse.ArgumentParser(
        description="NIA Attendance Monitor - Live Display Version",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--mode',
        choices=['once', 'monitor', 'config', 'live', 'animated', 'stream'],
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
        console.print("│ [green]✅ Config updated[/green]")
        return

    monitor = NIAAttendanceMonitor(config=config)
    
    # Get credentials
    employee_id = (args.employee_id or 
                   os.environ.get('NIA_EMPLOYEE_ID') or 
                   config.get('employee_id'))
    if not employee_id:
        employee_id = Prompt.ask("│ Employee ID")
        
    password = (args.password or 
                os.environ.get('NIA_PASSWORD') or 
                config.get('password'))
    if not password:
        console.print("│ Password (hidden):")
        password = getpass.getpass("│ ")
    
    if args.mode:
        mode_map = {
            'once': '1', 
            'monitor': '2', 
            'live': '3',
            'animated': '4',
            'stream': '5',
            'config': '6'
        }
        choice = mode_map.get(args.mode, '1')
    else:
        console.print("\n[bold bright_white]OPERATION MODES:[/bold bright_white]")
        console.print("[bright_cyan]1.[/bright_cyan] 🔍 Quick System Scan")
        console.print("[bright_cyan]2.[/bright_cyan] 📡 Real-time Surveillance") 
        console.print("[bright_cyan]3.[/bright_cyan] 🚀 Live Dashboard (Recommended)")
        console.print("[bright_cyan]4.[/bright_cyan] 🌐 Animated Live Display")
        console.print("[bright_cyan]5.[/bright_cyan] 📡 Live Event Stream")
        console.print("[bright_cyan]6.[/bright_cyan] ⚙️  System Configuration")
        
        choice = Prompt.ask(
            "\n[bright_white]SELECT OPERATION[/bright_white]", 
            choices=["1", "2", "3", "4", "5", "6"], 
            default="3"
        )
    
    if choice == "1":
        result = monitor.one_time_check(employee_id, password)
        if result:
            console.print("═" * 59)
            console.print(Align.center("✅ CHECK COMPLETE"))
            console.print("─" * 59)
            
            analysis = result['analysis']
            console.print(f"│ Your records: {analysis.get('total_records', 0)}")
            console.print(f"│ Today: {analysis.get('today_records', 0)}")
            
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
                    
                    row_style = "red" if status == "ACCESS_DENIED" else None
                    table.add_row(str(idx), time_str, temp_str, status, style=row_style)
                
                console.print(table)
        else:
            console.print("│ [red]❌ Check failed![/red]")
    
    elif choice == "2":
        if args.interactive:
            console.print("\n[bold]Real-Time Monitoring Mode:[/bold]")
            console.print("1. 📡 SignalR WebSocket (True Real-Time)")
            console.print("2. 🔄 Smart Polling (10s intervals)") 
            console.print("3. ⏰ Standard Interactive (Manual refresh)")
            
            realtime_choice = Prompt.ask("Choose real-time mode", choices=["1", "2", "3"], default="1")
            
            if realtime_choice == "1":
                monitor.start_signalr_monitor(employee_id, password, handle_signalr_attendance_update, args.verbose)
            elif realtime_choice == "2":
                monitor.real_time_monitor(employee_id, password, poll_interval=10)
            elif realtime_choice == "3":
                monitor.interactive_monitor(employee_id, password, args.interval)
        else:
            monitor.start_signalr_monitor(employee_id, password, handle_signalr_attendance_update, args.verbose)
    
    elif choice == "3":
        monitor.start_live_dashboard(employee_id, password, handle_signalr_attendance_update, args.verbose)
    
    elif choice == "4":
        monitor.start_animated_live_display(employee_id, password, handle_signalr_attendance_update, args.verbose)
    
    elif choice == "5":
        monitor.start_live_stream(employee_id, password, handle_signalr_attendance_update, args.verbose)
    
    elif choice == "6":
        console.print("│ Configuration:")
        console.print_json(json.dumps(config, indent=2))

if __name__ == "__main__":
    main()