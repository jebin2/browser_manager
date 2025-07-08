from browser_manager import BrowserManager
from custom_logger import logger_config

browser_manager = BrowserManager()
with browser_manager as page:
	logger_config.info(f"Page title: {page.title()}")
	page.get_by_role("combobox", name="Search").fill("Playwright context manager")
	page.get_by_role("button", name="Google Search").first.click()
	page.wait_for_load_state("networkidle")
	logger_config.info(f"New page title: {page.title()}")
	logger_config.info("wait", seconds=300)

logger_config.success("Example run completed successfully.")