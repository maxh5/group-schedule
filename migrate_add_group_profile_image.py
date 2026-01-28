"""
Migration script to add profile_image column to groups table
and set all existing groups to default 'default_group.jpg'
"""
from app import app, db

with app.app_context():
    # Add the profile_image column with default 'default_group.jpg' for existing groups
    with db.engine.connect() as conn:
        # Check if column exists
        result = conn.execute(db.text("PRAGMA table_info(groups)"))
        columns = [row[1] for row in result]
        
        if 'profile_image' not in columns:
            print("Adding profile_image column to groups table...")
            conn.execute(db.text("ALTER TABLE groups ADD COLUMN profile_image VARCHAR(200) DEFAULT 'default_group.jpg'"))
            conn.commit()
            
            # Update all existing rows to have default profile image
            conn.execute(db.text("UPDATE groups SET profile_image = 'default_group.jpg' WHERE profile_image IS NULL"))
            conn.commit()
            
            print("✓ Migration completed successfully!")
            print("  - Added 'profile_image' column to groups")
            print("  - Set all existing groups to 'default_group.jpg'")
        else:
            print("Profile_image column already exists. No migration needed.")
