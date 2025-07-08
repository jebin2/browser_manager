from custom_logger import logger_config
import os
import subprocess
class WindowManager:
    """Handles window management operations (Windows-specific)."""
    
    def __init__(self, powershell_path: str = None):
        self.powershell_path = powershell_path or os.getenv(
            "POWERSHELL", 
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.ps_script = os.path.join(self.script_dir, "window_control.ps1")
    
    def _run_powershell_script(self, action: str) -> None:
        """Run PowerShell script with given action."""
        try:
            subprocess.run([
                self.powershell_path, 
                "-ExecutionPolicy", "Bypass", 
                "-File", self.ps_script, 
                "-action", action
            ], check=True)
        except subprocess.CalledProcessError as e:
            logger_config.warning(f"PowerShell script failed for action '{action}': {e}")
        except FileNotFoundError:
            logger_config.warning(f"PowerShell not found at {self.powershell_path}")
    
    def save_active_window(self) -> None:
        """Save the currently active window handle."""
        self._run_powershell_script("save")
    
    def minimize_active_window(self) -> None:
        """Minimize the currently active window."""
        self._run_powershell_script("minimize")
    
    def restore_previous_focus(self) -> None:
        """Restore focus to the previously saved window."""
        self._run_powershell_script("restoreFocus")