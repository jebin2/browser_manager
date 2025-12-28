from abc import ABC, abstractmethod
from .browser_config import BrowserConfig
import subprocess
import requests
from .browser_connection_error import BrowserConnectionError
from custom_logger import logger_config
import time
import glob
import os
import shutil
import json

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

    def clean_browser_profile(self, config: BrowserConfig):
        """
        Clean up a browser profile directory by removing lock files and caches.

        Args:
            config (BrowserConfig): Contains user_data_dir and cleanup flags.
        """
        if not config.delete_user_data_dir_singleton_lock:
            return

        profile_path = config.user_data_dir

        # Fix Chrome exit state to prevent "Restore pages" popup
        self._fix_chrome_exit_state(profile_path)

        # Remove Singleton* files in user_data_dir
        singleton_files = glob.glob(os.path.join(profile_path, "Singleton*"))
        for file_path in singleton_files:
            try:
                os.remove(file_path)
                logger_config.success(f"Removed {file_path}")
            except Exception as e:
                logger_config.error(f"Failed to remove {file_path}: {e}")

        # Also remove lockfiles from /tmp/.com.google.Chrome*/Singleton*
        tmp_singletons = glob.glob("/tmp/.com.google.Chrome*/Singleton*")
        for file_path in tmp_singletons:
            try:
                os.remove(file_path)
                logger_config.success(f"Removed temp file {file_path}")
            except Exception as e:
                logger_config.error(f"Failed to remove temp file {file_path}: {e}")

        # Remove 'lockfile'
        lockfile_path = os.path.join(profile_path, "lockfile")
        if os.path.exists(lockfile_path):
            try:
                os.remove(lockfile_path)
                logger_config.success(f"Removed {lockfile_path}")
            except Exception as e:
                logger_config.error(f"Failed to remove {lockfile_path}: {e}")

        # Remove Extensions/
        extensions_path = os.path.join(profile_path, "Extensions")
        if os.path.exists(extensions_path):
            try:
                shutil.rmtree(extensions_path)
                logger_config.success(f"Removed {extensions_path}")
            except Exception as e:
                logger_config.error(f"Failed to remove {extensions_path}: {e}")

        # Remove GPUCache/
        gpu_cache_path = os.path.join(profile_path, "GPUCache")
        if os.path.exists(gpu_cache_path):
            try:
                shutil.rmtree(gpu_cache_path)
                logger_config.success(f"Removed {gpu_cache_path}")
            except Exception as e:
                logger_config.error(f"Failed to remove {gpu_cache_path}: {e}")

    def _fix_chrome_exit_state(self, profile_path: str):
        """
        Fix Chrome's exit state in the Preferences file to prevent "Restore pages" popup.
        
        When Chrome crashes or is killed, it marks exit_type as "Crashed" in the Preferences file.
        This method resets it to "Normal" and sets exited_cleanly to true.
        """
        # Chrome stores preferences in Default/Preferences
        prefs_file = os.path.join(profile_path, "Default", "Preferences")
        
        if not os.path.exists(prefs_file):
            logger_config.info(f"No Preferences file found at {prefs_file}, skipping exit state fix")
            return
        
        try:
            with open(prefs_file, 'r', encoding='utf-8') as f:
                prefs = json.load(f)
            
            modified = False
            
            # Fix profile.exit_type
            if 'profile' not in prefs:
                prefs['profile'] = {}
            if prefs['profile'].get('exit_type') != 'Normal':
                prefs['profile']['exit_type'] = 'Normal'
                modified = True
                
            # Fix profile.exited_cleanly
            if not prefs['profile'].get('exited_cleanly', True):
                prefs['profile']['exited_cleanly'] = True
                modified = True
            
            if modified:
                with open(prefs_file, 'w', encoding='utf-8') as f:
                    json.dump(prefs, f, indent=2)
                logger_config.success(f"Fixed Chrome exit state in {prefs_file}")
            else:
                logger_config.info("Chrome exit state already clean")
                
        except json.JSONDecodeError as e:
            logger_config.warning(f"Failed to parse Preferences file: {e}")
        except Exception as e:
            logger_config.warning(f"Failed to fix Chrome exit state: {e}")