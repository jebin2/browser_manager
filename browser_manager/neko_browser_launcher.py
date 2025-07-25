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
	
	def _get_available_ports(self):
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
			config (BrowserConfig): Contains user_data_dir and cleanup flags.
		"""
		if not config.delete_user_data_dir_singleton_lock:
			return

		profile_path = config.user_data_dir

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
					["docker", "rm", "-f", config.docker_name],
					check=True
				)
				logger_config.info(f"Container {config.docker_name} stopped and removed successfully.")
				return True
			else:
				logger_config.info(f"No container named {config.docker_name} found.")
				return False

		except subprocess.CalledProcessError as e:
			logger_config.error(f"Error stopping Docker container {config.docker_name}: {e}")
			raise


	def launch(self, config: BrowserConfig) -> tuple[subprocess.Popen, str]:
		"""Launch Neko browser container."""
		if not self._docker_image_exists(config.neko_docker_cmd.split(" ")[-1]):
			logger_config.info("Please Follow This to install: https://github.com/jebin2/neko-apps/blob/master/chrome-remote-debug/README.md")
			raise BrowserLaunchError(f"Neko directory not found: {config.neko_dir}")

		self.stop_docker(config)
		self.clean_browser_profile(config)

		server_port, debug_port = self._get_available_ports()
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