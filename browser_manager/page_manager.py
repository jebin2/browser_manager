from typing import List
from custom_logger import logger_config
from playwright.sync_api import Browser, Page

class PageManager:
    """Manages Playwright page operations."""
    
    def __init__(self, browser: Browser, close_other_tabs: bool = True):
        self.browser = browser
        self.close_other_tabs = close_other_tabs
        self.context = browser.contexts[0] if browser.contexts else browser.new_context()
    
    def get_current_page(self) -> Page:
        """Get the current active page."""
        pages = self.context.pages
        if not pages:
            return self.context.new_page()
        
        current_page = pages[-1]
        if self.close_other_tabs:
            self.close_all_other_pages(current_page)
        
        current_page.bring_to_front()
        return current_page
    
    def close_all_other_pages(self, current_page: Page) -> None:
        """Close all tabs except the current one."""
        pages = self.context.pages
        for page in pages:
            if page != current_page:
                try:
                    page.close()
                except Exception as e:
                    logger_config.warning(f"Failed to close tab: {e}")
    
    def new_page(self) -> Page:
        """Create a new page."""
        return self.context.new_page()
    
    def close_context(self) -> None:
        """Close the browser context."""
        if self.context:
            try:
                self.context.close()
            except Exception as e:
                logger_config.warning(f"Error closing context: {e}")