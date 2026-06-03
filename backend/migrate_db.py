# backend/migrate_db.py
from sqlalchemy import text, inspect
from db import engine

def column_exists(table_name, column_name):
    """Check if a column exists in the table"""
    inspector = inspect(engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns

def table_exists(table_name):
    """Check if a table exists in the database"""
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()

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

            if not column_exists('users', 'profile_picture'):
                conn.execute(text("ALTER TABLE users ADD COLUMN profile_picture VARCHAR(500) DEFAULT '/user_icon.jpg'"))
                conn.commit()
                print("✅ Added column: profile_picture")
            else:
                print("⏭️  Column 'profile_picture' already exists")

            if not column_exists('users', 'created_at'):
                conn.execute(text("ALTER TABLE users ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"))
                conn.commit()
                print("✅ Added column: created_at")
            else:
                print("⏭️  Column 'created_at' already exists")

            # =================================================================
            # Persistent Memory — Phase 1 (within-session rolling summary)
            # =================================================================
            if not column_exists('chat_history', 'session_summary'):
                conn.execute(text("ALTER TABLE chat_history ADD COLUMN session_summary MEDIUMTEXT NULL"))
                conn.commit()
                print("✅ Added column: chat_history.session_summary")
            else:
                print("⏭️  Column 'chat_history.session_summary' already exists")

            if not column_exists('chat_history', 'summary_through_id'):
                conn.execute(text("ALTER TABLE chat_history ADD COLUMN summary_through_id INT NULL"))
                conn.commit()
                print("✅ Added column: chat_history.summary_through_id")
            else:
                print("⏭️  Column 'chat_history.summary_through_id' already exists")

            # =================================================================
            # Persistent Memory — Phase 2 (distilled-fact embeddings)
            # =================================================================
            if not column_exists('user_memories', 'embedding'):
                conn.execute(text("ALTER TABLE user_memories ADD COLUMN embedding MEDIUMTEXT NULL"))
                conn.commit()
                print("✅ Added column: user_memories.embedding")
            else:
                print("⏭️  Column 'user_memories.embedding' already exists")

            if not column_exists('user_memories', 'embedding_model'):
                conn.execute(text("ALTER TABLE user_memories ADD COLUMN embedding_model VARCHAR(64) NULL"))
                conn.commit()
                print("✅ Added column: user_memories.embedding_model")
            else:
                print("⏭️  Column 'user_memories.embedding_model' already exists")

            if not column_exists('user_memories', 'paused'):
                conn.execute(text("ALTER TABLE user_memories ADD COLUMN paused BOOLEAN NOT NULL DEFAULT FALSE"))
                conn.commit()
                print("✅ Added column: user_memories.paused")
            else:
                print("⏭️  Column 'user_memories.paused' already exists")

            # =================================================================
            # Persistent Memory — Phase 4 (verbatim turn-level recall)
            # =================================================================
            if not column_exists('chat_history', 'embedding'):
                conn.execute(text("ALTER TABLE chat_history ADD COLUMN embedding MEDIUMTEXT NULL"))
                conn.commit()
                print("✅ Added column: chat_history.embedding")
            else:
                print("⏭️  Column 'chat_history.embedding' already exists")

            if not column_exists('chat_history', 'embedding_model'):
                conn.execute(text("ALTER TABLE chat_history ADD COLUMN embedding_model VARCHAR(64) NULL"))
                conn.commit()
                print("✅ Added column: chat_history.embedding_model")
            else:
                print("⏭️  Column 'chat_history.embedding_model' already exists")

            if not column_exists('chat_history', 'topic_label'):
                conn.execute(text("ALTER TABLE chat_history ADD COLUMN topic_label VARCHAR(128) NULL"))
                conn.commit()
                print("✅ Added column: chat_history.topic_label")
            else:
                print("⏭️  Column 'chat_history.topic_label' already exists")

            # =================================================================
            # Citations persistence — Sources block survives cache/refresh/history
            # =================================================================
            if not column_exists('chat_history', 'citations'):
                conn.execute(text("ALTER TABLE chat_history ADD COLUMN citations MEDIUMTEXT NULL"))
                conn.commit()
                print("✅ Added column: chat_history.citations")
            else:
                print("⏭️  Column 'chat_history.citations' already exists")

            # =================================================================
            # Persistent Memory — Phase 3 (idle sweep) + Phase 5 (pause toggle)
            # =================================================================
            if not column_exists('users', 'last_chat_at'):
                conn.execute(text("ALTER TABLE users ADD COLUMN last_chat_at DATETIME NULL"))
                conn.commit()
                print("✅ Added column: users.last_chat_at")
            else:
                print("⏭️  Column 'users.last_chat_at' already exists")

            if not column_exists('users', 'memory_paused'):
                conn.execute(text("ALTER TABLE users ADD COLUMN memory_paused BOOLEAN NOT NULL DEFAULT FALSE"))
                conn.commit()
                print("✅ Added column: users.memory_paused")
            else:
                print("⏭️  Column 'users.memory_paused' already exists")

            # =================================================================
            # Personalized home-screen suggestions (precomputed per user)
            # =================================================================
            if not table_exists('user_suggested_questions'):
                conn.execute(text("""
                    CREATE TABLE user_suggested_questions (
                        user_id INT NOT NULL PRIMARY KEY,
                        questions MEDIUMTEXT NOT NULL,
                        generated_at DATETIME NOT NULL,
                        source_signature VARCHAR(64) NOT NULL DEFAULT '',
                        source VARCHAR(32) NOT NULL DEFAULT 'default',
                        CONSTRAINT fk_usq_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                """))
                conn.commit()
                print("✅ Created table: user_suggested_questions")
            else:
                print("⏭️  Table 'user_suggested_questions' already exists")

            # =================================================================
            # Proposals tracker -- in-flight grant submissions with task lists.
            # =================================================================
            if not table_exists('submissions'):
                conn.execute(text("""
                    CREATE TABLE submissions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        title VARCHAR(255) NOT NULL,
                        sponsor VARCHAR(64) NOT NULL DEFAULT 'Internal',
                        deadline DATETIME NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'active',
                        notes TEXT NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX ix_submissions_user_id (user_id),
                        CONSTRAINT fk_submissions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                """))
                conn.commit()
                print("✅ Created table: submissions")
            else:
                print("⏭️  Table 'submissions' already exists")

            if not table_exists('submission_tasks'):
                conn.execute(text("""
                    CREATE TABLE submission_tasks (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        submission_id INT NOT NULL,
                        title VARCHAR(255) NOT NULL,
                        description TEXT NULL,
                        kb_doc_id VARCHAR(128) NULL,
                        due_offset_days INT NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'pending',
                        notes TEXT NULL,
                        sort_order INT NOT NULL DEFAULT 0,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX ix_submission_tasks_submission_id (submission_id),
                        CONSTRAINT fk_submission_tasks_submission FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
                    )
                """))
                conn.commit()
                print("✅ Created table: submission_tasks")
            else:
                print("⏭️  Table 'submission_tasks' already exists")

            # Deadline Watcher idempotency log -- one row per (submission, threshold)
            # email we've sent. The watcher consults this on every run to avoid
            # double-sending the same "deadline in N days" reminder.
            if not table_exists('deadline_reminder_log'):
                conn.execute(text("""
                    CREATE TABLE deadline_reminder_log (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        submission_id INT NOT NULL,
                        threshold_days INT NOT NULL,
                        sent_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        sent_to VARCHAR(255) NULL,
                        INDEX ix_deadline_reminder_submission_id (submission_id),
                        INDEX ix_deadline_reminder_threshold (submission_id, threshold_days),
                        CONSTRAINT fk_deadline_reminder_submission FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
                    )
                """))
                conn.commit()
                print("✅ Created table: deadline_reminder_log")
            else:
                print("⏭️  Table 'deadline_reminder_log' already exists")

            # =================================================================
            # Purge legacy student-data schema and rename the legacy role.
            # Drops the degreeworks/banner/canvas tables, removes dead `users`
            # columns, and renames role "student" -> "user".
            # =================================================================
            for dead_table in ('degreeworks_data', 'banner_student_data', 'canvas_student_data'):
                if table_exists(dead_table):
                    conn.execute(text(f"DROP TABLE {dead_table}"))
                    conn.commit()
                    print(f"✅ Dropped table: {dead_table}")
                else:
                    print(f"⏭️  Table '{dead_table}' already absent")

            for dead_col in ('student_id', 'major', 'morgan_connected', 'morgan_connected_at'):
                if column_exists('users', dead_col):
                    conn.execute(text(f"ALTER TABLE users DROP COLUMN {dead_col}"))
                    conn.commit()
                    print(f"✅ Dropped column: users.{dead_col}")
                else:
                    print(f"⏭️  Column 'users.{dead_col}' already absent")

            role_result = conn.execute(text("UPDATE users SET role='user' WHERE role='student'"))
            conn.commit()
            print(f"✅ Renamed role 'student' -> 'user' on {role_result.rowcount} row(s)")

            # =================================================================
            # Research-admin profile fields (department / title / primary_role)
            # =================================================================
            if not column_exists('users', 'department'):
                conn.execute(text("ALTER TABLE users ADD COLUMN department VARCHAR(128) NULL"))
                conn.commit()
                print("✅ Added column: users.department")
            else:
                print("⏭️  Column 'users.department' already exists")

            if not column_exists('users', 'title'):
                conn.execute(text("ALTER TABLE users ADD COLUMN title VARCHAR(128) NULL"))
                conn.commit()
                print("✅ Added column: users.title")
            else:
                print("⏭️  Column 'users.title' already exists")

            if not column_exists('users', 'primary_role'):
                conn.execute(text("ALTER TABLE users ADD COLUMN primary_role VARCHAR(32) NULL"))
                conn.commit()
                print("✅ Added column: users.primary_role")
            else:
                print("⏭️  Column 'users.primary_role' already exists")

            print("\n✅ Database migration completed successfully!")
            
        except Exception as e:
            print(f"❌ Migration error: {e}")
            raise

if __name__ == "__main__":
    migrate()
