from sqlalchemy import String, Double, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
class StorageState(Base):
    __tablename__ = 'storage_state'
    id: Mapped[int] = mapped_column(primary_key=True)
    current_dir: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[float] = mapped_column(Double)
class DiskMigration(Base):
    __tablename__ = 'disk_migrations'
    id: Mapped[int] = mapped_column(primary_key=True)
    old_dir: Mapped[str] = mapped_column(String(255))
    new_dir: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default='migrating')
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    retired_at: Mapped[float | None] = mapped_column(Double, nullable=True)
class DeliveryMigration(Base):
    __tablename__ = 'delivery_migrations'
    __table_args__ = (UniqueConstraint('migration_id', 'delivery_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    migration_id: Mapped[int] = mapped_column(ForeignKey('disk_migrations.id'))
    delivery_id: Mapped[int] = mapped_column(ForeignKey('deliveries.id'))
    status: Mapped[str] = mapped_column(String(20), default='pending')
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    copied_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    verified_at: Mapped[float | None] = mapped_column(Double, nullable=True)
