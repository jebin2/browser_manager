from custom_logger import logger_config
import os
import subprocess
import re
import secrets
import string
from .browser_config import BrowserConfig
from .browser_launcher import BrowserLauncher
from .browser_launch_error import BrowserLaunchError

class NekoBrowserLauncher(BrowserLauncher):
	"""Launches browser using Neko Docker container."""

	def generate_random_string(self, length=10):
		characters = string.ascii_letters
		random_string = ''.join(secrets.choice(characters) for _ in range(length))
		return random_string.lower()
	
	def launch(self, config: BrowserConfig) -> tuple[subprocess.Popen, str]:
		"""Launch Neko browser container."""
		if not os.path.exists(config.neko_dir):
			raise BrowserLaunchError(f"Neko directory not found: {config.neko_dir}")
		
		cmd = ["./run-neko.sh", "-p", config.user_data_dir, "-i", self.generate_random_string()]
		
		try:
			process = subprocess.Popen(
				cmd,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=True,
				bufsize=1,
				env={**os.environ, 'PYTHONUNBUFFERED': '1'},
				cwd=config.neko_dir
			)
			
			debug_port, server_port = self._parse_neko_output(process)
			ws_url = self._get_websocket_url(debug_port, config.connection_timeout)
			
			logger_config.info(f"Neko browser launched with PID: {process.pid} with port {debug_port} with server port {server_port}")
			return process, ws_url
			
		except Exception as e:
			raise BrowserLaunchError(f"Failed to launch Neko browser: {e}")
	
	def _parse_neko_output(self, process: subprocess.Popen) -> int:
		"""Parse Neko output to extract debugging port."""
		debug_port = None
		server_port = None

		while debug_port is None or server_port is None:
			line = process.stdout.readline()
			if not line:
				continue
			print(line)

			match = re.search(r"Debug port:\s*(\d+)", line)
			if match:
				debug_port = int(match.group(1))

			match = re.search(r"Server port:\s*(\d+)", line)
			if match:
				server_port = int(match.group(1))
		
		if debug_port is None:
			raise BrowserLaunchError("Could not determine Neko debug port")
		logger_config.info("Server started.")
		return debug_port, server_port
	
	def cleanup(self, process: subprocess.Popen) -> None:
		"""Clean up Neko process."""
		if process:
			try:
				process.terminate()
				process.wait(timeout=5)
			except subprocess.TimeoutExpired:
				process.kill()
			logger_config.info("Neko process cleaned up")