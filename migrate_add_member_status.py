"""
Migration script to add status column to group_members table
and set all existing members to 'accepted' status
"""
from app import app, db

with app.app_context():
    # Add the status column with default 'accepted' for existing members
    with db.engine.connect() as conn:
        # Check if column exists
        result = conn.execute(db.text("PRAGMA table_info(group_members)"))
        columns = [row[1] for row in result]
        
        if 'status' not in columns:
            print("Adding status column to group_members table...")
            conn.execute(db.text("ALTER TABLE group_members ADD COLUMN status VARCHAR(20) DEFAULT 'accepted'"))
            conn.commit()
            
            # Update all existing rows to have 'accepted' status
            conn.execute(db.text("UPDATE group_members SET status = 'accepted' WHERE status IS NULL"))
            conn.commit()
            
            print("✓ Migration completed successfully!")
            print("  - Added 'status' column to group_members")
            print("  - Set all existing members to 'accepted' status")
        else:
            print("Status column already exists. No migration needed.")
