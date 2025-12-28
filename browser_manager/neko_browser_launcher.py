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
			result = subprocess.run(
				["docker", "images", "-q", image_name],
				capture_output=True,
				text=True,
				check=True
			)
			# Check if the result has any output (image ID)
			return bool(result.stdout.strip())
		except subprocess.CalledProcessError:
			return False

	def _build_neko_image(self, application: str = "chrome-remote-debug", base_image: str = "ghcr.io/m1k1o/neko/base:latest") -> bool:
		"""
		Clone neko-apps repo to /tmp, build the specified application image, and clean up.
		
		Args:
			application: The neko application to build (default: chrome-remote-debug)
			base_image: The base image to use for building (default: ghcr.io/m1k1o/neko/base:latest)
		
		Returns:
			True if build was successful, False otherwise
		"""
		neko_apps_dir = "/tmp/neko-apps"
		neko_repo_url = "https://github.com/jebin2/neko-apps.git"
		
		try:
			# Clean up any existing clone
			if os.path.exists(neko_apps_dir):
				logger_config.info(f"Removing existing neko-apps directory: {neko_apps_dir}")
				shutil.rmtree(neko_apps_dir)
			
			# Clone the repository
			logger_config.info(f"Cloning neko-apps from {neko_repo_url} to {neko_apps_dir}")
			clone_result = subprocess.run(
				["git", "clone", neko_repo_url, neko_apps_dir],
				capture_output=True,
				text=True,
				check=True
			)
			logger_config.info(f"Clone successful: {clone_result.stdout}")
			
			# Run the build script
			build_script = os.path.join(neko_apps_dir, "build")
			if not os.path.exists(build_script):
				logger_config.error(f"Build script not found at {build_script}")
				return False
			
			# Make build script executable
			os.chmod(build_script, 0o755)
			
			build_cmd = [
				build_script,
				"-y",
				"--application", application,
				"--base_image", base_image
			]
			
			logger_config.info(f"Running build command: {' '.join(build_cmd)}")
			build_result = subprocess.run(
				build_cmd,
				cwd=neko_apps_dir,
				capture_output=True,
				text=True,
				check=True
			)
			logger_config.info(f"Build output: {build_result.stdout}")
			if build_result.stderr:
				logger_config.warning(f"Build stderr: {build_result.stderr}")
			
			logger_config.info("Neko image built successfully!")
			return True
			
		except subprocess.CalledProcessError as e:
			logger_config.error(f"Build failed with return code {e.returncode}")
			logger_config.error(f"stdout: {e.stdout}")
			logger_config.error(f"stderr: {e.stderr}")
			return False
		except Exception as e:
			logger_config.error(f"Error during neko image build: {e}")
			return False
		finally:
			# Clean up: remove the cloned repository
			if os.path.exists(neko_apps_dir):
				logger_config.info(f"Cleaning up: removing {neko_apps_dir}")
				try:
					shutil.rmtree(neko_apps_dir)
				except Exception as e:
					logger_config.warning(f"Failed to clean up {neko_apps_dir}: {e}")

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
		# Build the exact command string with atomic file operations
		# Uses temp file + mv to prevent partial reads
		cmd = (
			f"while true; do "
			f"if docker ps -a --format '{{{{.Names}}}}' | grep -q '^{config.docker_name}$'; then "
			f"    TS=$(date +%Y%m%d_%H%M%S); "
			f"    docker exec {config.docker_name} scrot /tmp/neko_$TS.png && "
			f"    mkdir -p ./{config.docker_name} && "
			f"    docker cp {config.docker_name}:/tmp/neko_$TS.png ./{config.docker_name}/screenshot_tmp.png && "
			f"    mv ./{config.docker_name}/screenshot_tmp.png ./{config.docker_name}/screenshot.png; "
			f"else "
			f"    echo '[STOP] Container {config.docker_name} not found. Exiting loop.'; "
			f"    break; "
			f"fi; "
			f"sleep {interval}; "
			f"done"
		)

		# Check if a process is already running for this *specific* container
		# We make the pgrep search string unique to the container
		check_string = f"docker exec {config.docker_name} scrot"
		check = subprocess.run(
			["pgrep", "-af", check_string],
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
		)

		if check.stdout.strip():  # Found running process for this container
			logger_config.warning(f"[SKIP] Screenshot loop already running for {config.docker_name}.")
			return

		# Start new background loop
		self.screenshot_process = subprocess.Popen(["bash", "-c", cmd])

		import atexit, signal
		def cleanup():
			print(f"Stopping screenshot process: {self.screenshot_process.pid}")
			try:
				os.kill(self.screenshot_process.pid, signal.SIGKILL)
			except ProcessLookupError:
				pass

		atexit.register(cleanup)
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
				'xdotool', 'type', f'{config.neko_attach_folder}/{file_path}'
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
		image_name = config.neko_docker_cmd.split(" ")[-1]
		if not self._docker_image_exists(image_name):
			logger_config.info(f"Docker image {image_name} not found. Attempting to build...")
			if not self._build_neko_image():
				logger_config.error("Failed to build neko image. Please follow: https://github.com/jebin2/neko-apps/blob/master/chrome-remote-debug/README.md")
				raise BrowserLaunchError(f"Failed to build neko Docker image: {image_name}")

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

			# Cleanup function
			import atexit
			def cleanup():
				print(f"Stopping container: {config.docker_name}")
				subprocess.run(["docker", "kill", config.docker_name], check=False)

			# Register cleanup
			atexit.register(cleanup)
			return process, ws_url
			
		except Exception as e:
			raise BrowserLaunchError(f"Failed to launch Neko browser: {e}")
	
	def _graceful_close_chrome(self, config: BrowserConfig) -> bool:
		"""Gracefully close Chrome inside the container to prevent 'Restore pages' popup.
		
		This sends SIGTERM to chrome processes which allows Chrome to save its state
		and exit cleanly, preventing the crash recovery dialog on next launch.
		"""
		import time
		
		try:
			# Check if container is running
			result = subprocess.run(
				["docker", "ps", "--format", "{{.Names}}"],
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=True,
			)
			if config.docker_name not in result.stdout.strip().splitlines():
				logger_config.info(f"Container {config.docker_name} not running, skipping graceful close")
				return True
			
			logger_config.info(f"Gracefully closing Chrome in container {config.docker_name}...")
			
			# Try multiple methods to gracefully close Chrome
			# Method 1: Try killall (more commonly available than pkill)
			kill_cmd = subprocess.run(
				["docker", "exec", config.docker_name, "killall", "-TERM", "chrome"],
				check=False,
				timeout=5,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE
			)
			
			# Method 2: If killall failed, try using shell to find and kill processes
			if kill_cmd.returncode != 0:
				logger_config.info("killall not available, trying shell-based kill...")
				# Use shell command to find chrome PIDs and send SIGTERM
				subprocess.run(
					["docker", "exec", config.docker_name, "sh", "-c", 
					 "for pid in $(ps aux | grep -i chrome | grep -v grep | awk '{print $2}'); do kill -TERM $pid 2>/dev/null; done"],
					check=False,
					timeout=10,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE
				)
			
			# Wait for Chrome to close gracefully
			time.sleep(3)
			
			# Verify Chrome has exited using ps
			check_result = subprocess.run(
				["docker", "exec", config.docker_name, "sh", "-c",
				 "ps aux | grep -i chrome | grep -v grep"],
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=True,
				timeout=5
			)
			
			if check_result.stdout.strip():
				logger_config.warning("Chrome still running, forcing kill...")
				subprocess.run(
					["docker", "exec", config.docker_name, "sh", "-c",
					 "for pid in $(ps aux | grep -i chrome | grep -v grep | awk '{print $2}'); do kill -9 $pid 2>/dev/null; done"],
					check=False,
					timeout=10,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE
				)
				time.sleep(1)
			
			logger_config.info("Chrome closed gracefully")
			return True
			
		except subprocess.TimeoutExpired:
			logger_config.warning("Timeout during graceful Chrome close")
			return False
		except Exception as e:
			logger_config.warning(f"Error during graceful Chrome close: {e}")
			return False

	def cleanup(self, config: BrowserConfig, process: subprocess.Popen) -> None:
		"""Clean up Neko process."""
		try:
			# Stop screenshot loop if running
			if hasattr(self, 'screenshot_process') and self.screenshot_process:
				try:
					self.screenshot_process.terminate()
					self.screenshot_process.wait(timeout=5)
					logger_config.info("Screenshot process stopped")
				except subprocess.TimeoutExpired:
					self.screenshot_process.kill()
					logger_config.warning("Screenshot process force killed")
				except Exception as e:
					logger_config.warning(f"Error stopping screenshot process: {e}")
			
			# Gracefully close Chrome first to prevent "Restore pages" popup
			self._graceful_close_chrome(config)
			
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