# main.py
try:
    import websocket
    # Basic sanity check: websocket-client provides WebSocketApp
    if not hasattr(websocket, 'WebSocketApp'):
        raise ImportError("module 'websocket' does not provide 'WebSocketApp'")
except Exception as e:
    console.print("│ [red]🚨 WebSocket import error:[/red]")
    console.print(f"│ [red]  {e}[/red]")
    console.print("│ [yellow]💡 Fix: install the correct package with:\n  pip install websocket-client\n(or add 'websocket-client' to requirements.txt)[/yellow]")
    raise
import json
import threading
import time
import random
from datetime import datetime
from urllib.parse import quote
from rich.console import Console
from soundNotifier import SoundNotifier

console = Console()
sound_notifier = SoundNotifier()

class NIASignalRMonitor:
    def __init__(self, base_url, session_cookies, verbose=False):
        self.base_url = base_url
        self.session_cookies = session_cookies
        self.ws = None
        self.is_connected = False
        self.should_reconnect = True
        # Flag set when this instance has requested a full re-auth and is waiting
        # for the external reauthentication/handoff to complete. While True,
        # the monitor should not schedule or attempt further reconnects.
        self.pending_reauth = False
        # Unique identifier for this monitor instance (helps debugging/handoff)
        self.instance_id = id(self)
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
        # If a full re-auth is already pending for this instance, avoid
        # scheduling additional reconnect attempts — wait for handoff.
        if getattr(self, 'pending_reauth', False):
            if self.verbose:
                console.print(f"│ [dim]INSTANCE {self.instance_id}: Pending reauth — skipping reconnect[/dim]")
            return
        
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
        # Do not schedule reconnects if a full re-auth is pending
        if (close_status_code == 1000 or 
            self.reconnect_attempts >= self.max_reconnect_attempts or
            not self.should_reconnect or
            getattr(self, 'pending_reauth', False)):
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

        # Play success sound on connection
        sound_notifier.play_sound("success")
    
    def _schedule_reconnect(self, employee_id=None, password=None, full_reauth=False):
        """Enhanced reconnection with optional full re-authentication"""
        if not self.should_reconnect or getattr(self, 'pending_reauth', False):
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
            # Mark that this instance is awaiting external reauthentication
            self.pending_reauth = True
            # Trigger full re-authentication (external handler should perform handoff)
            for callback in self.callbacks:
                try:
                    callback({'type': 'reauth_required', 'employee_id': self.employee_id, 'password': self.password, 'instance_id': self.instance_id})
                except Exception as e:
                    if self.verbose:
                        console.print(f"│ [red]⚠️  REAUTH CALLBACK ERROR: {e}[/red]")
            # Important: do NOT call connect() from this instance after requesting reauth.
            # The external handler (monitor manager) must perform a clean stop and start a
            # new NIASignalRMonitor that will handle subsequent connections.
            return
        else:
            console.print("│ [blue]🔄 RECONNECT: Attempting to re-establish connection...[/blue]")
            # Use a small delay before reconnecting to ensure clean state
            time.sleep(2)
            self.connect(self.connection_token)

    def stop(self):
        """Stop this monitor instance and clean up resources."""
        console.print(f"│ [yellow]🔒 STOPPING: Monitor instance {getattr(self, 'instance_id', 'unknown')}[/yellow]")
        self.should_reconnect = False
        # Clear pending reauth flag when explicitly stopping
        self.pending_reauth = False
        self.is_connected = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            finally:
                self.ws = None

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
