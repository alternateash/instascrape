# -*- coding: utf-8 -*-
"""
Instagram Saved Collection Scraper (Cookie Authentication Version)
================================================================

This script allows you to download all media (photos, videos, and carousel
	items) from one of your saved collections on Instagram using your browser's
	session cookie to bypass the need for username/password login. This version
	parses the page source for embedded JSON data, which is a reliable method.

Prerequisites:
--------------
1. Python 3.7+
2. Google Chrome browser installed.
3. Required Python libraries. Install them using pip:
   pip install selenium requests webdriver-manager "blinker==1.7.0"

How to Run:
-----------
1. Follow the instructions in 'cookie_instructions.md' to get your
	'sessionid' cookie.
2. Save this script as a Python file (e.g., instagram_scraper.py).
3. Open a terminal or command prompt.
4. Navigate to the directory where you saved the file.
5. Run the script with the command: python instagram_scraper.py
6. Follow the on-screen prompts.
"""

import os
import re
import sys
import time
import json
import random
import threading
import requests
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

try:
    import termios
    import tty
    import select
except Exception:  # Non-TTY environments / unsupported platforms
    termios = None
    tty = None
    select = None

# Use standard selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# Automatically manage the Chrome driver
from webdriver_manager.chrome import ChromeDriverManager

# --- Configuration ---
# Use random delays to better mimic human behavior.
def short_delay(): time.sleep(random.uniform(2, 4))
def medium_delay(): time.sleep(random.uniform(5, 8))


def get_user_input():
    """Prompts the user for necessary information to run the scraper."""
    print("--- Instagram Collection Scraper (Cookie Version) ---")
    print("Please provide the following details:")

    def is_exit(value: str) -> bool:
        # Allow typing ESC (or pasting the literal escape char) then Enter.
        return value.strip().upper() == "ESC" or "\x1b" in value

    while True:
        session_id = input("1. Your Instagram 'sessionid' cookie (or type ESC to exit): ").strip()
        if is_exit(session_id):
            return None
        if session_id:
            break
        print("Session ID cannot be empty.")

    while True:
        collection_url = input("2. The full URL of your Saved Collection (or type ESC to exit): ").strip()
        if is_exit(collection_url):
            return None
        if not collection_url:
            print("Collection URL cannot be empty.")
            continue
        parsed = urlparse(collection_url)
        if parsed.scheme not in ("http", "https"):
            print("Please enter a valid URL starting with http:// or https://")
            continue
        break
    # Add a default value if the user just presses Enter.
    save_dir_input = input(r"3. Path to the folder to save media [Default: ~/Downloads/InstaScraped]: ") or '~/Downloads/InstaScraped'
    save_dir = os.path.expanduser(save_dir_input) # Expands ~ to the user's home directory
    
    while True:
        start_post_str = input("4. Start from post number (optional, press Enter for beginning): ")
        if not start_post_str or start_post_str.isdigit():
            start_post = int(start_post_str) if start_post_str else 1
            break
        print("Invalid input. Please enter a number or leave blank.")

    while True:
        end_post_str = input("5. End at post number (optional, press Enter for end): ")
        if not end_post_str or end_post_str.isdigit():
            end_post = int(end_post_str) if end_post_str else float('inf')
            break
        print("Invalid input. Please enter a number or leave blank.")

    if not os.path.isdir(save_dir):
        try:
            os.makedirs(save_dir)
            print(f"Created directory: {save_dir}")
        except OSError as e:
            print(f"Error: Could not create directory {save_dir}. {e}")
            return None
            
    return session_id, collection_url, save_dir, start_post, end_post


class _EscapeStopper:
    """Listens for the ESC key in the terminal and flips an Event; best-effort only."""

    def __init__(self):
        self.stop_event = threading.Event()
        self._thread = None
        self._enabled = bool(termios and tty and select)
        self._old_term_attrs = None

    def start(self):
        if not self._enabled:
            return
        if not sys.stdin.isatty():
            return
        if self._thread and self._thread.is_alive():
            return

        try:
            fd = sys.stdin.fileno()
            self._old_term_attrs = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            self._old_term_attrs = None
            return

        def run():
            try:
                fd = sys.stdin.fileno()
                while not self.stop_event.is_set():
                    r, _, _ = select.select([fd], [], [], 0.2)
                    if not r:
                        continue
                    ch = os.read(fd, 1)
                    if ch == b"\x1b":
                        self.stop_event.set()
                        return
            except Exception:
                return

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_event.set()
        if not self._enabled:
            return
        if not sys.stdin.isatty():
            return
        if self._old_term_attrs is None:
            return
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_term_attrs)
        except Exception:
            return

class InstagramScraper:
    """A class to handle the scraping process of an Instagram collection."""

    # --- Element Selectors ---
    LOGIN_URL = "https://www.instagram.com/" # Start at the home page for cookie injection
    COLLECTION_POST_LINK = (By.CSS_SELECTOR, "a[href^='/p/']")


    def __init__(self, session_id, collection_url, save_dir, start_post, end_post):
        self.session_id = session_id
        self.collection_url = collection_url
        self.save_dir = save_dir
        self.start_post = int(start_post) if start_post else 1
        # `end_post` may arrive as float from GUI (e.g., 1.0). Normalize it.
        if end_post == float('inf'):
            self.end_post = float('inf')
        else:
            try:
                self.end_post = int(end_post)
            except Exception:
                self.end_post = float('inf')

        self._escape_stopper = _EscapeStopper()
        
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--log-level=3")
        
        service = ChromeService(executable_path=ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Create a requests session to handle downloads
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36'
        })
        self.session.cookies.set('sessionid', self.session_id, domain=".instagram.com")

    def _should_stop(self) -> bool:
        return self._escape_stopper.stop_event.is_set()


    def _wait_for_element(self, by, value, timeout=10):
        """Wrapper for WebDriverWait to simplify finding elements."""
        try:
            return WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
        except TimeoutException:
            return None

    def dismiss_instagram_popups(self, max_rounds: int = 4):
        """Best-effort dismissal of cookie/feature dialogs; never raises."""
        # Instagram UI changes frequently; keep this heuristic-based and safe.
        button_xpaths = [
            # Cookie consent
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'allow all')]",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'decline')]",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'essential')]",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'only allow')]",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'cookies')]",
            # Feature splashes / nags
            "//button[normalize-space()='Not now' or normalize-space()='Not Now']",
            "//button[normalize-space()='Skip']",
            "//button[normalize-space()='OK' or normalize-space()='Ok']",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'not now')]",
        ]

        dialog_button_xpath = (
            "//*[@role='dialog']//button"
            "[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'not now')"
            " or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'skip')"
            " or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'ok')"
            " or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')"
            " or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'decline')"
            " or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'essential')"
            " or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'cookies')]"
        )

        for _ in range(max_rounds):
            try:
                # ESC often closes feature dialogs.
                try:
                    self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
                except Exception:
                    pass

                clicked = False

                # Prefer dismissing within role=dialog first.
                try:
                    el = WebDriverWait(self.driver, 1.5).until(EC.element_to_be_clickable((By.XPATH, dialog_button_xpath)))
                    el.click()
                    clicked = True
                except Exception:
                    pass

                if not clicked:
                    for xp in button_xpaths:
                        try:
                            el = WebDriverWait(self.driver, 1.0).until(EC.element_to_be_clickable((By.XPATH, xp)))
                            el.click()
                            clicked = True
                            break
                        except Exception:
                            continue

                if not clicked:
                    return

                time.sleep(0.6)
            except Exception:
                return

    def _sync_driver_cookies_to_requests_session(self):
        """Copy Selenium-managed cookies into the requests session; never raises."""
        try:
            for c in self.driver.get_cookies() or []:
                name = c.get("name")
                value = c.get("value")
                domain = c.get("domain") or ".instagram.com"
                if not name or value is None:
                    continue
                # requests will accept a leading dot domain.
                self.session.cookies.set(name, value, domain=domain)
        except Exception:
            return

    def setup_session(self):
        """Sets up the browser session using the provided session cookie."""
        print("\nSetting up session with cookie...")
        self.driver.get(self.LOGIN_URL)
        
        if not self.session_id:
            print("Error: Session ID is empty. Please provide a valid cookie.")
            return False
            
        self.driver.add_cookie({
            'name': 'sessionid',
            'value': self.session_id,
            'domain': '.instagram.com',
            'path': '/',
            'secure': True,
            'httponly': True
        })
        
        print("Cookie injected. Refreshing to apply session...")
        self.driver.refresh()
        medium_delay()

        # Best-effort dismissal of cookie/feature popups on landing.
        self.dismiss_instagram_popups()

        # Keep requests session cookies in sync with the browser (helps __a=1 JSON).
        self._sync_driver_cookies_to_requests_session()
        
        print("Session setup appears successful.")
        return True


    def navigate_and_load_collection(self):
        """
        Navigates to the collection URL and scrolls just enough to load the
        posts required by the user's end_post number.
        """
        print(f"\nNavigating to collection: {self.collection_url}")
        self.driver.get(self.collection_url)

        # Best-effort dismissal of cookie/feature popups that can block the collection UI.
        self.dismiss_instagram_popups()

        print("Waiting for collection to load...")
        if not self._wait_for_element(*self.COLLECTION_POST_LINK, timeout=20):
            print("Error: Could not load the collection page. Is the URL correct, and is your session cookie valid?")
            return False

        if self.end_post == float('inf'):
            print("Collection loaded. Scrolling to reveal all posts...")
        else:
            print(f"Collection loaded. Scrolling to reveal at least {self.end_post} posts...")

        last_height = self.driver.execute_script("return document.body.scrollHeight")
        
        while True:
            if self._should_stop():
                print("\nESC pressed. Stopping...")
                return False
            # If a specific end post is set, check if we have loaded enough posts
            if self.end_post != float('inf'):
                post_elements = self.driver.find_elements(*self.COLLECTION_POST_LINK)
                if len(post_elements) >= self.end_post:
                    print(f"Found {len(post_elements)} posts, which is enough to proceed.")
                    break
            
            # Scroll down to load more content
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            medium_delay()
            
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                print("Reached the end of the collection.")
                break
            last_height = new_height
        
        return True

    def scrape_and_download(self):
        """Scrapes all post links and downloads media within the specified range."""
        print("\nScanning for all post links...")
        
        post_elements = self.driver.find_elements(*self.COLLECTION_POST_LINK)
        post_urls = [elem.get_attribute('href') for elem in post_elements]
        
        if not post_urls:
            print("No posts found in the collection.")
            return

        print(f"Found {len(post_urls)} total posts.")

        start_index = max(0, self.start_post - 1)
        if self.end_post == float('inf'):
            end_index = len(post_urls)
        else:
            end_index = min(len(post_urls), int(self.end_post))
        
        posts_to_download = post_urls[start_index:end_index]
        total_to_download = len(posts_to_download)
        if total_to_download == 0:
            print("No posts to download in the requested range.")
            return

        print(f"Will download posts from {self.start_post} to {end_index} ({total_to_download} posts).")

        for i, post_url in enumerate(posts_to_download):
            if self._should_stop():
                print("\nESC pressed. Stopping...")
                return
            post_number = start_index + i + 1
            print(f"\n--- Processing Post {post_number}/{end_index} ---")
            print(f"URL: {post_url}")
            
            media_urls, post_date = self._extract_media_from_page_source(post_url)
            
            if not media_urls or not post_date:
                print("Warning: Could not extract metadata for this post. Skipping.")
                continue

            post_id = post_url.strip('/').split('/')[-1]

            print(f"Found {len(media_urls)} media item(s) in this post.")

            for seq, media_url in enumerate(media_urls):
                if self._should_stop():
                    print("\nESC pressed. Stopping...")
                    return
                if not self._is_downloadable_url(media_url):
                    print(f"Skipping non-downloadable extracted URL: {media_url}")
                    continue
                self._download_file(media_url, post_id, post_date, seq + 1, post_number)
        
        print("\n--- All Done! ---")

    def _find_post_json(self, data, post_id):
        """Recursively search a dictionary or list for the post's main JSON object."""
        if isinstance(data, dict):
            # Prefer objects that look like actual media nodes.
            if data.get('shortcode') == post_id:
                has_media_shape = (
                    'edge_sidecar_to_children' in data
                    or 'display_url' in data
                    or 'video_url' in data
                    or 'carousel_media' in data
                    or data.get('is_video') is True
                    or data.get('media_type') == 2
                )
                if has_media_shape:
                    return data

            # Older embedded-style object (uses 'code')
            if data.get('code') == post_id:
                has_media_shape = (
                    'image_versions2' in data
                    or 'video_versions' in data
                    or 'carousel_media' in data
                    or data.get('media_type') in (1, 2, 8)
                )
                if has_media_shape:
                    return data

            for value in data.values():
                found = self._find_post_json(value, post_id)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self._find_post_json(item, post_id)
                if found:
                    return found
        return None

    def _extract_balanced_json_object(self, text, start_index):
        """Extract a balanced JSON object substring starting at the given '{' index."""
        if start_index < 0 or start_index >= len(text) or text[start_index] != '{':
            return None

        depth = 0
        in_string = False
        escape = False

        for i in range(start_index, len(text)):
            ch = text[i]

            if in_string:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue

            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start_index:i + 1]

        return None

    def _get_post_datetime_from_dom(self):
        """Try to get an ISO datetime string from the rendered DOM."""
        # Instagram typically renders a <time datetime="..."> element.
        for t in self.driver.find_elements(By.TAG_NAME, "time"):
            dt = t.get_attribute("datetime")
            if dt:
                # Ensure a consistent 'Z' suffix when possible.
                if dt.endswith("+00:00"):
                    return dt.replace("+00:00", "Z")
                return dt
        return None

    def _get_video_url_from_dom(self):
        """Try to get the actual video URL from the rendered DOM."""
        def is_blob(url):
            return bool(url) and url.startswith("blob:")

        # Prefer OG meta tags first (usually a real CDN URL, not a blob:).
        for selector in (
            "meta[property='og:video:url']",
            "meta[property='og:video:secure_url']",
            "meta[property='og:video']",
            "meta[name='twitter:player:stream']",
        ):
            for m in self.driver.find_elements(By.CSS_SELECTOR, selector):
                content = m.get_attribute("content")
                if content and not is_blob(content):
                    return content

        # Then try <video><source src="..."> (often real even when video.src is blob:).
        for s in self.driver.find_elements(By.CSS_SELECTOR, "video source"):
            src = s.get_attribute("src")
            if src and not is_blob(src):
                return src

        # Finally, try <video src/currentSrc>, but ignore blob: URLs.
        for v in self.driver.find_elements(By.TAG_NAME, "video"):
            src = v.get_attribute("src")
            if src and not is_blob(src):
                return src
            current_src = v.get_attribute("currentSrc")
            if current_src and not is_blob(current_src):
                return current_src

        return None

    def _pick_best_from_srcset(self, srcset: Optional[str]) -> Optional[str]:
        if not srcset or not isinstance(srcset, str):
            return None
        candidates = []
        for part in srcset.split(','):
            item = part.strip()
            if not item:
                continue
            pieces = item.split()
            url = pieces[0].strip()
            width = None
            if len(pieces) >= 2 and pieces[1].endswith('w'):
                try:
                    width = int(pieces[1][:-1])
                except ValueError:
                    width = None
            candidates.append((width or -1, url))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[-1][1]

    def _get_image_url_from_dom(self):
        """Try to get the actual image URL from the rendered DOM."""
        # Prefer OG meta tags first.
        for selector in (
            "meta[property='og:image:secure_url']",
            "meta[property='og:image']",
            "meta[name='twitter:image']",
        ):
            for m in self.driver.find_elements(By.CSS_SELECTOR, selector):
                content = m.get_attribute("content")
                if self._is_downloadable_url(content):
                    return content

        # Then look for likely post images in the page.
        for img in self.driver.find_elements(By.CSS_SELECTOR, "article img, main img"):
            src = img.get_attribute("src")
            srcset = img.get_attribute("srcset")
            best = self._pick_best_from_srcset(srcset) or src
            if self._is_downloadable_url(best):
                return best

        return None

    def _is_downloadable_url(self, url: Optional[str]) -> bool:
        if not url:
            return False

        if not isinstance(url, str):
            return False

        url = url.strip()
        if not url:
            return False

        if url.startswith("blob:") or url.startswith("data:"):
            return False
        try:
            return urlparse(url).scheme in ("http", "https")
        except Exception:
            return False

    def _unescape_instagram_url(self, url: str) -> str:
        """Unescape common sequences found in Instagram-embedded URLs."""
        return (
            url
            .replace('\\/', '/')
            .replace('\\u0026', '&')
            .replace('\\u003D', '=')
            .replace('\\u003F', '?')
            .replace('\\u0025', '%')
        )

    def _find_mp4_url_in_page_source(self):
        """Back-compat wrapper for callers that only searched for mp4."""
        return self._find_video_url_in_page_source(extensions=("mp4",))

    def _find_video_url_in_page_source(self, extensions=("mp4", "m4v")):
        """Best-effort search for a real video URL in the current page source."""
        html = self.driver.page_source or ""

        # 1) Prefer known JSON keys that often contain direct playback URLs.
        # These can appear escaped (https:\/\/) and may include unicode escapes.
        key_patterns = (
            r'"video_url"\s*:\s*"(https?:\\/\\/[^"\\]+)"',
            r'"playback_url"\s*:\s*"(https?:\\/\\/[^"\\]+)"',
            r'"contentUrl"\s*:\s*"(https?:\\/\\/[^"\\]+)"',
        )
        for pat in key_patterns:
            m = re.search(pat, html)
            if m:
                url = self._unescape_instagram_url(m.group(1))
                if self._is_downloadable_url(url):
                    return url

        ext_group = "|".join(re.escape(ext) for ext in extensions)

        # 2) Look for escaped video URLs anywhere.
        m = re.search(rf"https?:\\/\\/[^\"\\s]+?\\.(?:{ext_group})[^\"\\s]*", html)
        if m:
            url = self._unescape_instagram_url(m.group(0))
            if self._is_downloadable_url(url):
                return url

        # 3) Look for unescaped video URLs.
        m = re.search(rf"https?://[^\"\\s]+?\.(?:{ext_group})[^\"\\s]*", html)
        if m and self._is_downloadable_url(m.group(0)):
            return m.group(0)

        return None

    def _page_looks_like_video(self):
        """Heuristic to decide whether the current post is a video/reel."""
        # DOM signal
        if self.driver.find_elements(By.TAG_NAME, "video"):
            return True

        # Meta tag signal
        if self.driver.find_elements(
            By.CSS_SELECTOR,
            "meta[property='og:video'], meta[property='og:video:secure_url'], meta[property='og:video:url']",
        ):
            return True

        # HTML signal
        html = self.driver.page_source or ""
        return (
            '"is_video":true' in html
            or '"media_type":2' in html
            or 'og:video' in html
            or '"video_url"' in html
            or '"playback_url"' in html
            or '"contentUrl"' in html
        )

    def _url_path_ext(self, url: Optional[str]) -> str:
        if not isinstance(url, str) or not url:
            return ""
        try:
            return os.path.splitext(urlparse(url).path)[1].lower()
        except Exception:
            return ""

    def _wait_for_video_hints(self, timeout_seconds: float = 5.0):
        """Give the page a moment to populate video-related elements/metadata."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._page_looks_like_video():
                return True
            time.sleep(0.4)
        return False

    def _extract_timestamp_from_json(self, data):
        """Try common keys and then do a recursive search for a Unix timestamp."""
        if not isinstance(data, (dict, list)):
            return None

        if isinstance(data, dict):
            for key in ("taken_at_timestamp", "taken_at", "created_time"):
                value = data.get(key)
                if isinstance(value, int) and value > 0:
                    return value

            for value in data.values():
                found = self._extract_timestamp_from_json(value)
                if found is not None:
                    return found
        else:
            for item in data:
                found = self._extract_timestamp_from_json(item)
                if found is not None:
                    return found

        return None

    def _find_first_video_url_in_json(self, data):
        """Recursively search JSON for a plausible video URL."""
        if isinstance(data, dict):
            # Common v1 shape: video_versions: [{url: ...}, ...]
            try:
                video_versions = data.get("video_versions")
                if isinstance(video_versions, list) and video_versions:
                    for v in video_versions:
                        if isinstance(v, dict):
                            u = v.get("url")
                            if isinstance(u, str) and self._is_downloadable_url(u):
                                return u
            except Exception:
                pass

            for key in ("video_url", "playback_url", "contentUrl"):
                value = data.get(key)
                if isinstance(value, str) and self._is_downloadable_url(value):
                    # Prefer obvious video URLs when present.
                    ext = self._url_path_ext(value)
                    if ext in (".mp4", ".m4v"):
                        return value
                    return value

            for value in data.values():
                found = self._find_first_video_url_in_json(value)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self._find_first_video_url_in_json(item)
                if found:
                    return found
        return None

    def _fetch_post_json_via_a1(self, post_id: str):
        """Fetch post JSON via ?__a=1&__d=dis endpoints using the authenticated requests session."""
        # Cookies can change after the initial login/refresh; resync before API calls.
        self._sync_driver_cookies_to_requests_session()

        # Instagram sometimes serves this JSON from different routes.
        candidates = (
            f"https://www.instagram.com/p/{post_id}/?__a=1&__d=dis",
            f"https://www.instagram.com/reel/{post_id}/?__a=1&__d=dis",
        )
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "Referer": f"https://www.instagram.com/p/{post_id}/",
            "X-Requested-With": "XMLHttpRequest",
        }

        # Some responses are more reliable with a CSRF token present.
        try:
            csrf = self.session.cookies.get("csrftoken")
            if csrf:
                headers["X-CSRFToken"] = csrf
        except Exception:
            pass
        for url in candidates:
            for _ in range(2):
                try:
                    resp = self.session.get(url, headers=headers, timeout=30)
                    resp.raise_for_status()
                    # Sometimes this endpoint returns HTML; guard by attempting JSON parse.
                    return resp.json()
                except (requests.exceptions.RequestException, ValueError):
                    time.sleep(0.6)
                    continue
        return None

    def _get_best_video_url(self, post_id: str):
        """Return the best downloadable video URL for the current post page, if available."""
        url = self._get_video_url_from_dom() or self._find_video_url_in_page_source() or self._find_mp4_url_in_page_source()
        if self._is_downloadable_url(url):
            return url

        api_data = self._fetch_post_json_via_a1(post_id)
        if api_data:
            candidate_post_json = self._find_post_json(api_data, post_id)
            url = self._find_first_video_url_in_json(candidate_post_json or api_data)
            if self._is_downloadable_url(url):
                return url

        return None
            
    def _extract_media_from_page_source(self, post_url):
        """
        Navigates to a post and extracts media URLs and date by intelligently
        searching for the post's JSON data within the page's script tags.
        """
        self.driver.get(post_url)
        media_urls = []
        post_date = None
        
        try:
            self._wait_for_element(By.TAG_NAME, "main", timeout=15)
            short_delay()

            post_id = post_url.strip('/').split('/')[-1]
            post_json = None

            # Strategy 1 (preferred): Next.js payload
            next_data_scripts = self.driver.find_elements(By.CSS_SELECTOR, "script#__NEXT_DATA__")
            if next_data_scripts:
                try:
                    data = json.loads(next_data_scripts[0].get_attribute("innerHTML") or "{}")
                    post_json = self._find_post_json(data, post_id)
                except json.JSONDecodeError:
                    post_json = None

            # Strategy 2: window.__additionalDataLoaded(...) payloads
            if not post_json:
                script_elements = self.driver.find_elements(By.TAG_NAME, "script")
                for script in script_elements:
                    script_html = script.get_attribute('innerHTML') or ""
                    if "__additionalDataLoaded" not in script_html:
                        continue
                    if post_id not in script_html:
                        continue

                    # Extract the JSON object argument after the first comma.
                    comma_idx = script_html.find(',')
                    brace_idx = script_html.find('{', comma_idx + 1) if comma_idx != -1 else -1
                    if brace_idx == -1:
                        continue

                    json_str = self._extract_balanced_json_object(script_html, brace_idx)
                    if not json_str:
                        continue

                    try:
                        data = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue

                    post_json = self._find_post_json(data, post_id)
                    if post_json:
                        break

            # Strategy 3 (legacy fallback): look for any script mentioning the shortcode
            if not post_json:
                script_elements = self.driver.find_elements(By.TAG_NAME, "script")
                for script in script_elements:
                    script_html = script.get_attribute('innerHTML') or ""
                    if post_id not in script_html:
                        continue

                    brace_idx = script_html.find('{')
                    if brace_idx == -1:
                        continue

                    json_str = self._extract_balanced_json_object(script_html, brace_idx)
                    if not json_str:
                        continue

                    try:
                        data = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue

                    post_json = self._find_post_json(data, post_id)
                    if post_json:
                        break

            if not post_json:
                # We can still sometimes proceed via DOM fallbacks.
                print("Warning: Could not locate post JSON; will attempt DOM fallbacks.")
            
            # --- Timestamp extraction (JSON first, DOM fallback) ---
            timestamp = self._extract_timestamp_from_json(post_json) if post_json else None
            if timestamp is not None:
                post_date = datetime.fromtimestamp(timestamp).isoformat() + "Z"
            else:
                post_date = self._get_post_datetime_from_dom()
                if not post_date:
                    print("Error: Could not find a valid timestamp (JSON or DOM).")
                    return None, None

            # --- Unified Media Extraction Logic ---
            
            if not post_json:
                # Without JSON, best-effort is DOM/meta for single video or image.
                video_url = self._get_best_video_url(post_id)
                image_url = self._get_image_url_from_dom()

                candidates = []
                if self._is_downloadable_url(video_url):
                    candidates.append(video_url)
                if self._is_downloadable_url(image_url) and not candidates:
                    candidates.append(image_url)

                media_urls = candidates
                return media_urls, post_date

            # Case 1: Modern Carousel ('edge_sidecar_to_children')
            if 'edge_sidecar_to_children' in post_json and post_json['edge_sidecar_to_children'].get('edges'):
                for edge in post_json['edge_sidecar_to_children']['edges']:
                    node = edge['node']
                    if node.get('is_video'):
                        media_urls.append(node.get('video_url'))
                    else:
                        media_urls.append(node.get('display_url'))
            # Case 2: Older Carousel ('carousel_media')
            elif 'carousel_media' in post_json and post_json['carousel_media']:
                for media_item in post_json['carousel_media']:
                    if media_item.get('media_type') == 2: # Video
                        media_urls.append(media_item['video_versions'][0]['url'])
                    else: # Image
                        media_urls.append(media_item['image_versions2']['candidates'][0]['url'])
            # Case 3: Single Video (covers both modern and older structures)
            elif post_json.get('is_video') or post_json.get('media_type') == 2:
                url = post_json.get('video_url')
                if not url and 'video_versions' in post_json:
                    url = post_json['video_versions'][0]['url']
                if (not url) or (url and not self._is_downloadable_url(url)):
                    url = self._get_best_video_url(post_id)
                media_urls.append(url)
            # Case 4: Single Image (covers both modern and older structures)
            else:
                url = post_json.get('display_url')
                if not url and 'image_versions2' in post_json:
                    url = post_json['image_versions2']['candidates'][0]['url']
                media_urls.append(url)

            # Clean out invalid URLs (notably blob: URLs from rendered video elements)
            media_urls = [url for url in media_urls if self._is_downloadable_url(url)]

            # If JSON was found but yielded nothing usable, fall back to DOM/meta.
            if not media_urls:
                fallback_video = self._get_best_video_url(post_id)
                fallback_image = self._get_image_url_from_dom()
                if self._is_downloadable_url(fallback_video):
                    media_urls.append(fallback_video)
                if self._is_downloadable_url(fallback_image):
                    media_urls.append(fallback_image)

            # If we grabbed only an image but the page looks like a video, try harder to get the video URL.
            if media_urls:
                image_exts = {'.jpg', '.jpeg', '.png', '.webp'}
                media_exts = {self._url_path_ext(u) for u in media_urls}
                only_images = media_exts and media_exts.issubset(image_exts)
                if only_images:
                    # Give the page a moment to hydrate; some posts reveal video hints late.
                    self._wait_for_video_hints(timeout_seconds=5.0)

                    better_video = self._get_best_video_url(post_id)
                    if self._is_downloadable_url(better_video):
                        media_urls = [better_video]

        except Exception as e:
            print(f"An unexpected error occurred during page source extraction: {e}")
            return None, None
            
        return media_urls, post_date


    def _download_file(self, url, post_id, post_date_str, sequence, post_number):
        """Downloads a file from a direct URL using the requests session."""
        try:
            if not self._is_downloadable_url(url):
                print(f"Skipping non-downloadable URL: {url}")
                return

            dt_object = datetime.fromisoformat(post_date_str.replace('Z', '+00:00'))
            formatted_date = dt_object.strftime("%Y-%m-%d_%H-%M-%S")
            safe_identifier = re.sub(r'[^\w-]', '', post_id)
            base_filename = f"{post_number}_{formatted_date}_{safe_identifier}"
            
            parsed_url = urlparse(url)
            path, ext = os.path.splitext(parsed_url.path)
            if not ext or len(ext) > 5:
                if '.jpg' in parsed_url.query: ext = '.jpg'
                elif '.mp4' in parsed_url.query: ext = '.mp4'
                else: ext = ".jpg"

            final_filename = f"{base_filename}_{sequence}{ext}"
            filepath = self._get_unique_filepath(self.save_dir, final_filename, base_filename, sequence, ext)
            
            print(f"Downloading item {sequence} to: {os.path.basename(filepath)}")

            response = self.session.get(url, stream=True, timeout=30)
            response.raise_for_status()

            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if self._should_stop():
                        print("\nESC pressed. Stopping...")
                        return
                    f.write(chunk)

        except requests.exceptions.RequestException as e:
            print(f"Error downloading {url}: {e}")
        except Exception as e:
            print(f"An unexpected error occurred during download process for {url}: {e}")
    
    def _get_unique_filepath(self, save_dir, initial_filename, base_filename, sequence, extension):
        """Checks if a filepath exists and returns a unique one if it does."""
        filepath = os.path.join(save_dir, initial_filename)
        counter = 1
        while os.path.exists(filepath):
            new_filename = f"{base_filename}_{sequence}_{counter}{extension}"
            filepath = os.path.join(save_dir, new_filename)
            counter += 1
        return filepath

    def run(self):
        """Executes the entire scraping and downloading process."""
        self._escape_stopper.start()
        try:
            print("Tip: Press ESC anytime to stop.")
            if self.setup_session():
                if self.navigate_and_load_collection():
                    self.scrape_and_download()

            print("\nScraping process finished.")
        finally:
            self._escape_stopper.stop()
            self.driver.quit()


if __name__ == "__main__":
    user_details = get_user_input()
    
    if user_details:
        try:
            scraper = InstagramScraper(*user_details)
            scraper.run()
        except Exception as e:
            print(f"\nA critical error occurred: {e}")
            print("The program will now exit.")
            # Ensure driver is closed even on a crash
            if 'scraper' in locals() and scraper.driver:
                scraper.driver.quit()
    else:
        print("Exiting.")


