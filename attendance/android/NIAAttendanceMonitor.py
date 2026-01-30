import requests
import json
import re
import time
import hashlib
import logging
import os
import csv
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
from rich import box
from typing import List, Dict, Any
from config import Config
from attendanceRecord import AttendanceRecord
from niaSignalRMonitor import NIASignalRMonitor
from methods import handle_signalr_attendance_update

console = Console()

import os

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
            # Keep a reference on the monitor manager so reauth can stop it
            self.signalr_monitor = signalr_monitor
            # In your start_signalr_monitor method, update the callback setup:
            signalr_monitor.add_callback(lambda data: handle_signalr_attendance_update(
                data, 
                monitor=self,  # Pass monitor instance for re-authentication
                employee_id=employee_id, 
                password=password
            ))

            # Store credentials on the monitor so it can request full reauth
            signalr_monitor.employee_id = employee_id
            signalr_monitor.password = password
            
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
            self.signalr_monitor = signalr_monitor
            # In your start_signalr_monitor method, update the callback setup:
            signalr_monitor.add_callback(lambda data: handle_signalr_attendance_update(
                data, 
                monitor=self,  # Pass monitor instance for re-authentication
                employee_id=employee_id, 
                password=password
            ))
            signalr_monitor.connect(connection_token)
            signalr_monitor.employee_id = employee_id
            signalr_monitor.password = password
        
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
            self.signalr_monitor = signalr_monitor
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
            self.signalr_monitor = signalr_monitor
            # In your start_signalr_monitor method, update the callback setup:
            signalr_monitor.add_callback(lambda data: handle_signalr_attendance_update(
                data, 
                monitor=self,  # Pass monitor instance for re-authentication
                employee_id=employee_id, 
                password=password
            ))
            signalr_monitor.connect(connection_token)
            signalr_monitor.employee_id = employee_id
            signalr_monitor.password = password
        
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
