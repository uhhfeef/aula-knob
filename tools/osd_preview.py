"""Show the brightness pop-up for a few seconds (for checking the look / taking a screenshot)."""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
from osd import BrightnessOSD

value = int(sys.argv[1]) if len(sys.argv) > 1 else 70
osd = BrightnessOSD(print)
time.sleep(0.8)
for _ in range(25):
    osd.show(value)
    time.sleep(0.3)
time.sleep(2)
