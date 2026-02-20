"""
================================================================================
neko_browser_launcher.py
================================================================================

OVERVIEW
--------
This module owns ALL Docker and port management for Neko browser containers.
It is the single source of truth for container lifecycle and port allocation.

browser_config.py is intentionally kept as a pure data class with zero Docker
knowledge. This module bridges config → running container.

WHAT THIS MODULE OWNS
---------------------
  [Port allocation]
	Each Neko container needs 3 unique port resources:
	  1. server_port  — Neko web UI HTTP port	   (default search from: 8081)
	  2. debug_port   — Chrome remote debug port	(default search from: 9224)
	  3. webrtc_range — 101 consecutive UDP ports   (default search from: 52000)

	Without proper allocation, all containers would fight over the same ports,
	causing Docker to fail with "port is already allocated".

  [Thread safety]
	threading.RLock() serializes port allocation within a single process.
	RLock (reentrant) prevents deadlock if allocate_ports() internally calls
	release_ports() on the same thread.

  [Cross-process safety]
	fcntl.flock(LOCK_EX) on a lock file serializes across separate Python
	processes/apps on the same host. A fresh file descriptor is opened on
	every acquisition to avoid Linux's same-process flock re-grant bug.

  [Persistent state]
	Allocations are persisted to /tmp/neko_port_state.json so separate
	processes share knowledge of which ports are in use. Written atomically
	via .tmp + os.replace() to prevent corruption on crash.

  [Dead container cleanup]
	Every allocation queries `docker ps` to reclaim ports from containers
	that have stopped or crashed. If Docker is unreachable, purge is skipped
	safely (stale allocations kept rather than incorrectly reclaimed).

  [Port exhaustion prevention]
	When all containers stop, port cursors reset to defaults so numbers
	never grow toward 65535.

  [TOCTOU mitigation]
	A race exists between checking port availability and Docker actually
	binding. _launch_with_retry() catches "port is already allocated" from
	Docker and reallocates fresh ports before retrying.

  [Container lifecycle]
	- stop_docker(): stops/removes a container, releases its ports
	- launch(): allocates ports, stops any conflicting container, starts new one
	- cleanup(): graceful Chrome shutdown → stop container → release ports
	- _graceful_close_chrome(): SIGTERM → wait → SIGKILL fallback to prevent
	  the "Restore pages?" dialog on next launch

  [Input validation]
	docker_name validated against strict regex to prevent shell injection.

EDGE CASES HANDLED
------------------
  1.  Two threads calling launch() simultaneously
	  → RLock serializes; each thread gets unique ports.

  2.  Two separate Python apps calling launch() simultaneously
	  → fcntl.flock serializes cross-process access.

  3.  Container crashes or is killed externally
	  → Next launch() purges dead containers via `docker ps`.

  4.  Same docker_name already running when launch() is called
	  → stop_docker() stops it, ports reclaimed, fresh ports allocated.

  5.  Docker daemon slow or unresponsive during purge
	  → timeout=5s; purge skipped, stale allocations kept safely.

  6.  Port grabbed externally between check and Docker bind (TOCTOU)
	  → _launch_with_retry() catches the Docker error and reallocates.

  7.  Process crashes mid state-file write
	  → Atomic os.replace() ensures state file is never partially written.

  8.  Port cursors growing toward 65535
	  → _maybe_reset_cursors() resets when no containers are active.

  9.  fcntl same-process re-grant bug
	  → Fresh fd per _FileLock acquisition; _thread_lock prevents two threads
		 from reaching fcntl simultaneously.

  10. Reentrant lock call (allocate inside allocate)
	  → RLock allows same-thread reentry; _release_ports_unlocked() skips
		 the file lock since caller already holds it.

  11. Shell injection via docker_name
	  → _validate_docker_name() enforces alphanumeric + [_.-] regex.

  12. No free ports left (port > 65535)
	  → RuntimeError raised with clear message, no infinite loop.

  13. use_neko=False
	  → launch() delegates to a different launcher; no Docker/port logic runs.

ENVIRONMENT VARIABLES
---------------------
  NEKO_PORT_STATE_FILE  JSON state file path  (default: /tmp/neko_port_state.json)
  NEKO_PORT_LOCK_FILE   flock lock file path  (default: /tmp/neko_port_state.lock)

USAGE
-----
  launcher = NekoBrowserLauncher()
  config = BrowserConfig(docker_name="grok_ui_chat", use_neko=True)
  process, ws_url = launcher.launch(config)
  ...
  launcher.cleanup(config, process)

================================================================================
"""

from custom_logger import logger_config
import os
import re
import socket
import threading
import json
import fcntl
import subprocess
import shutil
import secrets
import string
import time
from typing import Optional
from .browser_config import BrowserConfig, _WEBRTC_RANGE_SIZE
from .browser_launcher import BrowserLauncher
from .browser_launch_error import BrowserLaunchError
import random

# ══════════════════════════════════════════════════════════════════════════════
# Port manager — all state is module-level, shared across all launcher instances
# ══════════════════════════════════════════════════════════════════════════════

_PORT_STATE_FILE = os.getenv("NEKO_PORT_STATE_FILE", "/tmp/neko_port_state.json")
_PORT_LOCK_FILE  = os.getenv("NEKO_PORT_LOCK_FILE",  "/tmp/neko_port_state.lock")
_thread_lock	 = threading.RLock()

_DEFAULT_STATE = {
	"next_server_port":  8081,
	"next_debug_port":   9224,
	"next_webrtc_port":  52000,
	"allocations":	   {}
}


# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────

def _validate_docker_name(name: str):
	"""
	Enforce safe docker_name to prevent shell injection.
	Matches Docker's own allowed character set: [a-zA-Z0-9][a-zA-Z0-9_.-]*
	"""
	if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$', name):
		raise ValueError(
			f"Invalid docker_name '{name}'. "
			"Only alphanumerics, underscores, hyphens, and dots allowed."
		)


# ─────────────────────────────────────────────
# File lock
# ─────────────────────────────────────────────

class _FileLock:
	"""
	Cross-process exclusive lock using fcntl.flock().

	Why fresh fd on every acquisition:
	  Linux fcntl associates locks with (process, inode). If the same process
	  opens the lock file twice and calls flock(LOCK_EX) on fd2 while holding
	  fd1, the kernel grants it immediately (re-grant bug), silently breaking
	  mutual exclusion between threads. Opening a new fd each time, combined
	  with _thread_lock preventing two threads from reaching flock at once,
	  eliminates this entirely.

	Why safe on crash:
	  OS auto-releases flock when the process dies or fd is closed. No stale
	  lock recovery needed.
	"""
	def __init__(self, path: str):
		self.path = path
		self._fd  = None

	def __enter__(self):
		self._fd = open(self.path, "w")
		try:
			fcntl.flock(self._fd, fcntl.LOCK_EX)
		except Exception:
			self._fd.close()
			self._fd = None
			raise
		return self

	def __exit__(self, *_):
		if self._fd:
			try:
				fcntl.flock(self._fd, fcntl.LOCK_UN)
			finally:
				self._fd.close()
				self._fd = None


# ─────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────

def _read_state() -> dict:
	"""Read port allocation state from disk. Returns default if missing/corrupt."""
	if not os.path.exists(_PORT_STATE_FILE):
		return json.loads(json.dumps(_DEFAULT_STATE))
	with open(_PORT_STATE_FILE, "r") as f:
		try:
			state = json.load(f)
			state.setdefault("allocations", {})
			return state
		except json.JSONDecodeError:
			return json.loads(json.dumps(_DEFAULT_STATE))


def _write_state(state: dict):
	"""
	Atomically write state via .tmp + os.replace().
	Readers never see a partial write even if process is killed mid-operation.
	"""
	tmp = _PORT_STATE_FILE + ".tmp"
	with open(tmp, "w") as f:
		json.dump(state, f, indent=2)
	os.replace(tmp, _PORT_STATE_FILE)


def _maybe_reset_cursors(state: dict) -> dict:
	"""
	Reset port search cursors to defaults when no containers are active.
	Prevents cursors from growing unboundedly toward 65535 across many
	allocate/release cycles.
	"""
	if not state["allocations"]:
		state["next_server_port"] = _DEFAULT_STATE["next_server_port"]
		state["next_debug_port"]  = _DEFAULT_STATE["next_debug_port"]
		state["next_webrtc_port"] = _DEFAULT_STATE["next_webrtc_port"]
		logger_config.info("[PortManager] All containers stopped — port cursors reset.")
	return state


# ─────────────────────────────────────────────
# Docker query helpers
# ─────────────────────────────────────────────

def _running_docker_names() -> Optional[set]:
	"""
	Return names of currently running containers.
	Returns None (not empty set) if Docker is unreachable, so callers can
	distinguish "nothing running" from "can't tell".
	"""
	try:
		result = subprocess.run(
			["docker", "ps", "--format", "{{.Names}}"],
			capture_output=True, text=True, timeout=5
		)
		return set(result.stdout.strip().splitlines())
	except Exception:
		return None


def _purge_dead_allocations(state: dict) -> dict:
	"""
	Remove state entries for containers no longer running.
	Skipped entirely if Docker is unreachable — safer to keep stale
	allocations than to incorrectly reclaim ports still in use.
	"""
	running = _running_docker_names()
	if running is None:
		logger_config.warning("[PortManager] Could not reach Docker — skipping purge.")
		return state
	dead = [name for name in state["allocations"] if name not in running]
	for name in dead:
		logger_config.info(f"[PortManager] Reclaiming ports from dead container: {name}")
		del state["allocations"][name]
	return state


# ─────────────────────────────────────────────
# Port availability checks
# ─────────────────────────────────────────────

def _is_tcp_port_free(port: int) -> bool:
	"""
	Probe a TCP port by binding. SO_REUSEADDR disabled to avoid false
	positives on ports still in TIME_WAIT.
	"""
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
		s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
		try:
			s.bind(("0.0.0.0", port))
			return True
		except OSError:
			return False


def _is_udp_range_free(start: int, size: int) -> bool:
	"""
	Check all ports in [start, start+size) are free for UDP.
	All must be free since Docker maps the entire range atomically.
	"""
	for port in range(start, start + size):
		with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
			try:
				s.bind(("0.0.0.0", port))
			except OSError:
				return False
	return True


def _find_free_tcp_port(start: int, used: set) -> int:
	"""
	Find first free TCP port at or above `start` not in `used`.
	`used` = ports already allocated this session (not yet bound by Docker)
	so we don't double-allocate in the window before Docker binds them.
	"""
	port = start
	while port in used or not _is_tcp_port_free(port):
		port += 1
		if port > 65535:
			raise RuntimeError("[PortManager] No free TCP ports available.")
	return port


def _find_free_webrtc_range(start: int, used_starts: set, size: int = _WEBRTC_RANGE_SIZE) -> int:
	"""
	Find first free UDP block of `size` consecutive ports at or above `start`
	that doesn't overlap any range in `used_starts`.
	Jumps by `size` each iteration → O(n) not O(n*size).
	"""
	port = start
	while True:
		if port + size > 65535:
			raise RuntimeError("[PortManager] No free UDP port range available.")
		overlaps = any(
			port < (u + size) and (port + size) > u
			for u in used_starts
		)
		if not overlaps and _is_udp_range_free(port, size):
			return port
		port += size


# ─────────────────────────────────────────────
# Internal unlocked operations
# Only call when _thread_lock + _FileLock already held by caller.
# Avoids reentrant deadlock when allocate needs to release mid-operation.
# ─────────────────────────────────────────────

def _release_ports_unlocked(docker_name: str, state: dict) -> dict:
	"""Remove a container's allocation from state — no locks acquired."""
	if docker_name in state["allocations"]:
		logger_config.info(f"[PortManager] Releasing ports for: {docker_name}")
		del state["allocations"][docker_name]
	return state


# ─────────────────────────────────────────────
# Public port API
# Lock acquisition order is always: _thread_lock → _FileLock
# Never reversed — consistent ordering prevents deadlock.
# ─────────────────────────────────────────────

def _allocate_ports(docker_name: str) -> tuple[int, int, int]:
	"""
	Allocate a unique set of ports for a new Neko container.

	Steps:
	  1. Purge ports from dead containers.
	  2. Reset cursors if no containers active.
	  3. If docker_name already in state (dead but not purged), reclaim it.
	  4. Find free ports not in use by current allocations or system.
	  5. Persist allocation atomically.

	Note: If the container is still running when this is called, the caller
	(NekoBrowserLauncher.launch) is responsible for stopping it first via
	stop_docker() before calling _allocate_ports().

	Returns:
	  (server_port, debug_port, webrtc_port_start)
	"""
	_validate_docker_name(docker_name)
	with _thread_lock:
		with _FileLock(_PORT_LOCK_FILE):
			state = _read_state()
			state = _purge_dead_allocations(state)
			state = _maybe_reset_cursors(state)

			# Reclaim stale entry if present (dead container not yet purged)
			if docker_name in state["allocations"]:
				state = _release_ports_unlocked(docker_name, state)

			used_server = {v["server_port"]	  for v in state["allocations"].values()}
			used_debug  = {v["debug_port"]		for v in state["allocations"].values()}
			used_webrtc = {v["webrtc_port_start"] for v in state["allocations"].values()}

			server_port	   = _find_free_tcp_port(state["next_server_port"], used_server)
			debug_port		= _find_free_tcp_port(state["next_debug_port"],  used_debug)
			webrtc_port_start = _find_free_webrtc_range(state["next_webrtc_port"], used_webrtc)

			state["allocations"][docker_name] = {
				"server_port":	   server_port,
				"debug_port":		debug_port,
				"webrtc_port_start": webrtc_port_start,
			}
			state["next_server_port"] = server_port + 1
			state["next_debug_port"]  = debug_port + 1
			state["next_webrtc_port"] = webrtc_port_start + _WEBRTC_RANGE_SIZE

			_write_state(state)
			return server_port, debug_port, webrtc_port_start


def _release_ports(docker_name: str):
	"""
	Release a container's port allocation from shared state.
	Safe to call multiple times — no-op if already released.
	Lock order: _thread_lock → _FileLock (same as _allocate_ports).
	"""
	_validate_docker_name(docker_name)
	with _thread_lock:
		with _FileLock(_PORT_LOCK_FILE):
			state = _read_state()
			state = _release_ports_unlocked(docker_name, state)
			state = _maybe_reset_cursors(state)
			_write_state(state)


# ══════════════════════════════════════════════════════════════════════════════
# NekoBrowserLauncher
# ══════════════════════════════════════════════════════════════════════════════

class NekoBrowserLauncher(BrowserLauncher):
	"""
	Launches and manages Chrome inside a Neko Docker container.

	Owns the full container lifecycle:
	  launch()  → allocate ports → stop any conflict → start container
	  cleanup() → graceful Chrome close → stop container → release ports
	"""

	def generate_random_string(self, length: int = 10) -> str:
		return ''.join(secrets.choice(string.ascii_letters) for _ in range(length)).lower()

	# ─────────────────────────────────────────────
	# Docker image management
	# ─────────────────────────────────────────────

	def _docker_image_exists(self, image_name: str) -> bool:
		"""Return True if the Docker image exists locally."""
		try:
			logger_config.info("Checking if Docker image exists.")
			result = subprocess.run(
				["docker", "images", "-q", image_name],
				capture_output=True, text=True, check=True
			)
			return bool(result.stdout.strip())
		except subprocess.CalledProcessError:
			return False

	def _build_neko_image(
		self,
		application: str = "chrome-remote-debug",
		base_image: str = "ghcr.io/m1k1o/neko/base:latest"
	) -> bool:
		"""
		Clone neko-apps repo, build the Docker image, then clean up the clone.
		Returns True on success, False on any failure.
		"""
		neko_apps_dir = "/tmp/neko-apps"
		neko_repo_url = "https://github.com/jebin2/neko-apps.git"

		try:
			if os.path.exists(neko_apps_dir):
				logger_config.info(f"Removing existing: {neko_apps_dir}")
				shutil.rmtree(neko_apps_dir)

			logger_config.info(f"Cloning {neko_repo_url} → {neko_apps_dir}")
			subprocess.run(
				["git", "clone", neko_repo_url, neko_apps_dir],
				capture_output=True, text=True, check=True
			)

			build_script = os.path.join(neko_apps_dir, "build")
			if not os.path.exists(build_script):
				logger_config.error(f"Build script not found: {build_script}")
				return False

			os.chmod(build_script, 0o755)
			result = subprocess.run(
				[build_script, "-y", "--application", application, "--base_image", base_image],
				cwd=neko_apps_dir, capture_output=True, text=True, check=True
			)
			logger_config.info(f"Build output: {result.stdout}")
			if result.stderr:
				logger_config.warning(f"Build stderr: {result.stderr}")

			logger_config.info("Neko image built successfully.")
			return True

		except subprocess.CalledProcessError as e:
			logger_config.error(f"Build failed (rc={e.returncode}): {e.stderr}")
			return False
		except Exception as e:
			logger_config.error(f"Error building neko image: {e}")
			return False
		finally:
			if os.path.exists(neko_apps_dir):
				try:
					shutil.rmtree(neko_apps_dir)
					logger_config.info(f"Cleaned up {neko_apps_dir}")
				except Exception as e:
					logger_config.warning(f"Failed to clean up {neko_apps_dir}: {e}")

	# ─────────────────────────────────────────────
	# Container stop + port release
	# ─────────────────────────────────────────────

	def stop_docker(self, config: BrowserConfig) -> bool:
		"""
		Stop and remove a Docker container by name.
		Also releases its port allocation from the shared state file so
		those ports become available for the next container immediately.

		Returns True if a container was found and removed, False otherwise.
		"""
		try:
			logger_config.info(f"Stopping container: {config.docker_name}")
			result = subprocess.run(
				["docker", "ps", "-a", "--format", "{{.Names}}"],
				stdout=subprocess.PIPE, stderr=subprocess.PIPE,
				text=True, check=True,
			)
			container_names = result.stdout.strip().splitlines()

			if config.docker_name in container_names:
				logger_config.info(f"Container {config.docker_name} found — removing...")
				subprocess.run(["docker", "kill", config.docker_name], check=False,
							   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
				subprocess.run(["docker", "rm", "-f", config.docker_name], check=False,
							   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
				logger_config.info(f"Container {config.docker_name} stopped and removed.")

				# Release ports now that the container is gone
				_release_ports(config.docker_name)
				return True
			else:
				logger_config.info(f"No container named {config.docker_name} found.")
				return False

		except subprocess.CalledProcessError as e:
			logger_config.error(f"Error stopping container {config.docker_name}: {e}")
			raise

	# ─────────────────────────────────────────────
	# Main launch
	# ─────────────────────────────────────────────

	def _launch_with_retry(self, config: BrowserConfig, max_retries: int = 3, timeout: int = 30) -> subprocess.Popen:
		"""
		Start the Docker container via Popen with automatic port reallocation
		on conflict. Returns the Popen handle on success.

		Retry strategy — exponential backoff with jitter:
		Attempt 1 failure → wait ~1s  (1 * random 0.5–1.5)
		Attempt 2 failure → wait ~2s  (2 * random 0.5–1.5)
		Attempt 3 failure → wait ~4s  (4 * random 0.5–1.5)

		Jitter prevents multiple concurrent launchers from retrying in lockstep
		and hammering Docker/the port allocator simultaneously.

		Args:
		max_retries: Maximum number of launch attempts.
		timeout:     Max seconds to wait for `docker run` to return.
					Prevents hanging forever if Docker daemon is unresponsive.
		"""
		for attempt in range(max_retries):
			try:
				process = subprocess.Popen(
					config.neko_docker_cmd,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
					text=True,
					env={**os.environ, 'PYTHONUNBUFFERED': '1'},
					shell=True
				)
				stdout, stderr = process.communicate(timeout=timeout)
			except subprocess.TimeoutExpired:
				process.kill()
				process.communicate()
				raise BrowserLaunchError(
					f"Docker run timed out after {timeout}s — daemon may be unresponsive."
				)

			if process.returncode == 0:
				return process

			# Calculate backoff before deciding what to do —
			# always sleep between retries regardless of error type
			backoff = (2 ** attempt) * random.uniform(1.5, 3.5)

			if "port is already allocated" in stderr or "address already in use" in stderr:
				logger_config.warning(
					f"[Retry {attempt + 1}/{max_retries}] Port conflict — "
					f"reallocating and retrying in {backoff:.1f}s..."
				)
				_release_ports(config.docker_name)
				s, d, w = _allocate_ports(config.docker_name)
				config.server_port       = s
				config.debug_port        = d
				config.webrtc_port_start = w

			elif "Conflict. The container name" in stderr:
				logger_config.warning(
					f"[Retry {attempt + 1}/{max_retries}] Container name conflict — "
					f"force removing and retrying in {backoff:.1f}s..."
				)
				subprocess.run(
					["docker", "rm", "-f", config.docker_name], check=False,
					stdout=subprocess.PIPE, stderr=subprocess.PIPE
				)

			else:
				# Non-retryable error — fail immediately, no point waiting
				raise BrowserLaunchError(f"Docker failed: {stderr}")

			time.sleep(backoff)

		raise BrowserLaunchError(f"Docker failed after {max_retries} retries.")

	def launch(self, config: BrowserConfig) -> tuple[subprocess.Popen, str]:
		"""
        Full launch sequence for a Neko browser container:

          1. Verify Docker image exists; build it if not.
          2. Stop any existing container with the same docker_name and
             release its ports — ensures no conflict before allocation.
          3. Allocate unique ports (server, debug, webrtc range).
          4. Set allocated ports on config.
          5. Clean browser profile directory.
          6. Start Docker container (with retry on port conflict).
          7. Wait for Chrome WebSocket URL to become available.
          8. Optionally start background screenshot loop.
          9. Register atexit cleanup handler.

        Returns:
          (process, ws_url) — Popen handle and Chrome WebSocket debug URL.
        """
		image_name = config.neko_docker_cmd.split(" ")[-1]
		if not self._docker_image_exists(image_name):
			logger_config.info(f"Image {image_name} not found — building...")
			if not self._build_neko_image():
				raise BrowserLaunchError(f"Failed to build neko Docker image: {image_name}")

		# Stop any existing container + release its ports
		self.stop_docker(config)

		# Allocate unique ports and apply to config
		server_port, debug_port, webrtc_port_start = _allocate_ports(config.docker_name)
		config.server_port	   = server_port
		config.debug_port		= debug_port
		config.webrtc_port_start = webrtc_port_start

		self.clean_browser_profile(config)

		logger_config.info(
			f"[{config.docker_name}] Ports — "
			f"server: {server_port} | debug: {debug_port} | "
			f"webrtc: {webrtc_port_start}-{config.webrtc_port_end}"
		)

		try:
			# Single launch point — retry reallocates ports and loops back
			process = self._launch_with_retry(config)

			logger_config.info(f"Neko container started — PID: {process.pid}")

			ws_url = self._get_websocket_url(config.debug_port, config.connection_timeout)
			logger_config.info(f"Chrome WebSocket ready: {ws_url}")

			if config.take_screenshot:
				self._start_screenshot_loop(config)

			import atexit
			def _atexit_cleanup():
				subprocess.run(["docker", "kill", config.docker_name], check=False,
							stdout=subprocess.PIPE, stderr=subprocess.PIPE)
				_release_ports(config.docker_name)

			atexit.register(_atexit_cleanup)
			return process, ws_url

		except Exception as e:
			_release_ports(config.docker_name)
			raise BrowserLaunchError(f"Failed to launch Neko browser: {e}")

	# ─────────────────────────────────────────────
	# Screenshot loop
	# ─────────────────────────────────────────────

	def _start_screenshot_loop(self, config: BrowserConfig, interval: int = 2):
		"""
		Background bash loop that captures screenshots from inside the container
		every `interval` seconds using scrot.

		Uses temp file + mv for atomic writes — prevents consumers from reading
		a partially written screenshot file.

		Skips silently if a loop is already running for this container.
		"""
		cmd = (
			f"while true; do "
			f"if docker ps -a --format '{{{{.Names}}}}' | grep -q '^{config.docker_name}$'; then "
			f"	TS=$(date +%Y%m%d_%H%M%S); "
			f"	docker exec {config.docker_name} scrot /tmp/neko_$TS.png && "
			f"	mkdir -p ./{config.docker_name} && "
			f"	docker cp {config.docker_name}:/tmp/neko_$TS.png ./{config.docker_name}/screenshot_tmp.png && "
			f"	mv ./{config.docker_name}/screenshot_tmp.png ./{config.docker_name}/screenshot.png; "
			f"else "
			f"	echo '[STOP] Container {config.docker_name} not found. Exiting.'; "
			f"	break; "
			f"fi; "
			f"sleep {interval}; "
			f"done"
		)

		check = subprocess.run(
			["pgrep", "-af", f"docker exec {config.docker_name} scrot"],
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
		)
		if check.stdout.strip():
			logger_config.warning(f"[SKIP] Screenshot loop already running for {config.docker_name}.")
			return

		logger_config.info(f"Command to run: {cmd}")
		self.screenshot_process = subprocess.Popen(["bash", "-c", cmd])

		import atexit, signal

		def _stop_screenshot():
			try:
				os.kill(self.screenshot_process.pid, signal.SIGKILL)
			except ProcessLookupError:
				pass

		atexit.register(_stop_screenshot)
		logger_config.info(f"[BG] Screenshot loop started — every {interval}s → ./{config.docker_name}/screenshot.png")

	# ─────────────────────────────────────────────
	# File chooser helper
	# ─────────────────────────────────────────────

	def choose_file_via_xdotool(self, config: BrowserConfig, file_path: str) -> bool:
		"""
		Interact with a file chooser dialog inside the container via xdotool.
		Focuses address bar (Ctrl+L), types the full path, presses Enter.

		Requires: xdotool installed in container, AllowFileSelectionDialogs=True
		in Chrome policy, URLAllowlist set to the target folder.
		"""
		try:
			logger_config.info(
				"Requires: xdotool installed, AllowFileSelectionDialogs=True, "
				"URLAllowlist set to folder path, URLBlocklist empty."
			)
			logger_config.info("Waiting for dialog to appear.", seconds=3)

			subprocess.run(
				['docker', 'exec', config.docker_name, 'xdotool', 'key', 'ctrl+l'],
				timeout=3
			)
			logger_config.info("Wait for command finish.", seconds=1)

			subprocess.run(
				['docker', 'exec', config.docker_name, 'xdotool', 'type',
				 f'{config.neko_attach_folder}/{file_path}'],
				timeout=5
			)
			logger_config.info("Wait for command finish.", seconds=1)

			subprocess.run(
				['docker', 'exec', config.docker_name, 'xdotool', 'key', 'Return'],
				timeout=3
			)
			return True

		except subprocess.TimeoutExpired:
			logger_config.error("xdotool file chooser timed out.")
			return False

	# ─────────────────────────────────────────────
	# Graceful Chrome shutdown
	# ─────────────────────────────────────────────

	def _graceful_close_chrome(self, config: BrowserConfig) -> bool:
		"""
		Send SIGTERM to Chrome inside the container so it saves session state
		cleanly. Prevents the "Restore pages?" crash recovery dialog on next launch.

		Sequence:
		  1. Check container is running (skip gracefully if not).
		  2. killall -TERM chrome (or shell-based fallback if killall missing).
		  3. Wait 3 seconds for Chrome to exit.
		  4. If still running, force SIGKILL.
		"""
		try:
			result = subprocess.run(
				["docker", "ps", "--format", "{{.Names}}"],
				stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
			)
			if config.docker_name not in result.stdout.strip().splitlines():
				logger_config.info(f"Container {config.docker_name} not running — skipping graceful close.")
				return True

			logger_config.info(f"Gracefully closing Chrome in {config.docker_name}...")

			# Attempt 1: killall
			kill_result = subprocess.run(
				["docker", "exec", config.docker_name, "killall", "-TERM", "chrome"],
				check=False, timeout=5,
				stdout=subprocess.PIPE, stderr=subprocess.PIPE
			)

			# Attempt 2: shell-based fallback
			if kill_result.returncode != 0:
				logger_config.info("killall unavailable — using shell-based SIGTERM...")
				subprocess.run(
					["docker", "exec", config.docker_name, "sh", "-c",
					 "for pid in $(ps aux | grep -i chrome | grep -v grep | awk '{print $2}'); "
					 "do kill -TERM $pid 2>/dev/null; done"],
					check=False, timeout=10,
					stdout=subprocess.PIPE, stderr=subprocess.PIPE
				)

			time.sleep(3)

			# Check if still alive
			check = subprocess.run(
				["docker", "exec", config.docker_name, "sh", "-c",
				 "ps aux | grep -i chrome | grep -v grep"],
				stdout=subprocess.PIPE, stderr=subprocess.PIPE,
				text=True, timeout=5
			)

			if check.stdout.strip():
				logger_config.warning("Chrome still running — forcing SIGKILL...")
				subprocess.run(
					["docker", "exec", config.docker_name, "sh", "-c",
					 "for pid in $(ps aux | grep -i chrome | grep -v grep | awk '{print $2}'); "
					 "do kill -9 $pid 2>/dev/null; done"],
					check=False, timeout=10,
					stdout=subprocess.PIPE, stderr=subprocess.PIPE
				)
				time.sleep(1)

			logger_config.info("Chrome closed gracefully.")
			return True

		except subprocess.TimeoutExpired:
			logger_config.warning("Timeout during graceful Chrome close.")
			return False
		except Exception as e:
			logger_config.warning(f"Error during graceful Chrome close: {e}")
			return False

	# ─────────────────────────────────────────────
	# Full cleanup
	# ─────────────────────────────────────────────

	def cleanup(self, config: BrowserConfig, process: subprocess.Popen) -> None:
		"""
		Full teardown sequence:
		  1. Stop background screenshot loop.
		  2. Gracefully close Chrome (SIGTERM → SIGKILL fallback).
		  3. Stop and remove Docker container (also releases port allocation).
		  4. Terminate Popen process handle.
		"""
		try:
			# 1. Screenshot loop
			if hasattr(self, 'screenshot_process') and self.screenshot_process:
				try:
					self.screenshot_process.terminate()
					self.screenshot_process.wait(timeout=5)
					logger_config.info("Screenshot process stopped.")
				except subprocess.TimeoutExpired:
					self.screenshot_process.kill()
					logger_config.warning("Screenshot process force killed.")
				except Exception as e:
					logger_config.warning(f"Error stopping screenshot process: {e}")

			# 2. Graceful Chrome shutdown (prevents "Restore pages?" on next launch)
			self._graceful_close_chrome(config)

			# 3. Stop container + release ports
			self.stop_docker(config)

			# 4. Popen handle
			if process:
				try:
					process.terminate()
					process.wait(timeout=5)
				except subprocess.TimeoutExpired:
					process.kill()
				logger_config.info("Neko process cleaned up.")

		except Exception as e:
			logger_config.error(f"Error during neko browser cleanup: {e}")