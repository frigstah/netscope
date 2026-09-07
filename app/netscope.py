#!/usr/bin/env python3
"""NetScope entry point. Developed for and by frig."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netscope.cli import main  # noqa: E402

sys.exit(main())
