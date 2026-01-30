import logging
from rich.logging import RichHandler
from rich.console import Console
from rich.align import Align
from rich.table import Table
from methods import handle_signalr_attendance_update
from config import Config
import argparse
import json
from rich.prompt import Prompt
from NIAAttendanceMonitor import NIAAttendanceMonitor
import os
import getpass
from soundNotifier import SoundNotifier
from methods import send_telegram_message

console = Console()
sound_notifier = SoundNotifier()

# Set up logging with Rich handler for mobile-friendly output
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="%H:%M:%S",
    handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True, show_path=False)]
)

def main():
    # Show startup banner
    console.print("\n")
    console.print(Align.center("┌─────────────────────────────────────────────────────┐"))
    console.print(Align.center("│              NIA ATTENDANCE MONITOR v3.0            │"))
    console.print(Align.center("│               [red]SECURE BIOMETRIC SURVEILLANCE[/red]            │"))
    console.print(Align.center("└─────────────────────────────────────────────────────┘"))
    console.print()
    send_telegram_message(message="NIA Attendance Booted")

    # Initialize sound system
    global sound_notifier
    sound_notifier.initialize()
    
    # Play startup sound
    sound_notifier.play_sound("startup")
    
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
            default="2"
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