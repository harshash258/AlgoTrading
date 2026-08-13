"""
Root forwarder to scripts/fetch_nse_symbols.py
"""
import runpy
import sys
import os

if __name__ == "__main__":
    target = os.path.join(os.path.dirname(__file__), "scripts", "fetch_nse_symbols.py")
    runpy.run_path(target, run_name="__main__")
