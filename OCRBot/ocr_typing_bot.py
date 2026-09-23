#!/usr/bin/env python3
"""
macOS Copy-Paste Tool (OCR & Auto-Typing Bot)
A macOS menu bar application to capture text via OCR and simulate physical keystrokes,
bypassing environments where clipboard paste is restricted or blocked.

New Features:
- 🎭 Natural / Human Typing Simulation (randomized keystroke jitter)
- 🧹 Smart Text Cleaners (fix OCR line breaks, strip code line numbers, trim spaces, case conversion)
- ✍️ Custom Text Input Modal (enter text directly into buffer)
- 📊 Live Buffer Statistics (character, word, line counts)
- 🔊 macOS Audio Feedback (Tink / Pop sounds on actions)
"""

import os
import random
import re
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


# ---------------------------------------------------------
# Text Cleaning & Formatting Helpers
# ---------------------------------------------------------
def clean_join_lines(text: str) -> str:
    """Joins lines that were awkwardly broken by OCR word-wrapping."""
    lines = [line.strip() for line in text.splitlines()]
    if not lines:
        return ""
    joined = []
    current_para = []
    for line in lines:
        if not line:
            if current_para:
                joined.append(" ".join(current_para))
                current_para = []
        else:
            current_para.append(line)
    if current_para:
        joined.append(" ".join(current_para))
    return "\n\n".join(joined)


def clean_strip_line_numbers(text: str) -> str:
    """Strips leading code line numbers (e.g. '1 | ', '01: ', '12. ', '>>> ')."""
    pattern = re.compile(r"^\s*(?:\d+[\.\:\)\|]|\(?\d+\)?\s*\||\[\d+\]|>>>|\.\.\.)\s*", re.MULTILINE)
    return pattern.sub("", text)


def clean_excess_whitespace(text: str) -> str:
    """Trims trailing spaces and collapses consecutive blank lines."""
    lines = [line.rstrip() for line in text.splitlines()]
    result = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                result.append("")
                prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return "\n".join(result).strip()


class OCRTypingBot(rumps.App):
    def __init__(self):
        super(OCRTypingBot, self).__init__("📋 OCR Bot")
        
        self.captured_text = ""
        self.history = []
        self.typing_mode = "normal"   # 'fast', 'normal', 'slow', 'human'
        self.typing_interval = 0.03   # Interval for fixed modes
        self.menu_delay = 2.0         # Seconds to wait when typing is triggered from menu
        self.is_typing = False
        self.sound_enabled = True

        # Build Menu Items
        self.preview_item = rumps.MenuItem("Preview: (Empty)", callback=None)
        self.capture_item = rumps.MenuItem("📸 Capture Text (⌘⇧C)", callback=self.on_capture_clicked)
        self.type_item = rumps.MenuItem("⌨️  Type Text (⌘⇧V)", callback=self.on_type_clicked)
        self.custom_text_item = rumps.MenuItem("✍️  Enter Custom Text...", callback=self.on_enter_custom_text)
        
        # History Submenu
        self.history_menu = rumps.MenuItem("🕒 Recent Captures")
        self.update_history_menu()

        # Text Cleaners & Formatters Submenu
        self.cleaner_menu = rumps.MenuItem("🧹 Clean & Transform")
        self.cleaner_menu.add(rumps.MenuItem("🔗 Fix Broken Linebreaks", callback=lambda _: self.transform_text(clean_join_lines, "Linebreaks Joined")))
        self.cleaner_menu.add(rumps.MenuItem("🔢 Strip Code Line Numbers", callback=lambda _: self.transform_text(clean_strip_line_numbers, "Line Numbers Removed")))
        self.cleaner_menu.add(rumps.MenuItem("✂️  Trim Excess Whitespace", callback=lambda _: self.transform_text(clean_excess_whitespace, "Whitespace Trimmed")))
        self.cleaner_menu.add(rumps.separator)
        self.cleaner_menu.add(rumps.MenuItem("🔠 UPPERCASE", callback=lambda _: self.transform_text(str.upper, "Converted to Uppercase")))
        self.cleaner_menu.add(rumps.MenuItem("🔡 lowercase", callback=lambda _: self.transform_text(str.lower, "Converted to Lowercase")))
        self.cleaner_menu.add(rumps.MenuItem("🔤 Title Case", callback=lambda _: self.transform_text(str.title, "Converted to Title Case")))

        # Speed Submenu
        self.speed_menu = rumps.MenuItem("⚡ Typing Speed & Mode")
        self.speed_fast = rumps.MenuItem("⚡ Fast (0.01s)", callback=lambda s: self.set_speed("fast", 0.01, s))
        self.speed_normal = rumps.MenuItem("⏱️  Normal (0.03s)", callback=lambda s: self.set_speed("normal", 0.03, s))
        self.speed_slow = rumps.MenuItem("🐢 Safe / Slow (0.06s)", callback=lambda s: self.set_speed("slow", 0.06, s))
        self.speed_human = rumps.MenuItem("🎭 Natural / Human (Jitter)", callback=lambda s: self.set_speed("human", 0.035, s))
        self.speed_normal.state = 1
        self.speed_menu.add(self.speed_fast)
        self.speed_menu.add(self.speed_normal)
        self.speed_menu.add(self.speed_slow)
        self.speed_menu.add(rumps.separator)
        self.speed_menu.add(self.speed_human)

        # Delay Submenu
        self.delay_menu = rumps.MenuItem("⏳ Menu Focus Delay")
        self.delay_1s = rumps.MenuItem("1 Second", callback=lambda s: self.set_delay(1.0, s))
        self.delay_2s = rumps.MenuItem("2 Seconds", callback=lambda s: self.set_delay(2.0, s))
        self.delay_3s = rumps.MenuItem("3 Seconds", callback=lambda s: self.set_delay(3.0, s))
        self.delay_2s.state = 1
        self.delay_menu.add(self.delay_1s)
        self.delay_menu.add(self.delay_2s)
        self.delay_menu.add(self.delay_3s)

        # Settings
        self.sound_toggle = rumps.MenuItem("🔊 Sound Effects", callback=self.toggle_sound)
        self.sound_toggle.state = 1

        # Help & Info
        self.info_item = rumps.MenuItem("ℹ️  Keyboard Shortcuts", callback=self.show_shortcuts_info)

        # Assemble full menu
        self.menu = [
            self.preview_item,
            rumps.separator,
            self.capture_item,
            self.type_item,
            self.custom_text_item,
            rumps.separator,
            self.cleaner_menu,
            self.history_menu,
            self.speed_menu,
            self.delay_menu,
            rumps.separator,
            self.sound_toggle,
            self.info_item,
        ]

    def play_sound(self, sound_name="Tink"):
        """Plays a native macOS system sound asynchronously."""
        if not self.sound_enabled:
            return
        sound_path = f"/System/Library/Sounds/{sound_name}.aiff"
        if os.path.exists(sound_path):
            try:
                subprocess.Popen(["afplay", sound_path], stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def toggle_sound(self, sender):
        self.sound_enabled = not self.sound_enabled
        sender.state = 1 if self.sound_enabled else 0
        if self.sound_enabled:
            self.play_sound("Pop")

    def set_speed(self, mode, interval, sender):
        self.typing_mode = mode
        self.typing_interval = interval
        for item in [self.speed_fast, self.speed_normal, self.speed_slow, self.speed_human]:
            item.state = 0
        sender.state = 1
        rumps.notification("OCR Bot", "Typing Mode Updated", f"Active mode: {sender.title}")

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
                "Modes:\n"
                "• Natural / Human Mode: Randomizes typing rhythm with micro-delays to evade proctoring/bot detection.\n"
                "• Clean & Transform: Strip code line numbers, fix broken lines, or change case."
            )
        )

    def on_enter_custom_text(self, _):
        """Opens a modal dialog to enter or paste custom text directly into the buffer."""
        window = rumps.Window(
            title="Enter Custom Text",
            message="Type or paste the text you want the bot to type:",
            default_text=self.captured_text,
            ok="Save to Buffer",
            cancel="Cancel",
            dimensions=(360, 160)
        )
        response = window.run()
        if response.clicked and response.text.strip():
            self.set_active_text(response.text)
            self.add_to_history(response.text)
            self.play_sound("Tink")
            rumps.notification("OCR Bot", "Custom Text Saved", f"{len(response.text)} characters ready to type.")

    def transform_text(self, transform_fn, label):
        """Applies a transformation function to the active text."""
        if not self.captured_text:
            rumps.notification("OCR Bot", "No Text to Transform", "Capture text first or enter custom text.")
            return
        transformed = transform_fn(self.captured_text)
        self.set_active_text(transformed)
        self.add_to_history(transformed)
        self.play_sound("Pop")
        rumps.notification("OCR Bot", label, f"Updated text: {len(transformed)} characters.")

    def update_history_menu(self):
        self.history_menu.clear()
        if not self.history:
            empty_item = rumps.MenuItem("No recent captures")
            self.history_menu.add(empty_item)
        else:
            for idx, text in enumerate(self.history):
                first_line = text.strip().splitlines()[0] if text.strip() else ""
                truncated = (first_line[:26] + "...") if len(first_line) > 26 else first_line
                label = f"{idx + 1}. {truncated}"
                item = rumps.MenuItem(label, callback=self.make_history_callback(text))
                self.history_menu.add(item)
            self.history_menu.add(rumps.separator)
            clear_item = rumps.MenuItem("Clear History", callback=self.clear_history)
            self.history_menu.add(clear_item)

    def make_history_callback(self, text):
        def callback(_):
            self.set_active_text(text)
            self.play_sound("Tink")
            first_line = text.strip().splitlines()[0] if text.strip() else ""
            rumps.notification("OCR Bot", "Active Text Updated", f"Selected: {first_line[:30]}...")
        return callback

    def clear_history(self, _):
        self.history.clear()
        self.update_history_menu()
        rumps.notification("OCR Bot", "History Cleared", "Recent capture history has been wiped.")

    def set_active_text(self, text):
        self.captured_text = text
        pyperclip.copy(text)
        
        # Calculate statistics
        chars = len(text)
        words = len(text.split())
        lines = len(text.splitlines()) if text else 0
        
        preview = text.strip().replace("\n", " ")
        if len(preview) > 22:
            preview = preview[:22] + "..."
        
        if text.strip():
            self.preview_item.title = f"Current ({chars}c, {words}w, {lines}L): \"{preview}\""
        else:
            self.preview_item.title = "Preview: (Empty)"

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
            # User cancelled screenshot (pressed Escape)
            return

        try:
            image = Image.open(TEMP_IMAGE_PATH)
            raw_text = pytesseract.image_to_string(image)
            text = raw_text.strip()

            if text:
                self.set_active_text(text)
                self.add_to_history(text)
                self.play_sound("Tink")
                rumps.notification(
                    "OCR Bot",
                    "Text Captured & Copied!",
                    f"{len(text)} characters extracted ready to type."
                )
            else:
                self.play_sound("Basso")
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
                    time.sleep(self.get_keystroke_delay())

                if line:
                    if self.typing_mode == "human":
                        # Type character by character with natural jitter
                        for ch in line:
                            try:
                                pyautogui.write(ch)
                            except Exception:
                                pass
                            time.sleep(self.get_keystroke_delay(char=ch))
                    else:
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
            
            self.play_sound("Pop")
        finally:
            self.is_typing = False

    def get_keystroke_delay(self, char=None):
        """Calculates keystroke delay based on active mode."""
        if self.typing_mode == "human":
            # Realistic Gaussian distribution around 35ms with 15ms variance
            delay = random.gauss(0.035, 0.015)
            delay = max(0.012, min(delay, 0.085))
            
            # Subtle extra pause after punctuation and spaces (mimics human cadence)
            if char in {".", ",", ";", ":", "!", "?"}:
                delay += random.uniform(0.08, 0.15)
            elif char == " ":
                delay += random.uniform(0.02, 0.05)
            return delay
        return self.typing_interval

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
