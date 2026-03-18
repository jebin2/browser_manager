"""
================================================================================
browser_config.py
================================================================================

OVERVIEW
--------
Pure data configuration class for browser launch settings. This module has
NO knowledge of Docker, ports, or container lifecycle. It is a plain dataclass
that holds settings and builds command strings from them.

All Docker interaction, port allocation, and container management is owned
entirely by NekoBrowserLauncher (neko_browser_launcher.py).

WHAT IT DOES
------------
  - Holds all configuration fields for a browser session (URL, flags, paths,
    ports, docker name, etc.)
  - Supports multiple browser types (Chrome, Brave) via BrowserType enum
  - Builds the `docker run` command string via neko_docker_cmd property
  - Provides browser-aware properties for policy paths, profile mounts, etc.

WHAT IT DOES NOT DO
-------------------
  - No port allocation
  - No Docker calls (docker ps, docker kill, etc.)
  - No file locks or state files
  - No container lifecycle management

SEPARATION OF CONCERNS
-----------------------
  browser_config.py       → what to run (data / command string)
  neko_browser_launcher.py → how to run it (Docker, ports, lifecycle)

  This separation means BrowserConfig can be used or tested independently
  of Docker, and NekoBrowserLauncher can evolve its port strategy without
  touching the config class.

USE CASES
---------
  - use_neko=True  (default): Used by NekoBrowserLauncher which handles
                              all port allocation before passing config in.
  - use_neko=False:           Used for local/non-Docker browser launches.
                              Port fields are irrelevant and ignored.

BROWSER TYPES
-------------
  BrowserType.CHROME → Chrome/Chromium with remote debugging
  BrowserType.BRAVE  → Brave browser with built-in ad blocking + remote debugging

ENVIRONMENT VARIABLES
---------------------
  NEKO_DIR            Path to neko repo         (default: ~/git/neko-remote-debugging)
  NEKO_ATTACH_FOLDER  Neko downloads folder     (default: /home/neko/Downloads)
  BROWSER_EXECUTABLE  Custom browser binary     (default: None)

USAGE
-----
  # Via NekoBrowserLauncher (recommended for Neko/Docker usage):
  #   NekoBrowserLauncher handles create() + port allocation internally.
  #   You never need to call BrowserConfig.create() directly for Neko.

  # For non-Docker usage:
  config = BrowserConfig(docker_name="local", use_neko=False)

  # For Brave with ad blocking (default):
  config = BrowserConfig(docker_name="test", browser_type=BrowserType.BRAVE)

  # For Chrome:
  config = BrowserConfig(docker_name="test", browser_type=BrowserType.CHROME)

  # Inheritors — policy mount (works for both browsers):
  policy_path = os.path.join(os.getcwd(), 'policies.json')
  additional_flags.append(config.policy_volume_mount(policy_path))

================================================================================
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum
import os
import requests
from custom_logger import logger_config

_WEBRTC_RANGE_SIZE = 101


class BrowserType(Enum):
    """Supported browser types for Neko containers."""
    CHROME = "chrome"
    BRAVE = "brave"


# Browser-specific configuration lookup table
_BROWSER_CONFIG = {
    BrowserType.CHROME: {
        "docker_image": "ghcr.io/m1k1o/neko-apps/chrome-remote-debug:latest",
        "profile_mount_path": "/home/neko/chrome-profile",
        "policy_container_path": "/etc/opt/chrome/policies/managed/policies.json",
        "process_name": "chrome",
        "flags_env_var": "NEKO_CHROME_FLAGS",
        "neko_application": "chrome-remote-debug",
    },
    BrowserType.BRAVE: {
        "docker_image": "ghcr.io/m1k1o/neko-apps/brave-remote-debug:latest",
        "profile_mount_path": "/home/neko/.config/brave",
        "policy_container_path": "/etc/brave/policies/managed/policies.json",
        "process_name": "brave",
        "flags_env_var": "NEKO_BRAVE_FLAGS",
        "neko_application": "brave-remote-debug",
    },
}


@dataclass
class BrowserConfig:
    """
    Pure data configuration for a browser session.

    For Neko/Docker usage, do not instantiate this directly — let
    NekoBrowserLauncher.launch() create and manage it, which ensures
    ports are correctly allocated before the container starts.

    For non-Docker usage (use_neko=False), instantiate directly.
    Port fields (server_port, debug_port, webrtc_port_start) are
    ignored when use_neko=False.
    """

    # ── Browser type ─────────────────────────────────────────────────────────
    browser_type: BrowserType = BrowserType.BRAVE

    # ── Browser / session settings ────────────────────────────────────────────
    url: str = "https://jebin2-paper.hf.space/"
    user_data_dir: Optional[str] = None
    delete_user_data_dir_singleton_lock: bool = True
    browser_executable: Optional[str] = os.getenv("BROWSER_EXECUTABLE")
    is_remote_debugging: bool = True
    debugging_port: int = 9222
    headless: bool = False
    close_other_tabs: bool = True
    minimize_window_focus: bool = False
    connection_timeout: int = 30
    extra_args: List[str] = field(default_factory=list)

    # ── Neko / Docker settings ────────────────────────────────────────────────
    use_neko: bool = True
    take_screenshot: bool = True
    neko_dir: str = os.getenv("NEKO_DIR", os.path.expanduser("~/git/neko-remote-debugging"))
    neko_attach_folder: str = os.getenv("NEKO_ATTACH_FOLDER", "/home/neko/Downloads")
    docker_name: str = "temp"
    host_network: bool = False
    additionl_docker_flag: str = ""
    disable_extensions: Optional[bool] = None
    use_default_policy: bool = True

    browser_flags: str = (
        "--disable-gpu "
        "--no-sandbox --no-zygote "
        "--window-size=1920,1080 --no-first-run "
        "--disable-session-crashed-bubble --disable-infobars "
        "--disable-dev-shm-usage"
    )

    # Port map template — placeholders are substituted in neko_docker_cmd.
    # server_port, debug_port, webrtc_start, webrtc_end are replaced at
    # command-build time with the actual allocated values.
    port_map_template: List[str] = field(default_factory=lambda: [
        "-p server_port:8080",
        "-p debug_port:9223",
        "-p webrtc_start-webrtc_end:webrtc_start-webrtc_end/udp"
    ])

    # ── Allocated port values ─────────────────────────────────────────────────
    # These are set by NekoBrowserLauncher after port allocation.
    # Default values are placeholders — never used directly without allocation.
    server_port: int = 8080
    debug_port: int = 9223
    webrtc_port_start: int = 52000
    webrtc_port_range_size: int = _WEBRTC_RANGE_SIZE

    def __post_init__(self):
        """Set defaults that depend on other fields."""
        if self.disable_extensions is None:
            # Brave needs extensions enabled for Shields ad blocking to work fully
            self.disable_extensions = self.browser_type != BrowserType.BRAVE

    # ── Browser-aware properties ──────────────────────────────────────────────

    @property
    def _browser_cfg(self) -> dict:
        """Internal lookup for current browser type's config."""
        return _BROWSER_CONFIG[self.browser_type]

    @property
    def docker_image(self) -> str:
        """Docker image name for the selected browser."""
        return self._browser_cfg["docker_image"]

    @property
    def profile_mount_path(self) -> str:
        """Container-side path where the browser profile is mounted."""
        return self._browser_cfg["profile_mount_path"]

    @property
    def policy_container_path(self) -> str:
        """Container-side path for the browser policy JSON file."""
        return self._browser_cfg["policy_container_path"]

    @property
    def browser_process_name(self) -> str:
        """Process name used for killall/grep inside the container."""
        return self._browser_cfg["process_name"]

    @property
    def flags_env_var(self) -> str:
        """Environment variable name for passing extra browser flags."""
        return self._browser_cfg["flags_env_var"]

    @property
    def neko_application(self) -> str:
        """Neko application name for the build script."""
        return self._browser_cfg["neko_application"]

    # ── Backward compatibility ────────────────────────────────────────────────

    @property
    def chrome_flags(self) -> str:
        """Deprecated alias for browser_flags. Use browser_flags instead."""
        return self.browser_flags

    @chrome_flags.setter
    def chrome_flags(self, value: str):
        self.browser_flags = value

    @property
    def effective_browser_flags(self) -> str:
        """Browser flags with conditional --disable-extensions based on disable_extensions field."""
        flags = self.browser_flags
        if self.disable_extensions:
            flags += " --disable-extensions"
        return flags

    # ── Helper methods ────────────────────────────────────────────────────────

    def download_policies(self) -> Optional[str]:
        target_path = f"/tmp/{self.browser_type.value}_policies.json"
        if os.path.exists(target_path):
            print(f"Policy file already exists at {target_path}, skipping download.")
            return target_path

        url = f"https://raw.githubusercontent.com/jebin2/neko-apps/6678e11e0409fc2077ccc8a40510dbe0ee53fe07/{self.browser_type.value}-remote-debug/policies.json"

        try:
            response = requests.get(url)
            if response.status_code == 200:
                with open(target_path, 'wb') as f:
                    f.write(response.content)
                logger_config.success(f"Downloaded policies.json to {target_path}")
                return target_path
            else:
                logger_config.error(f"Failed to download policies.json. Status code: {response.status_code}")
                return None
        except Exception as e:
            logger_config.error(f"Error downloading policies.json: {e}")
            return None

    def policy_volume_mount(self, host_policy_path: str = None) -> str:
        """
        Return the Docker volume mount string for a policy file.

        Usage by inheritors:
            policy_path = os.path.join(os.getcwd(), 'policies.json')
            additional_flags.append(config.policy_volume_mount(policy_path))
        """
        if not host_policy_path:
            host_policy_path = self.download_policies()

        return f'-v {host_policy_path}:{self.policy_container_path}'

    # ─────────────────────────────────────────────────────────────────────────

    @property
    def webrtc_port_end(self) -> int:
        """Inclusive end of the WebRTC UDP port range."""
        return self.webrtc_port_start + self.webrtc_port_range_size - 1

    @property
    def neko_docker_cmd(self) -> str:
        """
        Build the full `docker run` command string for this container.

        Substitutes port placeholders in port_map_template with the
        actual allocated port values set by NekoBrowserLauncher.
        Only meaningful when use_neko=True.
        """
        port_map_resolved = " ".join([
            p.replace("server_port",  str(self.server_port))
             .replace("debug_port",   str(self.debug_port))
             .replace("webrtc_start", str(self.webrtc_port_start))
             .replace("webrtc_end",   str(self.webrtc_port_end))
            for p in self.port_map_template
        ])
        has_custom_policy = self.policy_container_path in self.additionl_docker_flag
        policy_mount = self.policy_volume_mount() if self.use_default_policy and not has_custom_policy else ''
        return (
            f'docker run -d --name {self.docker_name} --rm '
            f'{"--network=host" if self.host_network else ""} '
            f'{port_map_resolved} '
            '--cap-add=SYS_ADMIN '
            f'-v {self.user_data_dir or "/tmp/neko-profile"}:{self.profile_mount_path} '
            f'{policy_mount} '
            f'{self.additionl_docker_flag} '
            f'-e NEKO_WEBRTC_EPR={self.webrtc_port_start}-{self.webrtc_port_end} '
            '-e NEKO_WEBRTC_NAT1TO1=127.0.0.1 '
            f'-e {self.flags_env_var}="{self.effective_browser_flags}" '
            '-e NEKO_DISABLE_AUDIO=1 '
            f'{self.docker_image}'
        )