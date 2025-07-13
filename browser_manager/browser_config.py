from dataclasses import dataclass, field
from typing import Optional, List
import os

@dataclass
class BrowserConfig:
    """Configuration for browser launch and management."""
    url: str = "https://jebin2-paper.hf.space/"
    user_data_dir: Optional[str] = None
    delete_user_data_dir_singleton_lock: bool = True
    browser_executable: str = os.getenv("BROWSER_EXECUTABLE", None)
    debugging_port: int = 9222
    headless: bool = False
    use_neko: bool = True
    neko_dir: str = os.getenv("NEKO_DIR", os.path.expanduser("~/git/neko-remote-debugging"))
    chrome_flags: str = "--no-sandbox --no-zygote --disable-extensions --window-size=1920,1080 --no-first-run --disable-session-crashed-bubble --disable-infobars"
    neko_docker_cmd: str = (
        'docker run -d --name docker_name --rm '
        '--network=host '
        '-p server_port:8080 -p debug_port:9223 '
        '--cap-add=SYS_ADMIN '
        '-v user_data_dir:/home/neko/chrome-profile '
        f'-e NEKO_CHROME_FLAGS={chrome_flags} '
        '-e NEKO_DISABLE_AUDIO=1 '
        'ghcr.io/m1k1o/neko-apps/chrome-remote-debug:latest'
    )
    docker_name: str = "temp"
    close_other_tabs: bool = True
    minimize_window_focus: bool = False
    connection_timeout: int = 30
    extra_args: List[str] = field(default_factory=list)