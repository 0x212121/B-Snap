#!/usr/bin/env python
import sys
import os
os.chdir('/')
sys.path.insert(0, '/app')

from alembic.config import Config
from alembic import command
import traceback

try:
    alembic_cfg = Config("/alembic.ini")
    command.upgrade(alembic_cfg, "head")
    print("Migration completed successfully!")
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
