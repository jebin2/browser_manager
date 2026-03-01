import json
import time
from typing import Callable, Optional
from custom_logger import logger_config
from playwright.sync_api import Page

def find_and_highlight_element(page: Page, text_excerpt: str, color: str = '#FFE066') -> bool:
    """
    Finds a DOM element containing the given excerpt and highlights it with a background color.
    Uses accurate JS DOM tree walking to locate the text node.
    """
    # JS function to find element, highlight and smooth scroll to it
    js_code = f"""
    (function() {{
        const excerpt = {json.dumps(text_excerpt)};
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        let targetEl = null;

        while (node = walker.nextNode()) {{
            if (node.textContent.includes(excerpt.substring(0, 50))) {{
                targetEl = node.parentElement;
                break;
            }}
        }}

        if (targetEl) {{
            // Highlight
            targetEl.style.backgroundColor = '{color}';
            targetEl.style.transition = 'background-color 0.3s ease';
            targetEl.style.borderRadius = '3px';
            targetEl.setAttribute('data-atv-highlighted', 'true');
            targetEl._atv_highlighted = true;
            return true;
        }}
        return false;
    }})();
    """
    success = page.evaluate(js_code)
    if not success:
        logger_config.warning(f"Could not locate text for highlighting: '{text_excerpt[:50]}...'")
    return success

def remove_highlights(page: Page):
    """
    Removes the background color specifically from any elements we highlighted previously.
    """
    js_code = """
    (function() {
        document.querySelectorAll('[data-atv-highlighted]').forEach(el => {
            el.style.backgroundColor = '';
            el.style.borderRadius = '';
            el.removeAttribute('data-atv-highlighted');
        });
    })();
    """
    page.evaluate(js_code)


def scroll_to_element(page: Page, text_excerpt: str, offset_y: int = -100) -> bool:
    """
    Find the element by excerpt and smoothly scroll to it.
    offset_y: Add some padding to the top (negative means scroll higher).
    """
    js_code = f"""
    (function() {{
        const excerpt = {json.dumps(text_excerpt)};
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        let targetEl = null;

        while (node = walker.nextNode()) {{
            if (node.textContent.includes(excerpt.substring(0, 50))) {{
                targetEl = node.parentElement;
                break;
            }}
        }}

        if (targetEl) {{
            const rect = targetEl.getBoundingClientRect();
            const targetY = window.scrollY + rect.top + {offset_y};
            window.scrollTo({{top: targetY, behavior: 'smooth'}});
            return true;
        }}
        return false;
    }})();
    """
    success = page.evaluate(js_code)
    if success:
        # Give smooth scrolling time to settle
        time.sleep(0.4)
    return success

def scroll_continuous(page: Page, pixels_per_second: float, duration_seconds: float):
    """
    Kicks off an async JS continuous scroll interpolation over time.
    """
    js_code = f"""
    (function() {{
        const startY = window.scrollY;
        const totalPixels = {pixels_per_second * duration_seconds};
        const endY = startY + totalPixels;
        const durationMs = {duration_seconds * 1000};
        const startTime = performance.now();

        function scrollStep(timestamp) {{
            const elapsed = timestamp - startTime;
            if (elapsed < durationMs) {{
                const progress = elapsed / durationMs;
                window.scrollTo(0, startY + (totalPixels * progress));
                window.requestAnimationFrame(scrollStep);
            }} else {{
                window.scrollTo(0, endY);
            }}
        }}
        window.requestAnimationFrame(scrollStep);
    }})();
    """
    page.evaluate(js_code)


def capture_viewport_frames(
    page: Page, 
    duration_sec: float, 
    fps: int, 
    output_dir: str, 
    start_frame_counter: int = 0,
    viewport_width: int = 390,
    viewport_height: int = 844,
    frame_callback: Optional[Callable[[str], None]] = None
) -> int:
    """
    Captures screenshots frame by frame at the requested framerate and duration.
    Optionally calls a frame_callback(filename) to process/overlay items on the frame.
    Returns the new current frame counter.
    """
    total_frames = int(duration_sec * fps)
    current_counter = start_frame_counter

    for _ in range(total_frames):
        screenshot_path = f"{output_dir}/frame_{current_counter:06d}.png"
        
        # We capture specifically the viewport area. By default screenshot captures what is visible
        # but to ensure exact dimensions, we map the clip.
        clip_y = page.evaluate("window.scrollY")
        page.screenshot(path=screenshot_path, clip={
            "x": 0, "y": clip_y,
            "width": viewport_width,
            "height": viewport_height
        })

        # Apply any overlays or watermarks
        if frame_callback:
            frame_callback(screenshot_path)

        current_counter += 1
        
        # Attempt to keep timing roughly consistent
        time.sleep(1.0 / fps)

    return current_counter
