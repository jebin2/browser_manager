from custom_logger import logger_config
import os
import subprocess
import shutil
import secrets
import string
from .browser_config import BrowserConfig
from .browser_launcher import BrowserLauncher
from .browser_launch_error import BrowserLaunchError
import socket
import glob

class NekoBrowserLauncher(BrowserLauncher):
	"""Launches browser using Neko Docker container."""

	def generate_random_string(self, length=10):
		characters = string.ascii_letters
		random_string = ''.join(secrets.choice(characters) for _ in range(length))
		return random_string.lower()
	
	def _get_available_ports(self, config: BrowserConfig):
		if config.host_network:
			return 8080, 9223

		def is_port_in_use_by_docker(port):
			try:
				result = subprocess.run(
					["docker", "ps", "--format", "{{.Ports}}"],
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
					text=True,
				)
				for line in result.stdout.strip().splitlines():
					if f":{port}->" in line or f":{port}/" in line:
						return True
			except Exception:
				# Ignore if Docker not installed
				return False
			return False

		def find_free_port(start, end):
			for port in range(start, end + 1):
				if is_port_in_use_by_docker(port):
					continue
				with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
					try:
						s.bind(("", port))
						return port
					except OSError:
						continue
			return None

		port1 = find_free_port(config.starting_server_port_to_check, config.starting_server_port_to_check+500)
		port2 = find_free_port(config.starting_debug_port_to_check, config.starting_debug_port_to_check+500)

		return port1, port2

	def _docker_image_exists(self, image_name: str) -> bool:
		try:
			logger_config.info("Checking for docker image exists.")
			subprocess.run(
				["docker", "images", "-q", image_name],
				capture_output=True,
				text=True,
				check=True
			)
			return True
		except subprocess.CalledProcessError:
			return False

	def stop_docker(self, config: BrowserConfig) -> bool:
		try:
			logger_config.info(f"Stopping existing Docker container: {config.docker_name}")
			
			# Check if container exists
			result = subprocess.run(
				["docker", "ps", "-a", "--format", "{{.Names}}"],
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=True,
				check=True,
			)
			container_names = result.stdout.strip().splitlines()
			
			if config.docker_name in container_names:
				logger_config.info(f"Container {config.docker_name} found. Removing it...")
				subprocess.run(
					["docker", "kill", config.docker_name],
					check=False
				)
				logger_config.info("wait", seconds=5)
				subprocess.run(
					["docker", "rm", "-f", config.docker_name],
					check=True
				)
				logger_config.info("wait", seconds=5)
				logger_config.info(f"Container {config.docker_name} stopped and removed successfully.")
				return True
			else:
				logger_config.info(f"No container named {config.docker_name} found.")
				return False

		except subprocess.CalledProcessError as e:
			logger_config.error(f"Error stopping Docker container {config.docker_name}: {e}")
			raise

	def _start_screenshot_loop(self, config: BrowserConfig, interval=2):
		# Build the exact command string
		cmd = (
			f"while true; do "
			f"docker exec {config.docker_name} scrot /tmp/neko_screen.png && "
			f"mkdir -p ./{config.docker_name} && "
			f"docker cp {config.docker_name}:/tmp/neko_screen.png ./{config.docker_name}/neko_$(date +%Y%m%d_%H%M%S).png; "
			f"sleep {interval}; "
			f"done"
		)

		# Check if process is already running
		check = subprocess.run(
			["pgrep", "-af", "scrot /tmp/neko_screen.png"],
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
		)

		if check.stdout.strip():  # Found running process
			logger_config.warning("[SKIP] Screenshot loop already running.")
			return

		# Start new background loop
		subprocess.Popen(["bash", "-c", cmd])
		logger_config.info(f"[BG] Screenshot loop started every {interval}s → saving in {config.docker_name}/")

	def choose_file_via_xdotool(self, config: BrowserConfig, file_path):
		print("[STEP 3] Using direct input approach...")
		
		try:
			# Wait for dialog to stabilize
			logger_config.info("Make sure to install xdotool and set AllowFileSelectionDialogs->True, URLAllowlist->folder path to make the volue and URLBlocklist to empty.")
			logger_config.info("Wait for dialog to appear.", seconds=3)
			
			# Send Alt+Tab to ensure dialog focus
			# subprocess.run([
			#     'docker', 'exec', neko_container_name,
			#     'xdotool', 'key', 'alt+Tab'
			# ], timeout=3)
			# time.sleep(1)
			
			# Focus address bar
			subprocess.run([
				'docker', 'exec', config.docker_name,
				'xdotool', 'key', 'ctrl+l'
			], timeout=3)
			logger_config.info("Wait for commad finish.", seconds=1)
			
			# Type file path
			subprocess.run([
				'docker', 'exec', config.docker_name,
				'xdotool', 'type', file_path
			], timeout=5)
			logger_config.info("Wait for commad finish.", seconds=1)
			
			# Press Enter
			subprocess.run([
				'docker', 'exec', config.docker_name,
				'xdotool', 'key', 'Return'
			], timeout=3)
			
			return True
			
		except subprocess.TimeoutExpired:
			print("[ERROR] Direct input method timed out")
			return False

	def launch(self, config: BrowserConfig) -> tuple[subprocess.Popen, str]:
		"""Launch Neko browser container."""
		if not self._docker_image_exists(config.neko_docker_cmd.split(" ")[-1]):
			logger_config.info("Please Follow This to install: https://github.com/jebin2/neko-apps/blob/master/chrome-remote-debug/README.md")
			raise BrowserLaunchError(f"Neko directory not found: {config.neko_dir}")

		self.stop_docker(config)
		self.clean_browser_profile(config)

		server_port, debug_port = self._get_available_ports(config)
		config.server_port = server_port
		config.debug_port = debug_port
		cmd = config.neko_docker_cmd
		logger_config.info(f'Command to run: {cmd}')
		try:
			process = subprocess.Popen(
				cmd,
                stdout=None,  # inherit from parent
                stderr=None,  # inherit from parent
				text=True,
				bufsize=1,
				env={**os.environ, 'PYTHONUNBUFFERED': '1'},
				shell=True
			)
			logger_config.info(f"Neko browser launched with PID: {process.pid}")

			ws_url = self._get_websocket_url(debug_port, config.connection_timeout)
			
			logger_config.info(f"Neko browser launched with PID: {process.pid} with port {debug_port} with server port {server_port}")
			if config.take_screenshot:
				self._start_screenshot_loop(config)
			return process, ws_url
			
		except Exception as e:
			raise BrowserLaunchError(f"Failed to launch Neko browser: {e}")
	
	def cleanup(self, config: BrowserConfig, process: subprocess.Popen) -> None:
		"""Clean up Neko process."""
		try:
			self.stop_docker(config)
			if process:
				try:
					process.terminate()
					process.wait(timeout=5)
				except subprocess.TimeoutExpired:
					process.kill()
				logger_config.info("Neko process cleaned up")
		except Exception as e:
			logger_config.error(f"Error during neko browser cleanup: {e}")