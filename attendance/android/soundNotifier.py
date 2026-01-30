import os
import platform
from rich.console import Console

console = Console()


class SoundNotifier:
    def __init__(self, enabled=True):
        self.system = platform.system()
        self.enabled = enabled
        self.initialized = False
        
    def initialize(self):
        """Initialize sound system"""
        if not self.enabled:
            return
            
        try:
            # Test if sound works
            if self.system == "Windows":
                import winsound
                winsound.Beep(1000, 100)  # Quick test beep
            elif self.system == "Darwin":  # macOS
                os.system('afplay /System/Library/Sounds/Ping.aiff 2>/dev/null &')
            else:  # Linux
                os.system('echo -e "\a"')  # Terminal bell
                
            self.initialized = True
            console.print("│ [green]🔊 Sound notifications: ENABLED[/green]")
            
        except Exception as e:
            console.print(f"│ [yellow]⚠️  Sound system unavailable: {e}[/yellow]")
            self.enabled = False
    
    def play_sound(self, sound_type="attendance"):
        """Play different sounds for different events"""
        if not self.enabled or not self.initialized:
            return
            
        try:
            if self.system == "Windows":
                self._windows_sound(sound_type)
            elif self.system == "Darwin":  # macOS
                self._macos_sound(sound_type)
            else:  # Linux and other Unix-like systems
                self._linux_sound(sound_type)
        except Exception as e:
            # Silent fail - don't spam errors for sound issues
            pass
    
    def _windows_sound(self, sound_type):
        """Windows sound notifications"""
        import winsound
        if sound_type == "attendance":
            winsound.Beep(1000, 300)  # High beep for attendance
        elif sound_type == "success":
            winsound.Beep(800, 200)   # Medium beep for success
        elif sound_type == "error":
            winsound.Beep(400, 500)   # Low beep for error
        elif sound_type == "reconnect":
            winsound.Beep(600, 150)   # Short beep for reconnect
        elif sound_type == "startup":
            winsound.Beep(800, 100)   # Quick startup beep
    
    def _macos_sound(self, sound_type):
        """macOS sound notifications"""
        if sound_type == "attendance":
            os.system('afplay /System/Library/Sounds/Ping.aiff 2>/dev/null &')
        elif sound_type == "success":
            os.system('afplay /System/Library/Sounds/Glass.aiff 2>/dev/null &')
        elif sound_type == "error":
            os.system('afplay /System/Library/Sounds/Basso.aiff 2>/dev/null &')
        elif sound_type == "reconnect":
            os.system('afplay /System/Library/Sounds/Pop.aiff 2>/dev/null &')
        elif sound_type == "startup":
            os.system('afplay /System/Library/Sounds/Pop.aiff 2>/dev/null &')
    
    def _linux_sound(self, sound_type):
        """Linux sound notifications"""
        try:
            # Try using speaker-test (usually available)
            if sound_type == "attendance":
                os.system('speaker-test -t sine -f 1000 -l 1 > /dev/null 2>&1 &')
            elif sound_type == "success":
                os.system('speaker-test -t sine -f 800 -l 1 > /dev/null 2>&1 &')
            elif sound_type == "error":
                os.system('speaker-test -t sine -f 400 -l 1 > /dev/null 2>&1 &')
            elif sound_type == "reconnect":
                os.system('speaker-test -t sine -f 600 -l 1 > /dev/null 2>&1 &')
            elif sound_type == "startup":
                os.system('speaker-test -t sine -f 590 -l 1 > /dev/null 2>&1 &')
        except:
            # Fallback to terminal bell
            if sound_type == "attendance":
                print('\a')  # Single bell
            elif sound_type == "error":
                print('\a\a')  # Double bell for error
            else:
                print('\a')  # Single bell for others
