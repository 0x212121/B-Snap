"""Prevent duplicate canonical and legacy WhatsApp phone identities.

Revision ID: 20261103_wa_phone_unique
Revises: 20261103_retire_inbox

Existing values are deliberately not rewritten. If two rows differ only by a
leading plus, the upgrade fails and an administrator must resolve the conflict.
"""

import sqlalchemy as sa

from alembic import op

revision = "20261103_wa_phone_unique"
down_revision = "20261103_retire_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Guard both formats using one unique identity key."""
    op.create_index(
        "uq_whatsapp_phone_canonical",
        "whatsapp_whitelist",
        [sa.text("ltrim(phone_number, '+')")],
        unique=True,
    )


def downgrade() -> None:
    """Remove the compatibility index without changing stored records."""
    op.drop_index("uq_whatsapp_phone_canonical", table_name="whatsapp_whitelist")
