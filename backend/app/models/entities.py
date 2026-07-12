from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base, TimestampMixin):

    """
    用户模型类，继承自Base和TimestampMixin
    使用SQLAlchemy ORM定义users表结构
    """
    __tablename__ = "users"  # 指定数据库表名为"users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # 用户ID，主键，自增
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 用户名，唯一，建立索引
    password_hash: Mapped[str] = mapped_column(String(128))  # 密码哈希值
    role: Mapped[str] = mapped_column(String(32), index=True)  # 用户角色，建立索引
    display_name: Mapped[str] = mapped_column(String(128))  # 显示名称
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # 是否激活状态，默认为True

    grants = relationship("UserProjectGrant", back_populates="user")  # 与UserProjectGrant模型建立双向关系


class Project(Base, TimestampMixin):

    """
    Project类，表示一个项目实体，继承自Base和TimestampMixin
    包含项目的基本信息，如ID、名称、代码、描述等
    """
    __tablename__ = "projects"  # 指定数据库表名为"projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # 项目ID，主键，自增
    name: Mapped[str] = mapped_column(String(128))  # 项目名称，最大长度128字符
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 项目代码，唯一且建立索引
    description: Mapped[str] = mapped_column(Text, default="")  # 项目描述，文本类型，默认为空字符串
    internal_only: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否仅内部可见，布尔类型，默认为False


class UserProjectGrant(Base, TimestampMixin):

    """
    用户项目授权关联表，用于记录用户对项目的访问权限关系。
    继承自Base基础模型和TimestampMixin时间戳混合类，提供基础ORM功能和时间戳字段。
    """
    __tablename__ = "user_project_grants"  # 指定数据库表名为"user_project_grants"
    # 定义表级别的约束，确保user_id和project_id的组合值唯一
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_user_project"),)

    # 主键字段，自增整数类型
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 外键字段，关联到users表的id字段，并创建索引
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # 外键字段，关联到projects表的id字段，并创建索引
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)

    # 定义与User模型的双向关系，关联User模型的grants属性
    user = relationship("User", back_populates="grants")
    # 定义与Project模型的关系，单向关联
    project = relationship("Project")


class ImportJob(Base, TimestampMixin):

    """
    导入任务数据模型类
    继承自Base基类和TimestampMixin混入类，用于处理导入任务的相关数据
    """
    __tablename__ = "import_jobs"  # 指定数据库表名为"import_jobs"

    # 主键ID字段，自增整型
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 域名字段，长度为32的字符串，并创建索引
    domain: Mapped[str] = mapped_column(String(32), index=True)
    # 文件名字段，最大长度为255的字符串
    file_name: Mapped[str] = mapped_column(String(255))
    # 状态字段，默认值为"pending"，用于记录导入任务的状态
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # 错误报告字段，Text类型，默认空字符串，用于存储导入过程中的错误信息
    error_report: Mapped[str] = mapped_column(Text, default="")
    # 总行数字段，默认值为0，记录导入文件的总行数
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    # 成功行数字段，默认值为0，记录成功导入的行数
    success_rows: Mapped[int] = mapped_column(Integer, default=0)
    # 失败行数字段，默认值为0，记录导入失败的行数
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)


class PolicyRecord(Base, TimestampMixin):
    __tablename__ = "policy_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    publish_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    region: Mapped[str] = mapped_column(String(128), default="")
    scope: Mapped[str] = mapped_column(String(255), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    source_file_name: Mapped[str] = mapped_column(String(255), default="")
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    access_level: Mapped[str] = mapped_column(String(32), default="public", index=True)

    project = relationship("Project")


class TenderRecord(Base, TimestampMixin):
    __tablename__ = "tender_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    project_name: Mapped[str] = mapped_column(String(255), index=True)
    project_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    tenderer: Mapped[str] = mapped_column(String(255), default="", index=True)
    winner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    agency: Mapped[str] = mapped_column(String(255), default="", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="", index=True)
    region: Mapped[str] = mapped_column(String(128), default="", index=True)
    publish_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    bid_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    content_summary: Mapped[str] = mapped_column(Text, default="")
    contact_name: Mapped[str] = mapped_column(String(128), default="")
    contact_phone: Mapped[str] = mapped_column(String(64), default="")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    source_file_name: Mapped[str] = mapped_column(String(255), default="")
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    access_level: Mapped[str] = mapped_column(String(32), default="public", index=True)

    project = relationship("Project")


class EnterpriseRecord(Base, TimestampMixin):
    __tablename__ = "enterprise_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    enterprise_name: Mapped[str] = mapped_column(String(255), index=True)
    unified_social_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    region: Mapped[str] = mapped_column(String(128), default="", index=True)
    industry: Mapped[str] = mapped_column(String(128), default="", index=True)
    business_scope: Mapped[str] = mapped_column(Text, default="")
    remark: Mapped[str] = mapped_column(Text, default="")
    source_file_name: Mapped[str] = mapped_column(String(255), default="")
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    access_level: Mapped[str] = mapped_column(String(32), default="public", index=True)

    project = relationship("Project")


class EnterpriseRelation(Base, TimestampMixin):
    __tablename__ = "enterprise_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise_records.id"), index=True)
    target_enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprise_records.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="")


class Attachment(Base, TimestampMixin):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    record_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str] = mapped_column(String(255))
    uploaded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    access_level: Mapped[str] = mapped_column(String(32), default="public", index=True)


class TextChunk(Base, TimestampMixin):
    __tablename__ = "text_chunks"
    __table_args__ = (UniqueConstraint("chunk_key", name="uq_chunk_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    record_id: Mapped[int] = mapped_column(Integer, index=True)
    chunk_key: Mapped[str] = mapped_column(String(255))
    source_field: Mapped[str] = mapped_column(String(128))
    chunk_order: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    content_preview: Mapped[str] = mapped_column(String(255), default="")
    embedding_model: Mapped[str] = mapped_column(String(128), default="hashing")
    vector_indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="新会话")


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    question_domain: Mapped[str] = mapped_column(String(32), default="")
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
