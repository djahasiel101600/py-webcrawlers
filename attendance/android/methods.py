from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
import requests
from soundNotifier import SoundNotifier
from attendanceRecord import AttendanceRecord


console = Console()
sound_notifier = SoundNotifier()

def handle_signalr_attendance_update(attendance_data, monitor=None, employee_id=None, password=None):
    """Enhanced callback with sound notifications"""
    
    if isinstance(attendance_data, dict):
        # Handle re-authentication requests
        if attendance_data.get('type') == 'reauth_required':
            console.print()
            console.print("═" * 59)
            console.print(Align.center("🔄 RE-AUTHENTICATION REQUESTED"))
            console.print("─" * 59)
            console.print(f"│ [yellow]⚠️  Connection issues detected, re-authenticating...[/yellow]")
            
            # Play reconnect sound
            sound_notifier.play_sound("reconnect")
            
            if monitor and employee_id and password:
                # Ensure the reauth request comes from the currently managed monitor
                instance_id = attendance_data.get('instance_id')
                if monitor and getattr(monitor, 'signalr_monitor', None):
                    current_id = getattr(monitor.signalr_monitor, 'instance_id', None)
                    if instance_id and current_id and instance_id != current_id:
                        console.print(f"│ [yellow]⚠️  Ignoring reauth from stale instance {instance_id} (current {current_id})[/yellow]")
                        return

                success = monitor.reauthenticate_and_restart_monitor(employee_id, password, handle_signalr_attendance_update)
                if success:
                    console.print(f"│ [green]✅ Re-authentication successful![/green]")
                    sound_notifier.play_sound("success")
                else:
                    console.print(f"│ [red]❌ Re-authentication failed[/red]")
                    sound_notifier.play_sound("error")
            else:
                console.print(f"│ [red]❌ Cannot re-authenticate: missing credentials[/red]")
                sound_notifier.play_sound("error")
            
            console.print("─" * 59)
            return
    # Handle refresh signals (attendance updates)
        elif attendance_data.get('type') == 'refresh_signal':
            console.print()
            console.print("═" * 59)
            console.print(Align.center("🔄 BIOHUB REFRESH SIGNAL"))
            console.print("─" * 59)
            console.print(f"│ [bright_green]🎯 ATTENDANCE: New scan detected![/bright_green]")
            console.print(f"│ [dim]📡 Signal received at: {datetime.now().strftime('%H:%M:%S')}[/dim]")
            console.print("│ [yellow]💡 The system should refresh automatically...[/yellow]")
            send_telegram_message(message=f"Signal received at: {datetime.now().strftime('%H:%M:%S')}")
            
            # Play attendance sound
            sound_notifier.play_sound("attendance")
            
            console.print("─" * 59)
            return

    # Handle actual attendance data with sound
    console.print()
    console.print("═" * 59)
    console.print(Align.center("⚡ REAL-TIME BIOMETRIC UPDATE"))
    console.print("─" * 59)

    if isinstance(attendance_data, dict):
        employee_name = attendance_data.get('Name', 'UNKNOWN_USER')
        date_time_str = attendance_data.get('DateTimeStamp', '')
        temperature = attendance_data.get('Temperature')
        status = "ACCESS_GRANTED" if attendance_data.get('AccessResult') == 1 else "ACCESS_DENIED"
        
        date_time = AttendanceRecord.parse_net_date(date_time_str)
        
        # Play sound based on access result
        if status == "ACCESS_GRANTED":
            sound_notifier.play_sound("attendance")
        else:
            sound_notifier.play_sound("error")
        
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


import requests
import os
from dotenv import load_dotenv

load_dotenv()

class TelegramSender:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("CHAT_ID")
        
    def send_message(self, text):
        """Send a message via Telegram Bot API"""
        if not self.token or not self.chat_id:
            print("ERROR: Telegram token or chat ID not set")
            return False
        
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print("Telegram message sent successfully")
                return True
            else:
                print(f"Failed to send message: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Error sending Telegram message: {e}")
            return False

# Simple function to send message
def send_telegram_message(message):
    sender = TelegramSender()
    return sender.send_message(message)