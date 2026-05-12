# backend/migrate_db.py
from sqlalchemy import text, inspect
from db import engine

def column_exists(table_name, column_name):
    """Check if a column exists in the table"""
    inspector = inspect(engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns

def migrate():
    print("🔄 Starting database migration...")
    
    with engine.connect() as conn:
        try:
            # Check and add each column if it doesn't exist
            if not column_exists('users', 'name'):
                conn.execute(text("ALTER TABLE users ADD COLUMN name VARCHAR(255) DEFAULT NULL"))
                conn.commit()
                print("✅ Added column: name")
            else:
                print("⏭️  Column 'name' already exists")

            if not column_exists('users', 'student_id'):
                conn.execute(text("ALTER TABLE users ADD COLUMN student_id VARCHAR(50) DEFAULT NULL"))
                conn.commit()
                print("✅ Added column: student_id")
            else:
                print("⏭️  Column 'student_id' already exists")

            if not column_exists('users', 'major'):
                conn.execute(text("ALTER TABLE users ADD COLUMN major VARCHAR(100) DEFAULT 'Computer Science'"))
                conn.commit()
                print("✅ Added column: major")
            else:
                print("⏭️  Column 'major' already exists")

            if not column_exists('users', 'profile_picture'):
                conn.execute(text("ALTER TABLE users ADD COLUMN profile_picture VARCHAR(500) DEFAULT '/user_icon.jpg'"))
                conn.commit()
                print("✅ Added column: profile_picture")
            else:
                print("⏭️  Column 'profile_picture' already exists")

            if not column_exists('users', 'morgan_connected'):
                conn.execute(text("ALTER TABLE users ADD COLUMN morgan_connected BOOLEAN DEFAULT FALSE"))
                conn.commit()
                print("✅ Added column: morgan_connected")
            else:
                print("⏭️  Column 'morgan_connected' already exists")

            if not column_exists('users', 'created_at'):
                conn.execute(text("ALTER TABLE users ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"))
                conn.commit()
                print("✅ Added column: created_at")
            else:
                print("⏭️  Column 'created_at' already exists")

            print("\n✅ Database migration completed successfully!")
            
        except Exception as e:
            print(f"❌ Migration error: {e}")
            raise

if __name__ == "__main__":
    migrate()
