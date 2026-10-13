#!/usr/bin/env python
import os
os.chdir('/')

from alembic.config import Config
from alembic.script import ScriptDirectory

alembic_cfg = Config("/alembic.ini")
script = ScriptDirectory.from_config(alembic_cfg)

print("Heads:", script.get_heads())
print("Current head:", script.get_current_head())
