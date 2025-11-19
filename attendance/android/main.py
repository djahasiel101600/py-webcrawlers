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
                logging.warning(f"Config load error: {e}")
        return self.defaults.copy()
    
    def save(self, config_data):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False)
        except Exception as e:
            logging.error(f"Save config error: {e}")

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
        status = "SUCCESS" if api_record['AccessResult'] == 1 else "FAILED"
        
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
        """Handle incoming WebSocket messages quietly"""
        try:
            data = json.loads(message)
            
            # Only handle method invocations, don't log connection stuff
            if isinstance(data, dict) and 'M' in data:
                methods = data.get('M', [])
                for method in methods:
                    method_name = method.get('H')
                    method_type = method.get('M')
                    method_args = method.get('A', [])
                    
                    # Only process attendance updates
                    if method_name == "biohub" and method_type in ["attendanceUpdate", "newRecord"]:
                        self._handle_attendance_update(method_args)
                        
        except json.JSONDecodeError:
            pass  # Silence JSON errors
    
    def on_error(self, ws, error):
        """Handle WebSocket errors quietly"""
        if self.verbose:  # Only log errors in verbose mode
            logging.error(f"WebSocket error: {error}")
        self.is_connected = False
    
    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket closure quietly"""
        self.is_connected = False
    
    def on_open(self, ws):
        """Handle WebSocket connection opened quietly"""
        self.is_connected = True
        self._send_join_message()  # Send join but don't log it
    
    def _handle_attendance_update(self, args):
        """Process real-time attendance updates quietly"""
        if args:
            attendance_data = args[0] if isinstance(args, list) and len(args) > 0 else args
            
            # Notify callbacks without logging
            for callback in self.callbacks:
                try:
                    callback(attendance_data)
                except Exception:
                    pass  # Silence callback errors
    
    def _send_join_message(self):
        """Send join message quietly"""
        join_message = {
            "H": "biohub",
            "M": "Join", 
            "A": [],
            "I": self._get_next_message_id()
        }
        self._send_message(join_message)
    
    def _send_message(self, message):
        """Send message through WebSocket quietly"""
        if self.ws and self.is_connected:
            try:
                message_str = json.dumps(message)
                self.ws.send(message_str)
            except Exception:
                pass  # Silence send errors
    
    def _get_next_message_id(self):
        """Get next message ID"""
        self.message_id += 1
        return self.message_id
    
    def connect(self, connection_token):
        """Connect to SignalR WebSocket quietly"""
        try:
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
            
            # Wait for connection
            for i in range(15):
                if self.is_connected:
                    return True
                time.sleep(1)
            
            return False
            
        except Exception:
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

    def login(self, employee_id, password):
        """Login to the NIA system quietly"""
        try:
            # Get login page for token
            response = self.session.get(self.auth_url)
            
            # Extract verification token
            token_match = re.search(r'name="__RequestVerificationToken".*?value="([^"]+)"', response.text)
            if not token_match:
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
            response = self.session.post(self.auth_url, data=login_data, allow_redirects=True)
            
            # Check if login was successful
            return response.status_code == 200 and employee_id in response.text
            
        except Exception:
            return False
    
    def get_attendance_data(self, employee_id, year=None, month=None, length=50):
        """Get attendance data via API"""
        if not year:
            year = datetime.now().year
        if not month:
            month = datetime.now().strftime("%B")
        
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
            
            # Convert to our record format
            records = [AttendanceRecord.from_api_data(record) for record in api_data.get('data', [])]
            
            # Process changes and optional CSV saving
            return self._process_attendance_data(records, employee_id, api_data)
            
        except requests.exceptions.RequestException as e:
            logging.error(f"API error: {e}")
            return None

    def _process_attendance_data(self, records, employee_id, api_data):
        """Process attendance data with change detection"""
        if records:
            changes = self.detect_changes(records)
            if changes['changes_detected']:
                logging.info(f"New: {len(changes['new_records'])}")
            
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

            logging.info(f"Saved {filename}")
            return filename

        except Exception as e:
            logging.error(f"CSV error: {e}")
            return None
    
    def analyze_attendance_patterns(self, attendance_data, employee_id):
        """Analyze attendance patterns - mobile optimized"""
        try:
            if not attendance_data or 'records' not in attendance_data:
                return None
            
            records = attendance_data['records']
            
            if not records:
                return None
            
            # Filter records for this employee
            my_records = [r for r in records if r.employee_id == employee_id]
            failed_records = [r for r in my_records if r.status == "FAILED"]
            
            if not my_records:
                return None
            
            # Parse today's records
            today = datetime.now().date()
            today_records = [r for r in my_records if r.date_time.date() == today]
            
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

    def get_signalr_connection_token(self):
        """Get SignalR connection token using proper cookie authentication"""
        try:
            # The token might be in the cookies or headers, not necessarily in the HTML
            console = Console()
            
            # Check what cookies we have
            cookies = self.session.cookies
            console.print(f"[dim]Available cookies: {list(cookies.keys())}[/dim]")
            
            # Try to get the attendance page which should set the proper cookies
            response = self.session.get(f"{self.base_url}/Attendance")
            
            # Save for debugging
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(response.text)
            
            # Method 1: Check if token is in Set-Cookie header
            if 'Set-Cookie' in response.headers:
                set_cookie = response.headers['Set-Cookie']
                console.print(f"[dim]Set-Cookie header: {set_cookie[:100]}...[/dim]")
                
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
                        console.print(f"[green]✅ Found token in Set-Cookie: {token[:50]}...[/green]")
                        return token
            
            # Method 2: The token might be generated client-side, so we need to simulate the SignalR negotiation
            console.print("[yellow]🔍 Trying SignalR negotiation...[/yellow]")
            return self._try_signalr_negotiation()
            
        except Exception as e:
            logging.error(f"❌ Error getting SignalR token: {e}")
            return None

    def _try_signalr_negotiation(self):
        """Try to negotiate with SignalR server to get connection token"""
        try:
            console = Console()
            
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
            
            console.print(f"[dim]Negotiating with: {negotiate_url}[/dim]")
            
            response = self.session.get(negotiate_url, params=params, headers=headers)
            
            if response.status_code == 200:
                negotiation_data = response.json()
                console.print(f"[dim]Negotiation response: {negotiation_data}[/dim]")
                
                # The connection token should be in the response
                if 'ConnectionToken' in negotiation_data:
                    token = negotiation_data['ConnectionToken']
                    console.print(f"[green]✅ Got token from negotiation: {token[:50]}...[/green]")
                    return token
                elif 'Url' in negotiation_data:
                    # Some SignalR setups return a URL with the token
                    url = negotiation_data['Url']
                    token_match = re.search(r'connectionToken=([^&]+)', url)
                    if token_match:
                        token = token_match.group(1)
                        console.print(f"[green]✅ Extracted token from URL: {token[:50]}...[/green]")
                        return token
            else:
                console.print(f"[red]❌ Negotiation failed: {response.status_code}[/red]")
                console.print(f"[dim]Response: {response.text[:200]}...[/dim]")
            
            return None
            
        except Exception as e:
            logging.error(f"❌ SignalR negotiation failed: {e}")
            return None

    def start_signalr_monitor(self, employee_id, password, on_attendance_update, verbose=False):
        """Start real-time SignalR WebSocket monitoring - optimized for mobile"""
        console = Console()
        
        # First, login to get session cookies
        if not self.login(employee_id, password):
            console.print("[red]Login failed[/red]")
            return False
        
        # Get current attendance data to display initially
        console.print("[yellow]Loading attendance...[/yellow]")
        current_attendance = self.get_attendance_data(employee_id)
        
        # Display current attendance
        if current_attendance:
            self._display_current_attendance_mobile(console, current_attendance, employee_id)
        else:
            console.print("[red]No attendance data[/red]")
        
        # Get connection token
        connection_token = self.get_signalr_connection_token()
        
        if not connection_token:
            console.print("[red]No SignalR token[/red]")
            console.print("[yellow]Using polling mode...[/yellow]")
            return self.real_time_monitor(employee_id, password)
        
        # Get cookies
        cookies_dict = {c.name: c.value for c in self.session.cookies}
        
        # Create and connect SignalR monitor
        signalr_monitor = NIASignalRMonitor(self.base_url, cookies_dict, verbose=verbose)
        signalr_monitor.add_callback(on_attendance_update)
        
        console.print("[green]Connecting...[/green]")
        
        if signalr_monitor.connect(connection_token):
            console.print("[green]✓ Real-Time Active[/green]")
            console.print("[dim]Listening for updates...[/dim]")
            console.print("[dim]Ctrl+C to stop[/dim]")
            
            try:
                # Keep main thread alive
                while signalr_monitor.is_connected:
                    time.sleep(1)
                        
            except KeyboardInterrupt:
                console.print("\n[yellow]Stopping...[/yellow]")
                
            finally:
                signalr_monitor.disconnect()
        
        else:
            console.print("[red]SignalR failed[/red]")
            console.print("[yellow]Using polling...[/yellow]")
            return self.real_time_monitor(employee_id, password)
        
        console.print("[green]Stopped[/green]")
        return True

    def _display_current_attendance_mobile(self, console, attendance_data, employee_id):
        """Display current day's attendance optimized for mobile"""
        analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
        
        if not analysis:
            console.print("[yellow]No data today[/yellow]")
            return
        
        today_records = analysis.get('today_details', [])
        
        console.rule(f"[blue]Today ({datetime.now().strftime('%m/%d')})[/blue]")
        
        if today_records:
            # Mobile-optimized compact table
            table = Table(show_header=True, header_style="bold", width=50, box=None)
            table.add_column("#", justify="right", width=3)
            table.add_column("Time", style="green", width=8)
            table.add_column("Temp", style="yellow", width=5)
            table.add_column("Status", width=8)
            
            for idx, record in enumerate(today_records, start=1):
                time_str = record.date_time.strftime("%H:%M")
                temp_str = f"{record.temperature:.1f}" if record.temperature else "-"
                status = "✅" if record.status == "SUCCESS" else "❌"
                
                table.add_row(str(idx), time_str, temp_str, status)
            
            console.print(table)
            
            # Compact summary
            console.print(f"[dim]Records: {len(today_records)}", end="")
            if analysis.get('failed_records', 0) > 0:
                console.print(f" | Failed: {analysis.get('failed_records', 0)}", end="")
            
            # Quick status
            if len(today_records) == 0:
                console.print(" | 🚨 No records")
            elif len(today_records) == 1:
                console.print(" | ⏳ Need Time Out")
            elif len(today_records) % 2 == 0:
                console.print(" | ✅ Complete")
            else:
                console.print(" | 🚨 Check Time Out")
                
        else:
            console.print("[yellow]No records today[/yellow]")
        
        console.print("[dim]Waiting for updates...[/dim]")
    def _display_current_attendance(self, console, attendance_data, employee_id):
        """Display current day's attendance in a clean table"""
        analysis = self.analyze_attendance_patterns(attendance_data, employee_id)
        
        if not analysis:
            console.print("[yellow]📭 No attendance data available for today[/yellow]")
            return
        
        today_records = analysis.get('today_details', [])
        total_records = analysis.get('total_records', 0)
        
        console.rule(f"[blue]📊 TODAY'S ATTENDANCE ({datetime.now().strftime('%Y-%m-%d')})[/blue]")
        
        if today_records:
            # Create a clean table for today's records
            table = Table(show_header=True, header_style="bold cyan", width=70)
            table.add_column("#", justify="right", style="white", width=4)
            table.add_column("Time", style="green", width=12)
            table.add_column("Temperature", style="yellow", width=10)
            table.add_column("Machine", style="blue", width=20)
            table.add_column("Status", style="magenta", width=10)
            
            for idx, record in enumerate(today_records, start=1):
                time_str = record.date_time.strftime("%H:%M:%S")
                temp_str = f"{record.temperature:.1f}°C" if record.temperature else "N/A"
                machine = record.machine_name[:18] + "..." if len(record.machine_name) > 18 else record.machine_name
                status = record.status
                
                # Color coding for status
                if status == "FAILED":
                    row_style = "red"
                    status_display = "❌ FAILED"
                else:
                    row_style = "green"
                    status_display = "✅ SUCCESS"
                
                table.add_row(str(idx), time_str, temp_str, machine, status_display, style=row_style)
            
            console.print(table)
            
            # Show summary
            console.print(f"\n[bold]Summary:[/bold]")
            console.print(f"  📈 Today's records: {len(today_records)}")
            console.print(f"  📊 Total your records: {total_records}")
            console.print(f"  ⚠️  Failed records: {analysis.get('failed_records', 0)}")
            
            # Attendance status analysis
            if len(today_records) == 0:
                console.print(f"  🚨 No attendance recorded today")
            elif len(today_records) == 1:
                console.print(f"  ⏳ Only Time In recorded - waiting for Time Out")
            elif len(today_records) % 2 == 0:
                pairs = len(today_records) // 2
                console.print(f"  ✅ Complete pairs: {pairs} (Time In/Out)")
            else:
                console.print(f"  🚨 Odd number of records - check for missing Time Out")
                
        else:
            console.print("[yellow]📭 No attendance records for today[/yellow]")
            console.print(f"[dim]Total records in system: {analysis.get('total_all_records', 0)}[/dim]")
        
        console.rule("[dim]Waiting for real-time updates...[/dim]")   
    
    def real_time_monitor(self, employee_id, password, poll_interval=10):
        """Real-time monitoring with frequent API polls"""
        console = Console()
        
        console.print("[green]🚀 Real-Time Monitor[/green]")
        console.print(f"[dim]Polling every {poll_interval}s | Q=Quit[/dim]")
        
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
                    console.clear()
                    console.rule(f"[blue]Real-Time Monitor [#{check_count}][/blue]")
                    
                    # Show real-time status
                    console.print(f"[dim]Last check: {datetime.now().strftime('%H:%M:%S')}[/dim]")
                    
                    if analysis and analysis.get('today_details'):
                        today_records = analysis['today_details']
                        
                        # Real-time alerts for new records
                        if current_count > last_records_count and last_records_count > 0:
                            new_records = current_count - last_records_count
                            console.print(f"[green]🚨 NEW: {new_records} record(s) added![/green]")
                        
                        last_records_count = current_count
                        
                        # Display today's records
                        table = Table(show_header=True, header_style="bold cyan", width=70)
                        table.add_column("#", justify="right", width=4)
                        table.add_column("Time", style="green", width=12)
                        table.add_column("Temp", style="yellow", width=8)
                        table.add_column("Status", style="magenta", width=10)
                        table.add_column("Type", style="blue", width=8)
                        
                        for idx, record in enumerate(today_records, start=1):
                            time_str = record.date_time.strftime("%H:%M")
                            temp_str = f"{record.temperature:.1f}°" if record.temperature else "N/A"
                            status = record.status
                            
                            # Infer record type based on position
                            record_type = "IN" if idx % 2 == 1 else "OUT"
                            
                            row_style = "red" if status == "FAILED" else "green"
                            table.add_row(str(idx), time_str, temp_str, status, record_type, style=row_style)
                        
                        console.print(table)
                        
                        # Real-time insights
                        console.print(f"\n[bold]Live Insights:[/bold]")
                        console.print(f"  📊 Today's records: {len(today_records)}")
                        
                        if len(today_records) == 0:
                            console.print(f"  ⚠️  No attendance records today")
                        elif len(today_records) == 1:
                            console.print(f"  🕒 Only Time In recorded - waiting for Time Out")
                        elif len(today_records) % 2 == 0:
                            console.print(f"  ✅ Complete pairs: {len(today_records) // 2}")
                        else:
                            console.print(f"  🚨 Odd number - check for missing Time Out")
                            
                    else:
                        console.print("[yellow]No records for today[/yellow]")
                        last_records_count = 0
                else:
                    console.print("[red]❌ Fetch failed[/red]")
                
                # Countdown for next poll
                console.print(f"\n[dim]Next check in {poll_interval} seconds... (Press Q to quit)[/dim]")
                start_time = time.time()

                while time.time() - start_time < poll_interval:
                    remaining = poll_interval - int(time.time() - start_time)
                    if remaining <= 0:
                        break
                        
                    # Update the countdown line
                    console.print(f"\r[dim]Next check in {remaining} seconds... (Press Q to quit)[/dim]", end="")
                    
                    # Check for quit command without blocking
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
            console.print("\n[yellow]Stopping real-time monitor...[/yellow]")
        
        console.print("[green]✅ Real-time monitoring stopped[/green]")

    def interactive_monitor(self, employee_id, password, interval_seconds=300):
        """Interactive monitoring with API - FAST refreshes!"""
        console = Console()
        
        console.print("[green]🚀 Interactive Monitor (API)[/green]")
        console.print("[dim]R=Refresh S=Save Q=Quit[/dim]")
        
        check_count = 0
        
        # Login once at start
        if not self.login(employee_id, password):
            console.print("[red]Login failed![/red]")
            return
        
        while True:
            console.clear()
            console.rule(f"[blue]Check #{check_count + 1}[/blue]")
            
            console.print("[yellow]🔄 Fetching...[/yellow]")
            
            # API call - much faster than Selenium!
            attendance_data = self.get_attendance_data(employee_id)
            
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
                    
                    for idx, record in enumerate(analysis['today_details'], start=1):
                        time_str = record.date_time.strftime("%H:%M:%S")
                        temp_str = f"{record.temperature:.1f}" if record.temperature else "N/A"
                        status = record.status
                        
                        row_style = "red" if status == "FAILED" else None
                        table.add_row(str(idx), time_str, temp_str, status, style=row_style)
                    
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
                        # Re-fetch to ensure we have all data for CSV
                        full_data = self.get_attendance_data(employee_id, length=100)
                        if full_data:
                            console.print(f"[green]Saved[/green]")
                        console.input("Enter...")
                    else:
                        console.print("[yellow]CSV disabled[/yellow]")
                        console.input("Enter...")
                elif key == 'r':
                    continue  # Instant refresh with API!
                else:
                    console.print("[yellow]Use R,S,Q[/yellow]")
                    console.input("Enter...")
                    
            except KeyboardInterrupt:
                console.print("\n[yellow]Stopping...[/yellow]")
                break
        
        console.print("[green]✅ Stopped[/green]")

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
        
        # Non-interactive monitoring
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
            
            logging.info(f"✓ Saved to {filename}")
            
        except Exception as e:
            logging.error(f"Save error: {e}")

    def get_signalr_connection_token_manual(self):
        """Manual method to help find the connection token"""
        console = Console()
        
        # Load and save the page
        response = self.session.get(f"{self.base_url}/Attendance")
        
        with open("attendance_page.html", "w", encoding="utf-8") as f:
            f.write(response.text)
        
        console.print("[yellow]🔍 Manual token extraction helper[/yellow]")
        console.print("[dim]The attendance page has been saved to 'attendance_page.html'[/dim]")
        console.print("\n[bold]Please do the following:[/bold]")
        console.print("1. Open 'attendance_page.html' in a text editor")
        console.print("2. Search for 'connectionToken'")
        console.print("3. Look for a line like: connectionToken: 'some_long_string'")
        console.print("4. Copy the long string value (without quotes)")
        console.print("\n[bold]Then enter the token here:[/bold]")
        
        try:
            token = console.input("Connection token: ").strip()
            if token:
                console.print(f"[green]✅ Using manual token (length: {len(token)})[/green]")
                return token
            else:
                console.print("[red]❌ No token entered[/red]")
                return None
        except KeyboardInterrupt:
            console.print("\n[yellow]Manual input cancelled[/yellow]")
            return None

def handle_signalr_attendance_update(attendance_data):
    """Callback for real-time updates - mobile optimized"""
    console = Console()
    
    # Minimal spacing
    console.print()
    console.rule("[green]🔄 Live Update[/green]")
    
    if isinstance(attendance_data, dict):
        employee_name = attendance_data.get('Name', 'Unknown')
        date_time_str = attendance_data.get('DateTimeStamp', '')
        temperature = attendance_data.get('Temperature')
        status = "SUCCESS" if attendance_data.get('AccessResult') == 1 else "FAILED"
        
        # Parse .NET date
        date_time = AttendanceRecord.parse_net_date(date_time_str)
        
        # Compact display
        console.print(f"[bold]{employee_name}[/bold]")
        console.print(f"🕒 {date_time.strftime('%H:%M')} | 🌡️ {temperature}°C" if temperature else f"🕒 {date_time.strftime('%H:%M')}")
        console.print(f"📊 {'✅ Success' if status == 'SUCCESS' else '❌ Failed'}")
        
    console.print(f"[dim]{datetime.now().strftime('%H:%M')}[/dim]")
    console.print("[dim]Listening...[/dim]")

def main():
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