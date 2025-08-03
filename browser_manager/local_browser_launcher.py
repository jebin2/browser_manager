from typing import Optional, List
from custom_logger import logger_config
import os
import subprocess
import psutil
import requests
import time
from .browser_config import BrowserConfig
from .window_manager import WindowManager
from .browser_launcher import BrowserLauncher
from .browser_launch_error import BrowserLaunchError
from .browser_connection_error import BrowserConnectionError

class LocalBrowserLauncher(BrowserLauncher):
    """Launches browser locally with remote debugging."""
    
    def __init__(self, window_manager: Optional[WindowManager] = None):
        self.window_manager = window_manager
    
    def launch(self, config: BrowserConfig) -> tuple[subprocess.Popen, str]:
        """Launch local browser with remote debugging."""
        self.clean_browser_profile(config)
        if self.window_manager and config.minimize_window_focus:
            self.window_manager.save_active_window()
        
        cmd = self._build_command(config)
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=None,  # inherit from parent
                stderr=None,  # inherit from parent
                text=True,
                bufsize=1,
                env={**os.environ, 'PYTHONUNBUFFERED': '1'}
            )
            
            logger_config.info(f"Local browser launched with PID: {process.pid}")

            ws_url = self._get_websocket_url(config.debugging_port, config.connection_timeout)
            
            if self.window_manager and config.minimize_window_focus:
                self.window_manager.minimize_active_window()
                self.window_manager.restore_previous_focus()
            
            return process, ws_url
            
        except Exception as e:
            raise BrowserLaunchError(f"Failed to launch local browser: {e}")
    
    def _build_command(self, config: BrowserConfig) -> List[str]:
        """Build browser command line arguments."""
        cmd = [
            config.browser_executable,
            f"--remote-debugging-port={config.debugging_port}",
            f"--user-data-dir={config.user_data_dir}",
            *config.chrome_flags.split()
        ]
        
        if config.headless:
            cmd.append("--headless=new")
        
        cmd.extend(config.extra_args)
        logger_config.info(f'Command to run: {cmd}')
        return cmd
    
    def cleanup(self, config: BrowserConfig, process: subprocess.Popen) -> None:
        """Clean up browser process and children."""
        if not process:
            return
        
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            
            # Terminate all children first
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            
            # Terminate parent
            parent.terminate()
            
            # Wait for graceful termination
            gone, alive = psutil.wait_procs(children + [parent], timeout=5)
            
            # Force kill if necessary
            for p in alive:
                try:
                    p.kill()
                    logger_config.warning(f"Force killed process {p.pid}")
                except psutil.NoSuchProcess:
                    pass
            
            logger_config.info("Browser process cleaned up successfully")
            
        except psutil.NoSuchProcess:
            logger_config.info("Browser process already terminated")
        except Exception as e:
            logger_config.error(f"Error during browser cleanup: {e}")