from browser_manager import BrowserManager
from custom_logger import logger_config
from browser_manager.browser_config import BrowserConfig

config = BrowserConfig()
config.docker_name = "test"
config.port_map_template.append("-p 54321:54321")
browser_manager = BrowserManager(config)
with browser_manager as page:
	logger_config.info(f"Page title: {page.title()}")
	logger_config.info(f"New page title: {page.title()}")
	logger_config.info("wait", seconds=300)

logger_config.success("Example run completed successfully.")