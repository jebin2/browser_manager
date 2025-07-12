from abc import ABC, abstractmethod
from .browser_config import BrowserConfig
import subprocess
import requests
from .browser_connection_error import BrowserConnectionError
from custom_logger import logger_config
import time

class BrowserLauncher(ABC):
    """Abstract base class for browser launchers."""
    
    @abstractmethod
    def launch(self, config: BrowserConfig) -> tuple[subprocess.Popen, str]:
        """Launch browser and return process and WebSocket URL."""
        pass
    
    @abstractmethod
    def cleanup(self, config: BrowserConfig, process: subprocess.Popen) -> None:
        """Clean up browser process."""
        pass
    
    def _get_websocket_url(self, port: int, timeout: int) -> str:
        """Get WebSocket URL from browser."""
        try:
            self._wait_for_browser_start(port, timeout)
            response = requests.get(f"http://localhost:{port}/json/version", timeout=5)
            response.raise_for_status()
            data = response.json()
            return data["webSocketDebuggerUrl"]
        except requests.RequestException as e:
            raise BrowserConnectionError(f"Could not connect to Neko browser: {e}")

    def _wait_for_browser_start(self, port: int, timeout: int) -> None:
        """Wait for browser to start and be ready for connections."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                logger_config.info(f"Waiting to start neko browser debug mode {int(time.time() - start_time):02d}", overwrite=True)
                response = requests.get(f"http://localhost:{port}/json/version", timeout=2)
                if response.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(1)
        
        raise BrowserConnectionError(f"Browser not ready after {timeout} seconds")