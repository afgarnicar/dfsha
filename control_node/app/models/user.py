"""Modelos de usuarios y grupos.

Define las entidades de identidad del sistema: usuarios, grupos y la relación
muchos-a-muchos entre ambos. Las contraseñas nunca se guardan en texto plano;
se almacena únicamente el hash Argon2.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from shared.constants import MAX_NAME_LENGTH


class User(Base):
    """Usuario del sistema de archivos distribuido."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(MAX_NAME_LENGTH), unique=True, nullable=False, index=True
    )
    # Hash Argon2 de la contraseña; nunca se almacena la contraseña en claro.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Grupos a los que pertenece el usuario (vía tabla de asociación).
    group_memberships: Mapped[list["GroupMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Group(Base):
    """Grupo de usuarios, usado para permisos compartidos."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(MAX_NAME_LENGTH), unique=True, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Miembros del grupo (vía tabla de asociación).
    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class GroupMember(Base):
    """Relación muchos-a-muchos entre usuarios y grupos."""

    __tablename__ = "group_members"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    group: Mapped["Group"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="group_memberships")
