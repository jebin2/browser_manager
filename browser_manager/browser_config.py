from dataclasses import dataclass, field
from typing import Optional, List
import os

@dataclass
class BrowserConfig:
    """Configuration for browser launch and management."""
    url: str = "https://jebin2-paper.hf.space/"
    user_data_dir: Optional[str] = None
    browser_executable: str = os.getenv("BROWSER_EXECUTABLE", "/usr/bin/brave-browser")
    debugging_port: int = 9222
    headless: bool = False
    use_neko: bool = True
    neko_dir: str = os.getenv("NEKO_DIR", os.path.expanduser("~/git/neko-remote-debugging"))
    close_other_tabs: bool = True
    minimize_window_focus: bool = False
    connection_timeout: int = 30
    extra_args: List[str] = field(default_factory=list)