# main.py
import os
import json
import threading
import requests
import re
import websocket
import ssl
import random
from datetime import datetime
from urllib.parse import quote
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.core.window import Window
from kivy.utils import get_color_from_hex
from kivy.metrics import dp
import time

# Set window size for desktop testing
Window.size = (400, 700)

class AttendanceRecord:
    def __init__(self, date_time, temperature, employee_id, employee_name, machine_name, status):
        self.date_time = date_time
        self.temperature = temperature
        self.employee_id = employee_id
        self.employee_name = employee_name
        self.machine_name = machine_name
        self.status = status
    
    @classmethod
    def from_api_data(cls, api_record):
        """Create record from API JSON data"""
        date_time = cls.parse_net_date(api_record['DateTimeStamp'])
        temperature = float(api_record['Temperature']) if api_record['Temperature'] else None
        
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

# Alternative WebSocket implementation using Kivy's UrlRequest
class NIASignalRMonitor:
    def __init__(self, base_url, session_cookies, app, verbose=False):
        self.base_url = base_url
        self.session_cookies = session_cookies
        self.app = app
        self.is_connected = False
        self.should_reconnect = True
        self.verbose = verbose
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.message_id = 0
        self.connection_token = None
        self.polling_event = None

    def connect(self, connection_token):
        """Use polling instead of WebSocket for Android compatibility"""
        try:
            self.connection_token = connection_token
            self.is_connected = True
            self.reconnect_attempts = 0
            
            Logger.info("SignalR: Starting polling monitor (Android-compatible)")
            
            # Start polling instead of WebSocket
            self.start_polling()
            Clock.schedule_once(lambda dt: self.app.on_signalr_connected())
            
            return True
            
        except Exception as e:
            Logger.error(f"SignalR: Polling start error: {e}")
            self.schedule_reconnect()
            return False

    def start_polling(self):
        """Start polling for updates (Android-compatible alternative to WebSocket)"""
        # Poll every 10 seconds for real-time updates
        self.polling_event = Clock.schedule_interval(self.poll_for_updates, 10)

    def poll_for_updates(self, dt):
        """Poll the server for updates"""
        try:
            # Simulate WebSocket behavior by checking for new data
            if self.app.config.get('employee_id'):
                # This will trigger a data refresh
                Clock.schedule_once(lambda dt: self.app.refresh_data(None))
                
        except Exception as e:
            Logger.error(f"SignalR: Polling error: {e}")

    def simulate_attendance_update(self):
        """Simulate receiving a WebSocket update"""
        Logger.info("SignalR: Simulated attendance update received")
        Clock.schedule_once(lambda dt: self.app.on_signalr_attendance_update())

    def on_message(self, ws, message):
        """Placeholder for WebSocket messages"""
        pass

    def on_error(self, ws, error):
        """Handle errors"""
        Logger.error(f"SignalR error: {error}")
        self.is_connected = False
        self.schedule_reconnect()

    def on_close(self, ws, close_status_code, close_msg):
        """Handle closure"""
        Logger.info(f"SignalR closed")
        self.is_connected = False
        self.schedule_reconnect()

    def on_open(self, ws):
        """Handle connection"""
        Logger.info("SignalR connected!")
        self.is_connected = True
        self.reconnect_attempts = 0
        Clock.schedule_once(lambda dt: self.app.on_signalr_connected())

    def schedule_reconnect(self):
        """Schedule reconnection"""
        if not self.should_reconnect:
            return
            
        self.reconnect_attempts += 1
        delay = min(30, 2 ** self.reconnect_attempts)

        Logger.info(f"SignalR: Reconnecting in {delay}s (Attempt {self.reconnect_attempts}/10)")
        
        Clock.schedule_once(lambda dt: self._reconnect(), delay)

    def _reconnect(self):
        """Attempt reconnection"""
        if not self.should_reconnect or self.reconnect_attempts >= self.max_reconnect_attempts:
            Logger.error("SignalR: Maximum reconnect attempts reached")
            return
        
        Logger.info("SignalR: Attempting to re-establish connection...")
        # For polling, we just restart the polling
        if self.polling_event:
            self.polling_event.cancel()
        self.start_polling()

    def disconnect(self):
        """Disconnect monitor"""
        Logger.info("SignalR: Disconnecting...")
        self.should_reconnect = False
        self.is_connected = False
        
        if self.polling_event:
            self.polling_event.cancel()
            self.polling_event = None
class NIAAttendanceMonitor:
    def __init__(self, config):
        self.config = config
        self.base_url = config.get('base_url', 'https://attendance.caraga.nia.gov.ph')
        self.auth_url = "https://accounts.nia.gov.ph/Account/Login"
        self.session = requests.Session()
        
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36',
            'X-Requested-With': 'XMLHttpRequest'
        })
    
    def login(self, employee_id, password):
        """Login to the NIA system - from your original code"""
        try:
            Logger.info("NIA Monitor: Attempting login...")
            
            response = self.session.get(self.auth_url)
            
            token_match = re.search(r'name="__RequestVerificationToken".*?value="([^"]+)"', response.text)
            if not token_match:
                Logger.error("NIA Monitor: Security token not found")
                return False
            
            token = token_match.group(1)
            
            login_data = {
                'EmployeeId': employee_id,
                'Password': password,
                'RememberMe': 'false',
                '__RequestVerificationToken': token
            }
            
            Logger.info("NIA Monitor: Sending login request...")
            response = self.session.post(self.auth_url, data=login_data, allow_redirects=True)
            
            if response.status_code == 200 and employee_id in response.text:
                Logger.info("NIA Monitor: Login successful")
                return True
            else:
                Logger.error("NIA Monitor: Login failed - invalid credentials")
                return False
            
        except Exception as e:
            Logger.error(f"NIA Monitor: Login error: {e}")
            return False
    
    def get_attendance_data(self, employee_id, length=50):
        """Get attendance data via API - from your original code"""
        try:
            year = datetime.now().year
            month = datetime.now().strftime("%B")
            
            url = f"{self.base_url}/Attendance/IndexData/{year}?month={month}&eid={employee_id}"
            
            # Complete data payload from your original code
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
            
            Logger.info("NIA Monitor: Fetching attendance data...")
            response = self.session.post(url, data=data, headers=headers)
            response.raise_for_status()
            api_data = response.json()
            
            records = [AttendanceRecord.from_api_data(record) for record in api_data.get('data', [])]
            
            Logger.info(f"NIA Monitor: Retrieved {len(records)} records")
            
            return {
                'records': records,
                'total_records': api_data.get('recordsTotal', 0),
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            Logger.error(f"NIA Monitor: API error: {e}")
            return None

    def get_signalr_connection_token(self):
        """Get SignalR connection token - from your original code"""
        try:
            Logger.info("SignalR: Acquiring connection token...")
            
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
                        Logger.info("SignalR: Token acquired from headers")
                        return token
            
            Logger.info("SignalR: Attempting negotiation protocol...")
            return self._try_signalr_negotiation()
            
        except Exception as e:
            Logger.error(f"SignalR: Token error: {e}")
            return None

    def _try_signalr_negotiation(self):
        """Try to negotiate with SignalR server - from your original code"""
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
            
            Logger.info("SignalR: Negotiating handshake...")
            
            response = self.session.get(negotiate_url, params=params, headers=headers)
            
            if response.status_code == 200:
                negotiation_data = response.json()
                
                if 'ConnectionToken' in negotiation_data:
                    token = negotiation_data['ConnectionToken']
                    Logger.info("SignalR: Negotiation successful")
                    return token
                elif 'Url' in negotiation_data:
                    url = negotiation_data['Url']
                    token_match = re.search(r'connectionToken=([^&]+)', url)
                    if token_match:
                        token = token_match.group(1)
                        Logger.info("SignalR: Token extracted from URL")
                        return token
            else:
                Logger.error(f"SignalR: Negotiation failed: HTTP {response.status_code}")
            
            return None
            
        except Exception as e:
            Logger.error(f"SignalR: Negotiation error: {e}")
            return None

class StyledLabel(Label):
    pass

class StyledButton(Button):
    pass

class LoginTab(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.orientation = 'vertical'
        self.padding = dp(20)
        self.spacing = dp(15)
        self.create_widgets()
    
    def create_widgets(self):
        # Title
        title = StyledLabel(
            text="NIA Attendance Login",
            size_hint_y=None,
            height=dp(50),
            font_size='20sp',
            bold=True
        )
        self.add_widget(title)
        
        # Employee ID input
        self.employee_id_input = TextInput(
            hint_text='Employee ID',
            size_hint_y=None,
            height=dp(50),
            text=self.app.config.get('employee_id', ''),
            background_color=get_color_from_hex('#FFFFFF'),
            foreground_color=get_color_from_hex('#000000')
        )
        self.add_widget(self.employee_id_input)
        
        # Password input
        self.password_input = TextInput(
            hint_text='Password',
            password=True,
            size_hint_y=None,
            height=dp(50),
            text=self.app.config.get('password', ''),
            background_color=get_color_from_hex('#FFFFFF'),
            foreground_color=get_color_from_hex('#000000')
        )
        self.add_widget(self.password_input)
        
        # Login button
        self.login_btn = StyledButton(
            text='Login & Start Monitor',
            size_hint_y=None,
            height=dp(60),
            background_color=get_color_from_hex('#2E7D32'),
            background_normal=''
        )
        self.login_btn.bind(on_press=self.login)
        self.add_widget(self.login_btn)
        
        # Progress bar
        self.progress = ProgressBar(
            size_hint_y=None,
            height=dp(4),
            max=100,
            value=0
        )
        self.progress.opacity = 0
        self.add_widget(self.progress)
        
        # Status label
        self.status_label = StyledLabel(
            text='Enter credentials to start real-time monitoring',
            size_hint_y=None,
            height=dp(40)
        )
        self.add_widget(self.status_label)
    
    def login(self, instance):
        employee_id = self.employee_id_input.text.strip()
        password = self.password_input.text.strip()
        
        if not employee_id or not password:
            self.set_status('Please enter both Employee ID and Password', 'error')
            return
        
        # Disable login button during attempt
        self.login_btn.disabled = True
        self.login_btn.text = 'Logging in...'
        self.progress.opacity = 1
        self.progress.value = 30
        
        # Save credentials
        self.app.config['employee_id'] = employee_id
        self.app.config['password'] = password
        self.app.save_config()
        
        self.set_status('Logging in...', 'warning')
        
        # Run login in background thread
        threading.Thread(target=self.perform_login, daemon=True).start()
    
    def perform_login(self):
        try:
            monitor = NIAAttendanceMonitor(self.app.config)
            Clock.schedule_once(lambda dt: self.update_progress(60))
            
            success = monitor.login(
                self.app.config['employee_id'], 
                self.app.config['password']
            )
            
            if success:
                Clock.schedule_once(lambda dt: self.update_progress(80))
                # Get SignalR token and start monitoring
                connection_token = monitor.get_signalr_connection_token()
                if connection_token:
                    Clock.schedule_once(lambda dt: self.login_success(monitor, connection_token))
                else:
                    Clock.schedule_once(lambda dt: self.login_failed("Failed to get SignalR token"))
            else:
                Clock.schedule_once(lambda dt: self.login_failed("Login failed"))
                
        except Exception as e:
            Logger.error(f"Login error: {e}")
            Clock.schedule_once(lambda dt: self.login_error(str(e)))
    
    def update_progress(self, value):
        self.progress.value = value
    
    def login_success(self, monitor, connection_token):
        self.progress.value = 100
        self.set_status('Login successful! Starting real-time monitor...', 'success')
        
        # Switch to monitor tab after a delay
        Clock.schedule_once(lambda dt: self.app.switch_to_monitor_tab(), 1)
        
        # Start SignalR monitoring
        Clock.schedule_once(lambda dt: self.app.start_signalr_monitor(monitor, connection_token), 2)
        
        # Fetch initial data
        threading.Thread(
            target=self.fetch_attendance_data, 
            args=(monitor,), 
            daemon=True
        ).start()
    
    def login_failed(self, message):
        self.progress.value = 0
        self.progress.opacity = 0
        self.login_btn.disabled = False
        self.login_btn.text = 'Login & Start Monitor'
        self.set_status(message, 'error')
    
    def login_error(self, error):
        self.progress.value = 0
        self.progress.opacity = 0
        self.login_btn.disabled = False
        self.login_btn.text = 'Login & Start Monitor'
        self.set_status(f'Error: {error}', 'error')
    
    def fetch_attendance_data(self, monitor):
        try:
            data = monitor.get_attendance_data(self.app.config['employee_id'])
            Clock.schedule_once(lambda dt: self.app.display_attendance_data(data))
        except Exception as e:
            Logger.error(f"Data fetch error: {e}")
            Clock.schedule_once(lambda dt: self.app.display_error(f"Failed to fetch data: {e}"))
    
    def set_status(self, message, status_type='info'):
        colors = {
            'info': '#2196F3',
            'success': '#4CAF50',
            'warning': '#FF9800',
            'error': '#F44336'
        }
        self.status_label.text = message
        self.status_label.color = get_color_from_hex(colors.get(status_type, '#2196F3'))

class MonitorTab(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.orientation = 'vertical'
        self.padding = dp(10)
        self.spacing = dp(10)
        self.create_widgets()
    
    def create_widgets(self):
        # Header
        header = BoxLayout(size_hint_y=None, height=dp(50))
        title = StyledLabel(
            text="Real-Time Attendance",
            font_size='18sp',
            bold=True
        )
        header.add_widget(title)
        self.add_widget(header)
        
        # Connection status
        self.connection_status = StyledLabel(
            text='Real-time: POLLING MODE',
            size_hint_y=None,
            height=dp(30),
            font_size='12sp',
            color=get_color_from_hex('#2196F3')  # Blue for polling
        )
        self.add_widget(self.connection_status)
        
        # Last update label
        self.last_update_label = StyledLabel(
            text='Last update: Never',
            size_hint_y=None,
            height=dp(30),
            font_size='12sp',
            color=get_color_from_hex('#666666')
        )
        self.add_widget(self.last_update_label)
        
        # Next poll label
        self.next_poll_label = StyledLabel(
            text='Next poll: --:--',
            size_hint_y=None,
            height=dp(30),
            font_size='12sp',
            color=get_color_from_hex('#666666')
        )
        self.add_widget(self.next_poll_label)
        
        # Records count
        self.records_count_label = StyledLabel(
            text='Records today: 0',
            size_hint_y=None,
            height=dp(30),
            font_size='14sp',
            bold=True
        )
        self.add_widget(self.records_count_label)
        
        # Real-time events
        self.events_label = StyledLabel(
            text='Real-time polling active (10s intervals)',
            size_hint_y=None,
            height=dp(40),
            font_size='12sp',
            color=get_color_from_hex('#666666')
        )
        self.add_widget(self.events_label)
        
        # Scrollable area for records
        scroll = ScrollView()
        self.records_layout = GridLayout(
            cols=1,
            size_hint_y=None,
            spacing=dp(5),
            padding=dp(5)
        )
        self.records_layout.bind(minimum_height=self.records_layout.setter('height'))
        scroll.add_widget(self.records_layout)
        self.add_widget(scroll)
        
        # Control buttons
        button_layout = BoxLayout(size_hint_y=None, height=dp(60), spacing=dp(10))
        
        self.refresh_btn = StyledButton(
            text='Refresh Now',
            background_color=get_color_from_hex('#2196F3'),
            background_normal=''
        )
        self.refresh_btn.bind(on_press=self.app.refresh_data)
        button_layout.add_widget(self.refresh_btn)
        
        self.monitor_btn = StyledButton(
            text='Stop Monitor',
            background_color=get_color_from_hex('#F44336'),
            background_normal=''
        )
        self.monitor_btn.bind(on_press=self.toggle_monitor)
        button_layout.add_widget(self.monitor_btn)
        
        self.add_widget(button_layout)
        
        # Status label
        self.status_label = StyledLabel(
            text='Real-time polling ready',
            size_hint_y=None,
            height=dp(30),
            font_size='12sp'
        )
        self.add_widget(self.status_label)
        
        # Start polling timer display
        self.update_poll_timer()
    
    def toggle_monitor(self, instance):
        if self.app.signalr_monitor and self.app.signalr_monitor.is_connected:
            self.app.stop_signalr_monitor()
            instance.text = 'Start Monitor'
            instance.background_color = get_color_from_hex('#4CAF50')
        else:
            self.app.start_signalr_monitor()
            instance.text = 'Stop Monitor'
            instance.background_color = get_color_from_hex('#F44336')
    
    def update_poll_timer(self):
        """Update the next poll time display"""
        # This would be called periodically to update the timer
        pass

class SettingsTab(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.orientation = 'vertical'
        self.padding = dp(20)
        self.spacing = dp(15)
        self.create_widgets()
    
    def create_widgets(self):
        title = StyledLabel(
            text="Settings",
            size_hint_y=None,
            height=dp(50),
            font_size='20sp',
            bold=True
        )
        self.add_widget(title)
        
        url_label = StyledLabel(
            text="Base URL:",
            size_hint_y=None,
            height=dp(30)
        )
        self.add_widget(url_label)
        
        self.base_url_input = TextInput(
            hint_text='https://attendance.caraga.nia.gov.ph',
            size_hint_y=None,
            height=dp(50),
            text=self.app.config.get('base_url', 'https://attendance.caraga.nia.gov.ph'),
            background_color=get_color_from_hex('#FFFFFF'),
            foreground_color=get_color_from_hex('#000000')
        )
        self.add_widget(self.base_url_input)
        
        self.verbose_btn = StyledButton(
            text='Verbose Logging: OFF',
            size_hint_y=None,
            height=dp(50),
            background_color=get_color_from_hex('#9E9E9E')
        )
        self.verbose_btn.bind(on_press=self.toggle_verbose)
        self.add_widget(self.verbose_btn)
        
        save_btn = StyledButton(
            text='Save Settings',
            size_hint_y=None,
            height=dp(50),
            background_color=get_color_from_hex('#2196F3'),
            background_normal=''
        )
        save_btn.bind(on_press=self.save_settings)
        self.add_widget(save_btn)
        
        self.status_label = StyledLabel(
            text='Configure your settings',
            size_hint_y=None,
            height=dp(30)
        )
        self.add_widget(self.status_label)
    
    def toggle_verbose(self, instance):
        current = self.app.config.get('verbose_logging', False)
        self.app.config['verbose_logging'] = not current
        instance.text = f'Verbose Logging: {"ON" if not current else "OFF"}'
        instance.background_color = get_color_from_hex('#4CAF50') if not current else get_color_from_hex('#9E9E9E')
    
    def save_settings(self, instance):
        self.app.config['base_url'] = self.base_url_input.text.strip()
        self.app.save_config()
        self.status_label.text = 'Settings saved successfully!'
        self.status_label.color = get_color_from_hex('#4CAF50')

class NIAAttendanceApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = "NIA Attendance Monitor"
        self.config_file = self.get_config_path()
        self.signalr_monitor = None
        self.load_config()

    def build(self):
        main_layout = BoxLayout(orientation='vertical')
        
        self.tabs = TabbedPanel()
        self.tabs.do_default_tab = False
        
        self.login_tab = LoginTab(self)
        self.monitor_tab = MonitorTab(self)
        self.settings_tab = SettingsTab(self)
        
        self.tabs.add_widget(TabbedPanelItem(text='Login', content=self.login_tab))
        self.tabs.add_widget(TabbedPanelItem(text='Monitor', content=self.monitor_tab))
        self.tabs.add_widget(TabbedPanelItem(text='Settings', content=self.settings_tab))
        
        main_layout.add_widget(self.tabs)
        return main_layout
    
    def get_config_path(self):
        """Get the correct config file path for the current platform"""
        try:
            # Use Kivy's app data directory which works on all platforms
            from kivy.app import App
            import os
            
            # Get the app data directory
            data_dir = App.get_running_app().user_data_dir
            
            # Create the directory if it doesn't exist
            if not os.path.exists(data_dir):
                os.makedirs(data_dir, exist_ok=True)
                Logger.info(f"Created app data directory: {data_dir}")
            
            config_path = os.path.join(data_dir, "nia_config.json")
            Logger.info(f"Using config file: {config_path}")
            return config_path
            
        except Exception as e:
            Logger.error(f"Error getting config path: {e}")
            # Fallback to current directory
            return "nia_config.json"

    def load_config(self):
        """Load configuration from file"""
        try:
            # Ensure directory exists
            config_dir = os.path.dirname(self.config_file)
            if config_dir and not os.path.exists(config_dir):
                os.makedirs(config_dir, exist_ok=True)
                Logger.info(f"Created config directory: {config_dir}")
                
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    self.config = json.load(f)
                Logger.info("Config loaded successfully")
            else:
                self.config = {
                    'employee_id': '',
                    'password': '',
                    'base_url': 'https://attendance.caraga.nia.gov.ph',
                    'verbose_logging': False
                }
                # Save default config
                self.save_config()
                Logger.info("Created default config")
                
        except Exception as e:
            Logger.error(f"Config load error: {e}")
            self.config = {
                'employee_id': '',
                'password': '',
                'base_url': 'https://attendance.caraga.nia.gov.ph',
                'verbose_logging': False
            }

    def save_config(self):
        """Save configuration to file"""
        try:
            # Ensure directory exists
            config_dir = os.path.dirname(self.config_file)
            if config_dir and not os.path.exists(config_dir):
                os.makedirs(config_dir, exist_ok=True)
                
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            Logger.info(f"Config saved to: {self.config_file}")
        except Exception as e:
            Logger.error(f"Config save error: {e}")
            
    def switch_to_monitor_tab(self):
        self.tabs.switch_to(self.tabs.tab_list[1])
    
    def start_signalr_monitor(self, monitor, connection_token):
        """Start SignalR WebSocket monitor"""
        try:
            cookies_dict = {c.name: c.value for c in monitor.session.cookies}
            self.signalr_monitor = NIASignalRMonitor(
                self.config['base_url'], 
                cookies_dict, 
                self,
                verbose=self.config.get('verbose_logging', False)
            )
            
            success = self.signalr_monitor.connect(connection_token)
            if success:
                Logger.info("SignalR monitor started successfully")
            else:
                Logger.error("Failed to start SignalR monitor")
                
        except Exception as e:
            Logger.error(f"SignalR start error: {e}")
    
    def on_signalr_connected(self):
        """Called when polling monitor starts"""
        self.monitor_tab.connection_status.text = 'Real-time: POLLING ACTIVE'
        self.monitor_tab.connection_status.color = get_color_from_hex('#4CAF50')
        self.monitor_tab.status_label.text = 'Real-time polling active (10s intervals)'
        self.monitor_tab.status_label.color = get_color_from_hex('#4CAF50')

    def on_signalr_attendance_update(self):
        """Called when new data is detected during polling"""
        self.monitor_tab.events_label.text = f'🔄 Data refreshed {datetime.now().strftime("%H:%M:%S")}'
        self.monitor_tab.events_label.color = get_color_from_hex('#FF9800')
        
        # Reset event label after 3 seconds
        Clock.schedule_once(lambda dt: self.reset_event_label(), 3)

    def stop_signalr_monitor(self):
        """Stop the polling monitor"""
        if self.signalr_monitor:
            self.signalr_monitor.disconnect()
            self.signalr_monitor = None
            self.monitor_tab.connection_status.text = 'Real-time: STOPPED'
            self.monitor_tab.connection_status.color = get_color_from_hex('#F44336')
            self.monitor_tab.status_label.text = 'Real-time monitoring stopped'
    
    def reset_event_label(self):
        self.monitor_tab.events_label.text = 'Waiting for real-time events...'
        self.monitor_tab.events_label.color = get_color_from_hex('#666666')
    
    def display_attendance_data(self, data):
        self.monitor_tab.last_update_label.text = f'Last update: {datetime.now().strftime("%H:%M:%S")}'
        self.monitor_tab.records_layout.clear_widgets()
        
        if not data or 'records' not in data:
            no_data_label = StyledLabel(
                text='No attendance data found',
                size_hint_y=None,
                height=dp(40),
                color=get_color_from_hex('#F44336')
            )
            self.monitor_tab.records_layout.add_widget(no_data_label)
            self.monitor_tab.records_count_label.text = 'Records today: 0'
            return
        
        records = data['records']
        today = datetime.now().date()
        today_records = [r for r in records if r.date_time.date() == today]
        
        self.monitor_tab.records_count_label.text = f'Records today: {len(today_records)}'
        
        if not today_records:
            no_data_label = StyledLabel(
                text='No records for today',
                size_hint_y=None,
                height=dp(40),
                color=get_color_from_hex('#FF9800')
            )
            self.monitor_tab.records_layout.add_widget(no_data_label)
            return
        
        for record in today_records[-10:]:
            record_box = BoxLayout(
                size_hint_y=None,
                height=dp(50),
                spacing=dp(5)
            )
            
            time_label = StyledLabel(
                text=record.date_time.strftime('%H:%M'),
                size_hint_x=0.3,
                font_size='14sp',
                bold=True
            )
            
            temp_text = f"{record.temperature:.1f}°C" if record.temperature else "N/A"
            temp_label = StyledLabel(
                text=temp_text,
                size_hint_x=0.3,
                font_size='12sp'
            )
            
            status_color = get_color_from_hex('#4CAF50') if record.status == "ACCESS_GRANTED" else get_color_from_hex('#F44336')
            status_label = StyledLabel(
                text=record.status.replace('ACCESS_', ''),
                size_hint_x=0.4,
                font_size='12sp',
                color=status_color
            )
            
            record_box.add_widget(time_label)
            record_box.add_widget(temp_label)
            record_box.add_widget(status_label)
            
            self.monitor_tab.records_layout.add_widget(record_box)
    
    def display_error(self, error):
        self.monitor_tab.records_layout.clear_widgets()
        error_label = StyledLabel(
            text=error,
            size_hint_y=None,
            height=dp(40),
            color=get_color_from_hex('#F44336')
        )
        self.monitor_tab.records_layout.add_widget(error_label)
        self.monitor_tab.records_count_label.text = 'Records: Error'
        self.monitor_tab.status_label.text = 'Data load failed'
        self.monitor_tab.status_label.color = get_color_from_hex('#F44336')
    
    def refresh_data(self, instance):
        self.monitor_tab.status_label.text = 'Refreshing data...'
        self.monitor_tab.status_label.color = get_color_from_hex('#FF9800')
        if instance:
            instance.disabled = True
            instance.text = 'Refreshing...'
        
        threading.Thread(target=self.refresh_data_thread, daemon=True).start()
    
    def refresh_data_thread(self):
        try:
            monitor = NIAAttendanceMonitor(self.config)
            
            if monitor.login(self.config['employee_id'], self.config['password']):
                data = monitor.get_attendance_data(self.config['employee_id'])
                Clock.schedule_once(lambda dt: self.refresh_complete(data))
            else:
                Clock.schedule_once(lambda dt: self.refresh_failed("Login failed"))
        except Exception as e:
            Clock.schedule_once(lambda dt: self.refresh_failed(str(e)))
    
    def refresh_complete(self, data):
        self.display_attendance_data(data)
        self.monitor_tab.refresh_btn.disabled = False
        self.monitor_tab.refresh_btn.text = 'Refresh Now'
        self.monitor_tab.status_label.text = 'Data refreshed successfully'
        self.monitor_tab.status_label.color = get_color_from_hex('#4CAF50')
    
    def refresh_failed(self, error):
        self.display_error(error)
        self.monitor_tab.refresh_btn.disabled = False
        self.monitor_tab.refresh_btn.text = 'Refresh Now'
        self.monitor_tab.status_label.text = 'Refresh failed'
        self.monitor_tab.status_label.color = get_color_from_hex('#F44336')

if __name__ == '__main__':
    NIAAttendanceApp().run()
