"""
Root forwarder to scripts/strategy_explorer.py
"""
import runpy
import sys
import os

if __name__ == "__main__":
    target = os.path.join(os.path.dirname(__file__), "scripts", "strategy_explorer.py")
    runpy.run_path(target, run_name="__main__")
