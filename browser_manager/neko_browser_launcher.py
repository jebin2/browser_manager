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

class NekoBrowserLauncher(BrowserLauncher):
	"""Launches browser using Neko Docker container."""

	def generate_random_string(self, length=10):
		characters = string.ascii_letters
		random_string = ''.join(secrets.choice(characters) for _ in range(length))
		return random_string.lower()

	def _get_available_ports(self):
		def find_free_port(start, end):
			for port in range(start, end + 1):
				with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
					try:
						s.bind(("", port))
						return port
					except OSError:
						continue
			return None

		port1 = find_free_port(8080, 8999)
		port2 = find_free_port(9223, 9999)

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

	def clean_browser_profile(self, config: BrowserConfig):
		"""
		Clean up a browser profile directory by removing lock files and caches.

		Args:
			profile_path (str): Path to the browser user data directory.
		"""
		if config.delete_user_data_dir_singleton_lock:
			profile_path = config.user_data_dir
			# Remove Singleton* files
			for filename in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
				file_path = os.path.join(profile_path, filename)
				if os.path.exists(file_path):
					os.remove(file_path)
					print(f"Removed {file_path}")

			# Remove lockfile
			lockfile_path = os.path.join(profile_path, "lockfile")
			if os.path.exists(lockfile_path):
				os.remove(lockfile_path)
				print(f"Removed {lockfile_path}")

			# Remove Extensions directory
			extensions_path = os.path.join(profile_path, "Extensions")
			if os.path.exists(extensions_path):
				shutil.rmtree(extensions_path)
				print(f"Removed {extensions_path}")

			# Remove GPUCache directory
			gpu_cache_path = os.path.join(profile_path, "GPUCache")
			if os.path.exists(gpu_cache_path):
				shutil.rmtree(gpu_cache_path)
				print(f"Removed {gpu_cache_path}")

	def stop_docker(self, config: BrowserConfig) -> bool:
		try:
			logger_config.info(f"Stopping the existing docker {config.docker_name} if there.")
			subprocess.run(
				["docker", "rm", "-f", config.docker_name],
				capture_output=True,
				text=True,
				check=True
			)
			return True
		except subprocess.CalledProcessError:
			return False

	def launch(self, config: BrowserConfig) -> tuple[subprocess.Popen, str]:
		"""Launch Neko browser container."""
		if not self._docker_image_exists(config.neko_docker_cmd.split(" ")[-1]):
			logger_config.info("Please Follow This to install: https://github.com/jebin2/neko-apps/blob/master/chrome-remote-debug/README.md")
			raise BrowserLaunchError(f"Neko directory not found: {config.neko_dir}")

		self.stop_docker(config)
		self.clean_browser_profile(config)

		server_port, debug_port = self._get_available_ports()
		cmd = (
			config.neko_docker_cmd
			.replace("server_port", str(server_port))
			.replace("debug_port", str(debug_port))
			.replace("docker_name", config.docker_name)
			.replace("user_data_dir", config.user_data_dir)
		)
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

			ws_url = self._get_websocket_url(debug_port, config.connection_timeout)
			
			logger_config.info(f"Neko browser launched with PID: {process.pid} with port {debug_port} with server port {server_port}")
			return process, ws_url
			
		except Exception as e:
			raise BrowserLaunchError(f"Failed to launch Neko browser: {e}")
	
	def cleanup(self, config: BrowserConfig, process: subprocess.Popen) -> None:
		"""Clean up Neko process."""
		if process:
			try:
				self.stop_docker(config)
				process.terminate()
				process.wait(timeout=5)
			except subprocess.TimeoutExpired:
				process.kill()
			logger_config.info("Neko process cleaned up")