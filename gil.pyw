"""
gil.pyw — consoleless launcher for Project G.I.L.
Run this file (double-click or pythonw) instead of main.py.
No terminal window is created, so GIL runs silently in the background.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from main import main
main()
