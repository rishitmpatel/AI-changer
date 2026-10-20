"""PyInstaller entry point for AI Switch.exe / AI Switch.app."""
import multiprocessing
import sys

from ai_switch.desktop.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
