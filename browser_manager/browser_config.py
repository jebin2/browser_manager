from dataclasses import dataclass, field
from typing import Optional, List
import os

@dataclass
class BrowserConfig:
    """Configuration for browser launch and management."""
    url: str = "https://jebin2-paper.hf.space/"
    user_data_dir: Optional[str] = None
    delete_user_data_dir_singleton_lock: bool = True
    browser_executable: Optional[str] = os.getenv("BROWSER_EXECUTABLE")
    debugging_port: int = 9222
    headless: bool = False
    use_neko: bool = True
    neko_dir: str = os.getenv("NEKO_DIR", os.path.expanduser("~/git/neko-remote-debugging"))

    chrome_flags: str = (
        "--no-sandbox --no-zygote --disable-extensions "
        "--window-size=1920,1080 --no-first-run "
        "--disable-session-crashed-bubble --disable-infobars "
        "--disable-dev-shm-usage"
    )

    port_map_template: List[str] = field(default_factory=lambda: [
        "-p server_port:8080",
        "-p debug_port:9223"
    ])

    host_network: bool = False

    starting_server_port_to_check: int = 8081
    starting_debug_port_to_check: int = 9224
    server_port: int = 8080
    debug_port: int = 9223
    docker_name: str = "temp"
    close_other_tabs: bool = True
    minimize_window_focus: bool = False
    connection_timeout: int = 30
    extra_args: List[str] = field(default_factory=list)

    @property
    def neko_docker_cmd(self) -> str:
        port_map_resolved = " ".join([
            p.replace("server_port", str(self.server_port)).replace("debug_port", str(self.debug_port))
            for p in self.port_map_template
        ])

        return (
            f'docker run -d --name {self.docker_name} --rm '
            f'{"--network=host" if self.host_network else ""} '
            f'{port_map_resolved} '
            '--cap-add=SYS_ADMIN '
            f'-v {self.user_data_dir or "/tmp/neko-profile"}:/home/neko/chrome-profile '
            f'-e NEKO_CHROME_FLAGS="{self.chrome_flags}" '
            '-e NEKO_DISABLE_AUDIO=1 '
            'ghcr.io/m1k1o/neko-apps/chrome-remote-debug:latest'
        )
