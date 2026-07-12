from sqlalchemy import inspect, select, text

from app.db.session import Base, SessionLocal, engine
from app.models.entities import Project, User, UserProjectGrant
from app.services.security import hash_password


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_tender_winner_column()
    with SessionLocal() as db:
        project = db.scalar(select(Project).where(Project.code == "DEMO"))
        if not project:
            project = Project(name="默认演示项目", code="DEMO", description="系统初始化创建的演示项目")
            db.add(project)
            db.flush()

        users = [
            ("admin", "admin123", "admin", "系统管理员"),
            ("internal", "internal123", "internal", "内部采购用户"),
            ("supplier", "supplier123", "supplier", "供应商用户"),
        ]
        for username, password, role, display_name in users:
            user = db.scalar(select(User).where(User.username == username))
            if not user:
                user = User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    display_name=display_name,
                    is_active=True,
                )
                db.add(user)
                db.flush()
                if role != "admin":
                    db.add(UserProjectGrant(user_id=user.id, project_id=project.id))
        db.commit()


def _ensure_tender_winner_column() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("tender_records"):
        return
    columns = {column["name"] for column in inspector.get_columns("tender_records")}
    if "winner" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tender_records ADD COLUMN winner VARCHAR(255) NULL"))
        try:
            conn.execute(text("CREATE INDEX ix_tender_records_winner ON tender_records (winner)"))
        except Exception as exc:
            print(f"[DB] winner index creation skipped: {exc}")
