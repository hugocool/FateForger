import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add the src directory to the path to import our models
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Set minimal environment variables to avoid config validation issues
os.environ.setdefault("SLACK_BOT_TOKEN", "alembic-dummy")
os.environ.setdefault("SLACK_SIGNING_SECRET", "alembic-dummy")
os.environ.setdefault("OPENAI_API_KEY", "alembic-dummy")
os.environ.setdefault("CALENDAR_WEBHOOK_SECRET", "alembic-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///alembic.db")

# Import our models
from productivity_bot.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


# Get the database URL from environment variable or use the one in alembic.ini
def get_url():
    """Get database URL from environment or config."""
    # Try to get from environment first (for production)
    url = os.getenv("DATABASE_URL")
    if url:
        # Convert aiosqlite URLs to regular sqlite for migrations
        if "sqlite+aiosqlite" in url:
            url = url.replace("sqlite+aiosqlite", "sqlite")
        return url

    # Fallback to alembic.ini configuration
    return config.get_main_option("sqlalchemy.url")


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Override the sqlalchemy.url in the config
    config.set_main_option("sqlalchemy.url", get_url())

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
