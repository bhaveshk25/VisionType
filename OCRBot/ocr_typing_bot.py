#!/usr/bin/env python3
"""
macOS Copy-Paste Tool (OCR & Auto-Typing Bot)
A macOS menu bar application to capture text via OCR and simulate physical keystrokes,
bypassing environments where clipboard paste is restricted or blocked.
"""

import os
import shutil
import subprocess
import threading
import time
from PIL import Image
import pyautogui
import pyperclip
import pytesseract
import rumps

# Try importing pynput for global hotkeys
try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False

# Auto-detect Tesseract binary path on macOS
TESSERACT_CANDIDATES = [
    shutil.which("tesseract"),
    "/opt/homebrew/bin/tesseract",       # Apple Silicon Homebrew
    "/usr/local/bin/tesseract",          # Intel Mac Homebrew
    "/usr/bin/tesseract",
]
TESSERACT_PATH = None
for candidate in TESSERACT_CANDIDATES:
    if candidate and os.path.exists(candidate):
        TESSERACT_PATH = candidate
        pytesseract.pytesseract.tesseract_cmd = candidate
        break

TEMP_IMAGE_PATH = "/tmp/ocr_capture.png"
MAX_HISTORY = 5


class OCRTypingBot(rumps.App):
    def __init__(self):
        super(OCRTypingBot, self).__init__("📋 OCR Bot")
        
        self.captured_text = ""
        self.history = []
        self.typing_interval = 0.03   # Normal speed
        self.menu_delay = 2.0         # Seconds to wait when typing is triggered from menu
        self.is_typing = False

        # Build Menu Items
        self.preview_item = rumps.MenuItem("Preview: (Empty)", callback=None)
        self.capture_item = rumps.MenuItem("📸 Capture Text (⌘⇧C)", callback=self.on_capture_clicked)
        self.type_item = rumps.MenuItem("⌨️  Type Text (⌘⇧V)", callback=self.on_type_clicked)
        
        # History Submenu
        self.history_menu = rumps.MenuItem("🕒 Recent Captures")
        self.update_history_menu()

        # Speed Submenu
        self.speed_menu = rumps.MenuItem("⚡ Typing Speed")
        self.speed_fast = rumps.MenuItem("Fast (0.01s)", callback=lambda s: self.set_speed(0.01, s))
        self.speed_normal = rumps.MenuItem("Normal (0.03s)", callback=lambda s: self.set_speed(0.03, s))
        self.speed_slow = rumps.MenuItem("Safe / Slow (0.06s)", callback=lambda s: self.set_speed(0.06, s))
        self.speed_normal.state = 1
        self.speed_menu.add(self.speed_fast)
        self.speed_menu.add(self.speed_normal)
        self.speed_menu.add(self.speed_slow)

        # Delay Submenu
        self.delay_menu = rumps.MenuItem("⏳ Menu Focus Delay")
        self.delay_1s = rumps.MenuItem("1 Second", callback=lambda s: self.set_delay(1.0, s))
        self.delay_2s = rumps.MenuItem("2 Seconds", callback=lambda s: self.set_delay(2.0, s))
        self.delay_3s = rumps.MenuItem("3 Seconds", callback=lambda s: self.set_delay(3.0, s))
        self.delay_2s.state = 1
        self.delay_menu.add(self.delay_1s)
        self.delay_menu.add(self.delay_2s)
        self.delay_menu.add(self.delay_3s)

        # Help & Info
        self.info_item = rumps.MenuItem("ℹ️  Keyboard Shortcuts", callback=self.show_shortcuts_info)

        # Assemble full menu
        self.menu = [
            self.preview_item,
            rumps.separator,
            self.capture_item,
            self.type_item,
            rumps.separator,
            self.history_menu,
            self.speed_menu,
            self.delay_menu,
            rumps.separator,
            self.info_item,
        ]

    def set_speed(self, interval, sender):
        self.typing_interval = interval
        for item in [self.speed_fast, self.speed_normal, self.speed_slow]:
            item.state = 0
        sender.state = 1

    def set_delay(self, delay, sender):
        self.menu_delay = delay
        for item in [self.delay_1s, self.delay_2s, self.delay_3s]:
            item.state = 0
        sender.state = 1

    def show_shortcuts_info(self, _):
        rumps.alert(
            title="macOS Copy-Paste Tool Shortcuts",
            message=(
                "• ⌘ + ⇧ + C  or  ⌘ + ⇧ + X : Capture screen area & OCR\n"
                "• ⌘ + ⇧ + V : Simulate physical typing of current text\n\n"
                "Tip: When using menu bar 'Type Text', you have a short delay "
                "to click into your target input field."
            )
        )

    def update_history_menu(self):
        self.history_menu.clear()
        if not self.history:
            empty_item = rumps.MenuItem("No recent captures")
            self.history_menu.add(empty_item)
        else:
            for idx, text in enumerate(self.history):
                truncated = (text[:30] + "...") if len(text) > 30 else text
                # Replace newlines for clean menu labels
                label = f"{idx + 1}. {truncated.replace(chr(10), ' ')}"
                item = rumps.MenuItem(label, callback=self.make_history_callback(text))
                self.history_menu.add(item)
            self.history_menu.add(rumps.separator)
            clear_item = rumps.MenuItem("Clear History", callback=self.clear_history)
            self.history_menu.add(clear_item)

    def make_history_callback(self, text):
        def callback(_):
            self.set_active_text(text)
            rumps.notification("OCR Bot", "Active Text Updated", f"Selected: {text[:40]}...")
        return callback

    def clear_history(self, _):
        self.history.clear()
        self.update_history_menu()
        rumps.notification("OCR Bot", "History Cleared", "Recent capture history has been wiped.")

    def set_active_text(self, text):
        self.captured_text = text
        pyperclip.copy(text)
        preview = text.strip().replace("\n", " ")
        if len(preview) > 32:
            preview = preview[:32] + "..."
        self.preview_item.title = f"Current: \"{preview}\"" if preview else "Preview: (Empty)"

    def add_to_history(self, text):
        if not text or not text.strip():
            return
        if text in self.history:
            self.history.remove(text)
        self.history.insert(0, text)
        if len(self.history) > MAX_HISTORY:
            self.history.pop()
        self.update_history_menu()

    def ocr_from_selection(self):
        global TESSERACT_PATH
        if os.path.exists(TEMP_IMAGE_PATH):
            try:
                os.remove(TEMP_IMAGE_PATH)
            except OSError:
                pass

        # Interactive screenshot selection
        subprocess.run(f"screencapture -i {TEMP_IMAGE_PATH}", shell=True)

        if not os.path.exists(TEMP_IMAGE_PATH):
            # User pressed Escape or cancelled screenshot
            return

        try:
            image = Image.open(TEMP_IMAGE_PATH)
            raw_text = pytesseract.image_to_string(image)
            text = raw_text.strip()

            if text:
                self.set_active_text(text)
                self.add_to_history(text)
                rumps.notification(
                    "OCR Bot",
                    "Text Captured & Copied!",
                    f"{len(text)} characters extracted ready to type."
                )
            else:
                rumps.notification("OCR Bot", "OCR Result Empty", "No legible text was detected in the selection.")
        except pytesseract.TesseractNotFoundError:
            rumps.alert(
                title="Tesseract Not Found",
                message=(
                    "Tesseract OCR is not installed or not in your PATH.\n\n"
                    "Install it via Homebrew:\nbrew install tesseract"
                )
            )
        except Exception as e:
            rumps.notification("OCR Bot", "OCR Extraction Error", str(e))
        finally:
            if os.path.exists(TEMP_IMAGE_PATH):
                try:
                    os.remove(TEMP_IMAGE_PATH)
                except OSError:
                    pass

    def perform_typing(self, delay=0.0):
        if self.is_typing:
            rumps.notification("OCR Bot", "Typing in Progress", "Please wait for current text to finish.")
            return

        # Choose clipboard text or fallback to captured text
        clipboard_text = pyperclip.paste()
        text_to_type = self.captured_text if self.captured_text else (clipboard_text if clipboard_text else "")

        if not text_to_type or not text_to_type.strip():
            rumps.notification("OCR Bot", "No Text Available", "Capture text first (⌘⇧C) or copy to clipboard.")
            return

        self.is_typing = True
        try:
            if delay > 0:
                rumps.notification(
                    "OCR Bot",
                    f"Typing in {int(delay)}s...",
                    "Click on your target window/input field now!"
                )
                time.sleep(delay)
            else:
                # Hotkey triggered: buffer 350ms so user releases Cmd and Shift
                time.sleep(0.35)

            # Type multiline text line-by-line using keyboard events
            lines = text_to_type.splitlines()
            for idx, line in enumerate(lines):
                if idx > 0:
                    pyautogui.press("enter")
                    time.sleep(self.typing_interval)
                if line:
                    try:
                        pyautogui.write(line, interval=self.typing_interval)
                    except Exception:
                        # Fallback for unicode or special characters
                        for ch in line:
                            try:
                                pyautogui.write(ch)
                            except Exception:
                                pass
                            time.sleep(self.typing_interval)
        finally:
            self.is_typing = False

    def on_capture_clicked(self, _):
        threading.Thread(target=self.ocr_from_selection, daemon=True).start()

    def on_type_clicked(self, _):
        threading.Thread(target=self.perform_typing, args=(self.menu_delay,), daemon=True).start()


def start_hotkey_listener(app: OCRTypingBot):
    """
    Listens for global macOS hotkeys:
      - Cmd + Shift + C  (or Cmd + Shift + X) -> Trigger OCR Capture
      - Cmd + Shift + V                       -> Trigger Physical Typing
    """
    if not PYNPUT_AVAILABLE:
        print("⚠️  pynput is not installed. Hotkeys will be disabled.")
        return

    try:
        def on_capture():
            threading.Thread(target=app.ocr_from_selection, daemon=True).start()

        def on_type():
            threading.Thread(target=app.perform_typing, args=(0.0,), daemon=True).start()

        hotkeys = keyboard.GlobalHotKeys({
            "<cmd>+<shift>+c": on_capture,
            "<cmd>+<shift>+x": on_capture,
            "<cmd>+<shift>+v": on_type,
        })
        hotkeys.start()
        print("✅ Global hotkeys registered: ⌘⇧C (Capture), ⌘⇧X (Alt Capture), ⌘⇧V (Type)")
    except Exception as e:
        print(f"⚠️  Failed to register global hotkeys: {e}")
        print("Ensure Terminal/Python has Accessibility permissions in System Settings.")


if __name__ == "__main__":
    print("🚀 Starting macOS OCR Typing Bot...")
    if TESSERACT_PATH:
        print(f"🔍 Tesseract detected at: {TESSERACT_PATH}")
    else:
        print("⚠️  Tesseract not found in standard paths. Run: brew install tesseract")

    app = OCRTypingBot()
    start_hotkey_listener(app)
    app.run()
