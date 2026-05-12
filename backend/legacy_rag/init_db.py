# backend/init_db.py

from .db import engine, Base
from .models import User

# This creates the tables in your MySQL 'chatbot' database
Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    print("Tables created")
