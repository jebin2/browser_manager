"""
This module provides a robust context manager for launching and controlling a browser
for automation with Playwright.
"""

from typing import Optional
from custom_logger import logger_config
import subprocess
import tempfile
import shutil
from playwright.sync_api import sync_playwright, Playwright, Browser, Page
from .browser_config import BrowserConfig
from .window_manager import WindowManager
from .browser_launcher import BrowserLauncher
from .page_manager import PageManager
from .local_browser_launcher import LocalBrowserLauncher
from .neko_browser_launcher import NekoBrowserLauncher

class BrowserManager:
    """
    Main browser manager with improved modularity and error handling.
    
    Usage:
        config = BrowserConfig(url="https://google.com", headless=True)
        manager = BrowserManager(config)
        
        # Context manager usage
        with manager as page:
            page.fill("#q", "search query")
            page.press("#q", "Enter")
        
        # Manual usage
        page = manager.start()
        try:
            # Do work with page
            pass
        finally:
            manager.stop()
    """
    
    def __init__(self, config: Optional[BrowserConfig] = None):
        self.config = config or BrowserConfig()
        self._setup_user_data_dir()
        
        self.window_manager = WindowManager() if self.config.minimize_window_focus else None
        self.launcher = self._create_launcher()
        
        # Runtime state
        self.browser_process: Optional[subprocess.Popen] = None
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page_manager: Optional[PageManager] = None
        self.page: Optional[Page] = None
        self._is_started = False
    
    def _setup_user_data_dir(self) -> None:
        """Setup user data directory."""
        if not self.config.user_data_dir:
            self.config.user_data_dir = tempfile.mkdtemp(prefix="browser_manager_")
            self._temp_dir_created = True
        else:
            self._temp_dir_created = False
    
    def _create_launcher(self) -> BrowserLauncher:
        """Create appropriate browser launcher."""
        if self.config.use_neko:
            return NekoBrowserLauncher()
        else:
            return LocalBrowserLauncher(self.window_manager)
    
    def start(self) -> Page:
        """Start the browser and return a Page object."""
        if self._is_started:
            return self.page
        
        try:
            # Launch browser
            self.browser_process, ws_url = self.launcher.launch(self.config)
            
            # Connect Playwright
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.connect_over_cdp(ws_url)
            
            # Setup page management
            self.page_manager = PageManager(self.browser, self.config.close_other_tabs)
            self.page = self.page_manager.get_current_page()
            
            # Navigate to URL
            self.page.goto(self.config.url)
            self.page.wait_for_load_state("networkidle", timeout=self.config.connection_timeout * 1000)
            
            self._is_started = True
            logger_config.success("Browser started successfully")
            return self.page
            
        except Exception as e:
            logger_config.error(f"Failed to start browser: {e}")
            self.stop()
            raise
    
    def stop(self) -> None:
        """Stop the browser and clean up resources."""
        if not self._is_started:
            return
        
        logger_config.info("Stopping browser manager...")
        
        # Close page
        if self.page and not self.page.is_closed():
            try:
                self.page.close()
            except Exception as e:
                logger_config.warning(f"Error closing page: {e}")
        
        # Close page manager
        if self.page_manager:
            self.page_manager.close_context()
        
        # Stop Playwright
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception as e:
                logger_config.warning(f"Error stopping Playwright: {e}")
        
        # Clean up browser process
        if self.browser_process:
            self.launcher.cleanup(self.config, self.browser_process)

        # Clean up temp directory
        if self._temp_dir_created and self.config.user_data_dir:
            try:
                shutil.rmtree(self.config.user_data_dir, ignore_errors=True)
                logger_config.info(f"Cleaned up temp directory: {self.config.user_data_dir}")
            except Exception as e:
                logger_config.warning(f"Error cleaning temp directory: {e}")
        
        self._is_started = False
        logger_config.success("Browser stopped successfully")
    
    def new_page(self) -> Page:
        """Create a new page."""
        if not self._is_started:
            raise RuntimeError("Browser not started")
        return self.page_manager.new_page()
    
    def __enter__(self) -> Page:
        """Context manager entry."""
        return self.start()
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()
    
    def __del__(self) -> None:
        """Destructor cleanup."""
        if self._is_started:
            self.stop()


# Factory function for common configurations
def create_browser_manager(
    url: str = "https://google.com",
    headless: bool = False,
    use_neko: bool = False,
    **kwargs
) -> BrowserManager:
    """Factory function to create browser manager with common settings."""
    config = BrowserConfig(url=url, headless=headless, use_neko=use_neko, **kwargs)
    return BrowserManager(config)


# Example usage
if __name__ == "__main__":
    # Using factory function
    manager = create_browser_manager(
        url="https://google.com",
        headless=False,
        use_neko=False
    )
    
    try:
        with manager as page:
            logger_config.info(f"Page title: {page.title()}")
            page.get_by_role("combobox", name="Search").fill("Playwright automation")
            page.get_by_role("button", name="Google Search").first.click()
            page.wait_for_load_state("networkidle")
            logger_config.info(f"New page title: {page.title()}")
            
    except Exception as e:
        logger_config.error(f"Error during automation: {e}")
    
    logger_config.success("Example completed")