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
  - Builds the `docker run` command string via neko_docker_cmd property
  - Provides a plain create() factory for constructing config objects

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

  # For direct/manual usage (ports must be set manually):
  config = BrowserConfig(docker_name="test", server_port=8081, debug_port=9224)

================================================================================
"""

from dataclasses import dataclass, field
from typing import Optional, List
import os

_WEBRTC_RANGE_SIZE = 101


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

    chrome_flags: str = (
        "--disable-gpu "
        "--no-sandbox --no-zygote --disable-extensions "
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

    # ── Port search starting points (used by NekoBrowserLauncher allocator) ───
    starting_server_port_to_check: int = 8081
    starting_debug_port_to_check: int = 9224
    starting_webrtc_port_to_check: int = 52000

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
        return (
            f'docker run -d --name {self.docker_name} --rm '
            f'{"--network=host" if self.host_network else ""} '
            f'{port_map_resolved} '
            '--cap-add=SYS_ADMIN '
            f'-v {self.user_data_dir or "/tmp/neko-profile"}:/home/neko/chrome-profile '
            f'{self.additionl_docker_flag} '
            f'-e NEKO_WEBRTC_EPR={self.webrtc_port_start}-{self.webrtc_port_end} '
            '-e NEKO_WEBRTC_NAT1TO1=127.0.0.1 '
            f'-e NEKO_CHROME_FLAGS="{self.chrome_flags}" '
            '-e NEKO_DISABLE_AUDIO=1 '
            'ghcr.io/m1k1o/neko-apps/chrome-remote-debug:latest'
        )