import os
from browser_manager import BrowserManager
from custom_logger import logger_config
from browser_manager.browser_config import BrowserConfig

config = BrowserConfig()
config.browser_executable = "/usr/bin/brave"
#config.use_neko = False
config.docker_name = "test"
# config.user_data_dir = f"/home/jebin/.automation_profile"
#config.user_data_dir = f'{os.getenv("PARENT_BASE_PATH")}/CaptionCreator/whoa/chatgpt_profile'
browser_manager = BrowserManager(config)
with browser_manager as page:
	logger_config.info(f"Page title: {page.title()}")
	logger_config.info(f"New page title: {page.title()}")
	context = page.context
	# Save cookies
	# while True:
	# 	import json
	# 	with open("", "w") as f:
	# 		json.dump(context.cookies(), f, indent=2)
	# 	logger_config.info("wait", seconds=10)

	# Later load them back
	# import json
	# with open("", "r") as f:
	# 	saved_cookies = json.load(f)

	# context.add_cookies(saved_cookies)

	logger_config.info("wait", seconds=300000)


logger_config.success("Example run completed successfully.")
