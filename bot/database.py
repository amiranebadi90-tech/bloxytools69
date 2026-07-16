from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os

Base = declarative_base()

DB_DIR = os.getenv("DB_DIR", ".")
os.makedirs(DB_DIR, exist_ok=True)
engine = create_engine(f'sqlite:///{DB_DIR}/licenses.db', echo=False)
Session = sessionmaker(bind=engine)

class License(Base):
    __tablename__ = 'licenses'

    id = Column(Integer, primary_key=True)
    key = Column(String(20), unique=True, nullable=False)

    used = Column(Boolean, default=False)
    user_id = Column(Integer, nullable=True)
    username = Column(String, nullable=True)

    banned = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)

    # مدت زمان لایسنس بر حسب دقیقه. None یعنی همیشگی (بدون انقضا)
    duration_minutes = Column(Integer, nullable=True)
    # تاریخ انقضا، بعد از فعال شدن لایسنس محاسبه و ذخیره می‌شود
    expires_at = Column(DateTime, nullable=True)
    # برای اینکه پیام «لایسنس تموم شده» فقط یک‌بار برای کاربر ارسال شود
    expired_notified = Column(Boolean, default=False)

    # ====================== لایسنس هوش مصنوعی (محدود به تعداد استفاده) ======================
    # این لایسنس‌ها به‌جای محدودیت زمانی، با تعداد قابلیت مجاز محدود می‌شوند.
    # کلید این لایسنس‌ها با پسوند "_AI" ساخته می‌شود (مثلاً AB12-CD34-EF56-GH78_AI).
    is_ai_license = Column(Boolean, default=False)
    # حداکثر تعداد قابلیتی که این لایسنس اجازه می‌دهد (پیش‌فرض 2)
    ai_uses_limit = Column(Integer, nullable=True)
    # تعداد باقیمانده؛ فقط وقتی یک قابلیت با موفقیت کامل شد (فایل نهایی ارسال شد) کم می‌شود
    ai_uses_left = Column(Integer, nullable=True)

Base.metadata.create_all(engine)


def _ensure_new_columns():
    """مهاجرت ساده برای دیتابیس‌های قدیمی‌تر: ستون‌های جدید رو در صورت نبودن اضافه می‌کند."""
    with engine.connect() as conn:
        existing_cols = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(licenses)").fetchall()
        }
        new_columns = {
            "duration_minutes": "INTEGER",
            "expires_at": "DATETIME",
            "expired_notified": "BOOLEAN DEFAULT 0",
            "is_ai_license": "BOOLEAN DEFAULT 0",
            "ai_uses_limit": "INTEGER",
            "ai_uses_left": "INTEGER",
        }
        for col_name, col_type in new_columns.items():
            if col_name not in existing_cols:
                conn.exec_driver_sql(f"ALTER TABLE licenses ADD COLUMN {col_name} {col_type}")
        conn.commit()


_ensure_new_columns()
