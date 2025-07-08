"""
This module provides a robust context manager for launching and controlling a browser
for automation with Playwright.
"""
from custom_logger import logger_config
import os
import subprocess
import psutil
import requests
from playwright.sync_api import sync_playwright, Playwright, Browser, Page
from typing import Optional
import re
import shutil

POWERSHELL_PATH = os.getenv("POWERSHELL", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
DEFAULT_BROWSER_EXECUTABLE = os.getenv("BROWSER_EXECUTABLE", "/usr/bin/brave-browser")
DEFAULT_NEKO_DIR=os.getenv("NEKO_DIR", os.path.expanduser("~/git/neko-remote-debugging"))

class BrowserManager:
	"""
	A context manager to reliably launch a browser, connect with Playwright,
	and ensure cleanup.

	Usage:
		manager = BrowserManager(url="https://google.com")
		with manager as page:
			# 'page' is a Playwright Page object
			page.fill("#q", "Playwright is awesome")
			page.press("#q", "Enter")
			time.sleep(5)

				(or)

		manager.start()
	"""

	def __init__(
		self,
		url: str = "https://jebin2-paper.hf.space/",
		user_data_dir: Optional[str] = None,
		browser_executable: str = DEFAULT_BROWSER_EXECUTABLE,
		debugging_port: int = 9222,
		headless: bool = False,
		use_neko: bool = True,
		neko_dir: str = DEFAULT_NEKO_DIR,
		close_other_tab: bool = True,
		minimize_bw_restore_current_window_focus: bool = False,
	):
		self.url = url
		self.user_data_dir = user_data_dir if user_data_dir else self._get_temp_user_data_dir()
		self.browser_executable = browser_executable
		self.debugging_port = debugging_port
		self.headless = headless
		self.use_neko = use_neko
		self.neko_dir = neko_dir
		self.close_other_tab = close_other_tab
		self.minimize_bw_restore_current_window_focus = minimize_bw_restore_current_window_focus
		self.debug_ws_url: Optional[str] = None

		self.browser_process: Optional[subprocess.Popen] = None
		self.neko_process: Optional[subprocess.Popen] = None
		self.playwright: Optional[Playwright] = None
		self.browser: Optional[Browser] = None
		self.context = None
		self.page: Optional[Page] = None

	def _get_temp_user_data_dir(self) -> str:
		# Create a temporary directory for the user profile to avoid conflicts
		import tempfile
		return tempfile.mkdtemp()

	def start(self) -> Page:
		"""
		Manually starts the browser, connects Playwright, and returns a Page object.
		
		**You are responsible for calling stop() when you are finished.**
		"""
		if not self.page:
			logger_config.info("Starting browser manager...")
			try:
				if self.use_neko:
					self._launch_neko()
				if not self.debug_ws_url:
					self.use_neko = False
					self._launch_local_browser()

				if not self.debug_ws_url:
					raise ConnectionError("Failed to get a browser debugging WebSocket URL.")

				self.playwright = sync_playwright().start()
				self.browser = self.playwright.chromium.connect_over_cdp(self.debug_ws_url)
				
				self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
				self.page = self.get_current_tab()

				self.page.goto(self.url)
				self.page.wait_for_load_state("networkidle", timeout=30000)
				
				logger_config.success("Browser started successfully. Remember to call stop().")
				return self.page

			except Exception as e:
				logger_config.error(f"Failed during browser startup: {e}")
				self.stop() # Attempt cleanup on startup failure
				raise

		return self.page

	def stop(self):
		"""
		Manually stops the browser and cleans up all associated resources.
		"""
		logger_config.info("Stopping browser manager and cleaning up resources...")
		# The order of closing is important: from page up to the process
		if self.page and not self.page.is_closed():
			self.page.close()
		if self.context:
			self.context.close()
		if self.playwright:
			self.playwright.stop()
		
		self._stop_browser_process()

		if self.user_data_dir and "temp" in self.user_data_dir:
			shutil.rmtree(self.user_data_dir, ignore_errors=True)
			logger_config.info(f"Cleaned up temporary user data dir: {self.user_data_dir}")

		logger_config.success("Cleanup complete.")

	def new_page(self):
		return self.context.new_page()

	def get_current_tab(self):
		pages = self.context.pages
		current_page = pages[-1]  # Assume the last one is the active/current tab

		if not current_page:
			return self.new_page()

		if self.close_other_tab:
			# Close all other tabs
			for page in pages:
				if page != current_page:
					try:
						page.close()
					except Exception as e:
						logger_config.warning(f"Failed to close a tab: {e}")

		current_page.bring_to_front()
		return current_page

	def __enter__(self) -> Page:
		"""Context manager entry point. Calls start()."""
		return self.start()

	def __del__(self) -> Page:
		self.stop()

	def __exit__(self, exc_type, exc_val, exc_tb):
		"""Context manager exit point. Calls stop()."""
		self.stop()

	def _launch_local_browser(self):
		"""Launch browser on the local machine with remote debugging."""
		self.save_active_window()
		cmd = [
			self.browser_executable,
			f"--remote-debugging-port={self.debugging_port}",
			f"--user-data-dir={self.user_data_dir}",
		]
		if self.headless:
			cmd.append("--headless=new")


		self.browser_process = subprocess.Popen(
			cmd,
			stdout=subprocess.PIPE,	# Capture stdout
			stderr=subprocess.DEVNULL, # Suppress stderr
			text=True,				 # Decode bytes to str automatically
			bufsize=1,				  # Line-buffered
			env={**os.environ, 'PYTHONUNBUFFERED': '1'}
		)
		logger_config.info(f"Browser launched with PID: {self.browser_process.pid} on port {self.debugging_port}")
		
		logger_config.info("wait 10 minute before start", seconds=10)

		# We need to fetch the WebSocket URL from the JSON endpoint
		try:
			response = requests.get(f"http://localhost:{self.debugging_port}/json/version", timeout=5)
			response.raise_for_status()
			data = response.json()
			self.debug_ws_url = data["webSocketDebuggerUrl"]
			logger_config.success(f"Connected to debugger: {self.debug_ws_url}")
			self.minimize_active_window()
			self.restore_previous_focus()
		except requests.RequestException as e:
			raise ConnectionError(f"Could not connect to browser's debugging port {self.debugging_port}. Is the browser running correctly? Error: {e}")

	def _launch_neko(self):
		if not os.path.exists(self.neko_dir):
			logger_config.warning(f"Does not exists:: {self.neko_dir}")
			logger_config.warning("Git clone :: https://github.com/jebin2/browser_manager.git for neko docker with remote debugging")
			logger_config.warning("Failed to connect neko docker. falling back to local browser")
			return

		cmd = [
			"./run-neko.sh",
			"-p", self.user_data_dir,
		]

		self.browser_process = subprocess.Popen(
			cmd,
			stdout=subprocess.PIPE,	# Capture stdout
			stderr=subprocess.DEVNULL, # Suppress stderr
			text=True,				 # Decode bytes to str automatically
			bufsize=1,				 # Line-buffered
			env={**os.environ, 'PYTHONUNBUFFERED': '1'},
			cwd=self.neko_dir
		)

		captured_lines = []
		server_port = None
		# Read only first 8 lines
		for _ in range(8):
			line = self.browser_process.stdout.readline()
			if not line:
				break  # EOF
			captured_lines.append(line.strip())
			match = re.search(r"Debug port:\s*(\d+)", line)
			if match:
				self.debugging_port = match.group(1)  # Return as integer
			match = re.search(r"Server port:\s*(\d+)", line)
			if match:
				server_port = match.group(1)  # Return as integer
		# Using virtual display :152
		# Using server port: 8080
		# Using debug port: 9223
		# Starting Neko with:
		# Server port: 8080
		# Debug port: 9223
		# Chrome profile: /home/jebineinstein/git/neko-remote-debugging/chrome-profile
		# Local IP: 172.28.156.132
		logger_config.info(f"Browser launched with PID: {self.browser_process.pid} on port {self.debugging_port} and server_port {server_port}")

		logger_config.info("wait 10 minute before start", seconds=10)

		try:
			response = requests.get(f"http://localhost:{self.debugging_port}/json/version", timeout=5)
			response.raise_for_status()
			data = response.json()
			self.debug_ws_url = data["webSocketDebuggerUrl"]
			logger_config.success(f"Connected to debugger: {self.debug_ws_url}")
		except:
			logger_config.warning("Failed to connect neko docker. falling back to local browser")
			self.debug_ws_url = None

	def _stop_browser_process(self):
		"""Properly terminate the browser process and all its children using psutil."""
		if not self.browser_process:
			return

		logger_config.info(f"Stopping browser process tree (PID: {self.browser_process.pid})...")
		try:
			parent = psutil.Process(self.browser_process.pid)
			children = parent.children(recursive=True)
			for child in children:
				child.terminate()
			parent.terminate()
			
			# Wait for processes to die
			gone, alive = psutil.wait_procs(children + [parent], timeout=5)
			for p in alive:
				logger_config.warning(f"Process {p.pid} did not terminate gracefully, killing it.")
				p.kill()

			logger_config.success("Browser process stopped.")
		except psutil.NoSuchProcess:
			logger_config.warning("Browser process was already gone.")
		except Exception as e:
			logger_config.error(f"Error stopping browser process: {e}")
		finally:
			self.browser_process = None

	def save_active_window(self):
		if self.minimize_bw_restore_current_window_focus and not self.use_neko:
			"""Save the currently active window handle for later restoration"""
			script_dir = os.path.dirname(os.path.abspath(__file__))
			ps_script = os.path.join(script_dir, "window_control.ps1")
			subprocess.run([POWERSHELL_PATH, "-ExecutionPolicy", "Bypass", "-File", ps_script, "-action", "save"], check=True)

	def minimize_active_window(self):
		if self.minimize_bw_restore_current_window_focus and not self.use_neko:
			"""Function to minimize the currently active window"""
			script_dir = os.path.dirname(os.path.abspath(__file__))
			ps_script = os.path.join(script_dir, "window_control.ps1")
			subprocess.run([POWERSHELL_PATH, "-ExecutionPolicy", "Bypass", "-File", ps_script, "-action", "minimize"], check=True)

	def restore_previous_focus(self):
		if self.minimize_bw_restore_current_window_focus and not self.use_neko:
			"""Function to restore focus to the previously saved window with click"""
			script_dir = os.path.dirname(os.path.abspath(__file__))
			ps_script = os.path.join(script_dir, "window_control.ps1")
			subprocess.run([POWERSHELL_PATH, "-ExecutionPolicy", "Bypass", "-File", ps_script, "-action", "restoreFocus"], check=True)

# --- How to use it ---
if __name__ == "__main__":
	print("Running browser automation example...")

	try:
		browser_manager = BrowserManager()
		with browser_manager as page:
			logger_config.info(f"Page title: {page.title()}")
			page.get_by_role("combobox", name="Search").fill("Playwright context manager")
			page.get_by_role("button", name="Google Search").first.click()
			page.wait_for_load_state("networkidle")
			logger_config.info(f"New page title: {page.title()}")
			logger_config.info("wait", seconds=300)
		
		logger_config.success("Example run completed successfully.")

	except Exception as e:
		logger_config.error(f"An error occurred during the example run: {e}")