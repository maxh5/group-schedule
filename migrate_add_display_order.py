"""
Migration script to add display_order column to group_members table
"""
from extras.extensions import db
from app import app
from sqlalchemy import text

def migrate():
    with app.app_context():
        # Add the display_order column if it doesn't exist
        try:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE group_members ADD COLUMN display_order INTEGER DEFAULT 0'))
                conn.commit()
            print("✓ Successfully added display_order column to group_members table")
        except Exception as e:
            if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                print("✓ Column display_order already exists, skipping")
            else:
                print(f"✗ Error: {e}")
                raise

if __name__ == '__main__':
    migrate()
