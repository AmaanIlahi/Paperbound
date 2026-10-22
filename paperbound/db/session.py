from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from paperbound import config

engine = create_engine(
    config.DATABASE_URL,
    pool_size=5,
    max_overflow=0,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_session():
    with SessionLocal() as session:
        yield session
