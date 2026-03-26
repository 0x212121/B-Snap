"""Ensure all datetime columns store UTC

Revision ID: 7cd0dd6ec316
Revises: ea697ca6b008
Create Date: 2026-03-26 11:38:51.802609

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7cd0dd6ec316'
down_revision: Union[str, None] = 'ea697ca6b008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Standardize all datetime storage to UTC.
    
    This migration documents the UTC standardization effort.
    All datetime columns already have timezone=True, so no schema
    changes are needed. The application code has been updated to:
    
    1. Use datetime.now(timezone.utc) for all new timestamps
    2. Updated models: task_timing, remember_token, etc.
    3. Updated utilities: snapshot_service, email_notifier, remember_me
    4. Updated scheduler: WhatsApp reports use UTC
    
    Existing data in the database should already be in UTC format
    with timezone information.
    """
    pass


def downgrade() -> None:
    """No downgrade needed - this is a documentation migration."""
    pass
