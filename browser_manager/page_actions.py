import json
import time
from typing import Callable, Optional
from custom_logger import logger_config
from playwright.sync_api import Page

def find_and_highlight_element(page: Page, text_excerpt: str, color: str = '#00B4D8') -> bool:
    """
    Finds a DOM element containing the given excerpt and highlights it with a background color.
    Uses accurate JS DOM tree walking to locate the text node.
    """
    # JS function to find text node, wrap matching text in <mark>, and style it.
    js_code = f"""
    (function() {{
        const excerpt = {json.dumps(text_excerpt)};
        const fullText = excerpt.trim();
        const fallbackText = excerpt.substring(0, 50).trim();
        if (!fallbackText) return false;

        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        let foundNode = null;
        let matchText = fullText;

        while (node = walker.nextNode()) {{
            if (node.textContent.includes(fullText)) {{
                foundNode = node;
                matchText = fullText;
                break;
            }} else if (node.textContent.includes(fallbackText) && !foundNode) {{
                // Save it as a fallback in case the full text spans across tags
                foundNode = node;
                matchText = fallbackText;
            }}
        }}

        if (foundNode) {{
            const index = foundNode.nodeValue.indexOf(matchText);
            if(index >= 0) {{
                const before = document.createTextNode(foundNode.nodeValue.substring(0, index));
                const after = document.createTextNode(foundNode.nodeValue.substring(index + matchText.length));
                
                const mark = document.createElement('mark');
                mark.textContent = matchText;
                mark.style.backgroundColor = '{color}';
                mark.style.color = '#ffffff';
                mark.style.borderRadius = '4px';
                mark.style.padding = '2px 4px';
                mark.style.boxShadow = '0 2px 4px rgba(0,0,0,0.1)';
                mark.setAttribute('data-atv-highlighted', 'true');
                
                const parentNode = foundNode.parentNode;
                parentNode.insertBefore(before, foundNode);
                parentNode.insertBefore(mark, foundNode);
                parentNode.insertBefore(after, foundNode);
                parentNode.removeChild(foundNode);
                
                return true;
            }}
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
    Removes the <mark> tags we inserted by unwrapping the text back into the parent node.
    """
    js_code = """
    (function() {
        document.querySelectorAll('mark[data-atv-highlighted="true"]').forEach(mark => {
            const parent = mark.parentNode;
            // Create a text node with the mark's contents
            const textNode = document.createTextNode(mark.textContent);
            parent.replaceChild(textNode, mark);
            // Optional: parent.normalize() to merge adjacent text nodes
            parent.normalize();
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
    new Promise((resolve) => {{
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
            const startY = window.scrollY;
            const targetY = startY + rect.top + {offset_y};
            const distance = targetY - startY;
            const durationMs = 800;
            const startTime = performance.now();
            
            function easeInOutQuad(t, b, c, d) {{
                t /= d/2;
                if (t < 1) return c/2*t*t + b;
                t--;
                return -c/2 * (t*(t-2) - 1) + b;
            }}

            function scrollStep(timestamp) {{
                const elapsed = Math.max(0, timestamp - startTime);
                if (elapsed < durationMs) {{
                    const nextY = easeInOutQuad(elapsed, startY, distance, durationMs);
                    window.scrollTo(0, nextY);
                    window.requestAnimationFrame(scrollStep);
                }} else {{
                    window.scrollTo(0, targetY);
                    resolve(true);
                }}
            }}
            window.requestAnimationFrame(scrollStep);
        }} else {{
            resolve(false);
        }}
    }});
    """
    success = page.evaluate(js_code)
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
    viewport_width: int = 375,
    viewport_height: int = 667,
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
        start_time = time.perf_counter()
        screenshot_path = f"{output_dir}/frame_{current_counter:06d}.jpg"
        
        # Capture as high-speed jpeg
        page.screenshot(path=screenshot_path, type="jpeg", quality=80)

        if current_counter % 10 == 0:
            logger_config.debug(f"Captured {current_counter - start_frame_counter + 1}/{total_frames} frames for current segment...", overwrite=True)

        # Apply any overlays or watermarks
        if frame_callback:
            frame_callback(screenshot_path)

        current_counter += 1
        
        # Attempt to keep timing roughly consistent
        elapsed = time.perf_counter() - start_time
        sleep_time = max(0, (1.0 / fps) - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

    return current_counter
