#!/usr/bin/env python3
"""
Setup script to create database tables for testing the calendar sync implementation.
"""

import asyncio
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.productivity_bot.models import Base


def setup_test_database():
    """Create all database tables for testing."""
    # Use a test SQLite database
    database_url = "sqlite:///./test_calendar_sync.db"

    # Create synchronous engine for table creation
    engine = create_engine(database_url)

    # Create all tables
    Base.metadata.create_all(bind=engine)

    print("✅ Test database created successfully!")
    print(f"📁 Database file: {os.path.abspath('./test_calendar_sync.db')}")
    print("📋 Tables created:")
    for table_name in Base.metadata.tables.keys():
        print(f"   - {table_name}")


if __name__ == "__main__":
    setup_test_database()
