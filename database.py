import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# Читаем URL базы данных из переменной окружения
# Для локальной разработки используем SQLite (по умолчанию)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./game.db")

try:
    if DATABASE_URL.startswith("postgresql"):
        print("🔄 Подключение к PostgreSQL (Neon)...")
        engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=5,
            max_overflow=0,
        )
    else:
        print("🔄 Подключение к SQLite (локально)...")
        engine = create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False}
        )
    print("✅ Подключение к базе данных успешно установлено")
except Exception as e:
    print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПОДКЛЮЧЕНИЯ К БД: {e}")
    # Если база не подключается — лучше выбросить исключение, чтобы приложение не стартовало
    raise

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Генератор для получения сессии базы данных."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
