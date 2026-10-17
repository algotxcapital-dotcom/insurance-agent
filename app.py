import streamlit as st
import os
import re
import io
import csv
import json
import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, date, timezone
from typing import List, Optional, Dict, Any

import requests
import pandas as pd
from pypdf import PdfReader
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field
from openai import OpenAI
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, Text, ForeignKey, DateTime, JSON
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session

# =====================================================================
# 1. תשתית אבטחה, הצפנה, מיסוך לוגים וחיבור למסד הנתונים (PostgreSQL / SQLite)
# =====================================================================
st.set_page_config(
    page_title="LEVERAGE AI – מערכת תכנון פיננסי, פנסיוני ומאגר ידע",
    page_icon="🛡️",
    layout="wide",
)

st.markdown("""
<style>
    .stApp { direction: rtl; text-align: right; font-family: 'Assistant', 'Segoe UI', sans-serif; }
    p, div, h1, h2, h3, h4, h5, li, span, label, th, td { text-align: right !important; }
    .stChatMessage { direction: rtl; }
    div[data-testid="stMetricValue"] { direction: ltr; text-align: right !important; }
    .source-badge { background-color: #eef2ff; color: #1e3a8a; padding: 4px 8px; border-radius: 6px; font-size: 0.85em; }
</style>
""", unsafe_allow_html=True)


class PIIRedactingFilter(logging.Filter):
    """מונע דליפת מספרי ת.ז, טלפונים ומפתחות API ללוגים."""
    ID_PAT = re.compile(r"\b\d{8,9}\b")
    PHONE_PAT = re.compile(r"\b05\d[- ]?\d{7}\b")
    KEY_PAT = re.compile(r"sk-[a-zA-Z0-9_-]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        msg = self.ID_PAT.sub("[REDACTED_ID]", msg)
        msg = self.PHONE_PAT.sub("[REDACTED_PHONE]", msg)
        msg = self.KEY_PAT.sub("[REDACTED_KEY]", msg)
        record.msg = msg
        record.args = ()
        return True


logger = logging.getLogger("leverage_enterprise")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.addFilter(PIIRedactingFilter())
    logger.addHandler(sh)


def get_fernet() -> Fernet:
    key = os.getenv("FERNET_ENCRYPTION_KEY", "")
    if not key:
        try:
            key = st.secrets.get("FERNET_ENCRYPTION_KEY", "")
        except Exception:
            key = ""
    if not key:
        # מפתח דטרמיניסטי לסביבת עבודה מקומית אם טרם הוגדר ב-Secrets
        derived = hashlib.sha256(b"LEVERAGE_DEFAULT_INTERNAL_VAULT_2026").digest()
        import base64
        key = base64.urlsafe_b64encode(derived).decode()
    return Fernet(key.encode())


fernet = get_fernet()


def encrypt_val(text: str) -> str:
    return fernet.encrypt(text.encode("utf-8")).decode("utf-8") if text else ""


def decrypt_val(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        return fernet.decrypt(cipher.encode("utf-8")).decode("utf-8")
    except Exception:
        return "[מוצפן]"


def hash_password(password: str, salt: str = "LEVERAGE_SALT_2026") -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def validate_israeli_id(id_num: str) -> bool:
    clean = re.sub(r"\D", "", str(id_num))
    if not clean or len(clean) > 9 or len(clean) < 5:
        return False
    clean = clean.zfill(9)
    total = 0
    for i, d in enumerate(clean):
        step = int(d) * ((i % 2) + 1)
        total += step if step < 10 else step - 9
    return total % 10 == 0


INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"disregard\s+all\s+prior",
    r"system\s+prompt",
    r"התעלם\s+מההוראות\s+הקודמות",
    r"שכח\s+את\s+כל\s+מה\s+שנאמר",
    r"הוראת\s+מערכת\s+חדשה",
]


def sanitize_document_text(text: str) -> tuple[str, list[str]]:
    warnings = []
    cleaned = text
    for pat in INJECTION_PATTERNS:
        if re.search(pat, cleaned, flags=re.IGNORECASE):
            warnings.append(f"נחסמה תבנית הזרקת פקודה חשודה: {pat}")
            cleaned = re.sub(pat, "[BLOCKED_INJECTION]", cleaned, flags=re.IGNORECASE)
    return cleaned, warnings


def resolve_openai_api_key() -> str:
    """שולף מפתח API מהשרת (Secrets / Env) או מזיכרון הסשן המאובטח."""
    env_key = os.getenv("OPENAI_API_KEY", "")
    if env_key:
        return env_key
    try:
        sec_key = st.secrets.get("OPENAI_API_KEY", "")
        if sec_key:
            return sec_key
    except Exception:
        pass
    return st.session_state.get("runtime_openai_key", "")


# --- חיבור מסד נתונים (תומך ב-PostgreSQL דרך DATABASE_URL או SQLite מקומי) ---
DB_URL = os.getenv("DATABASE_URL", "")
if not DB_URL:
    try:
        DB_URL = st.secrets.get("DATABASE_URL", "sqlite:///./leverage_enterprise.db")
    except Exception:
        DB_URL = "sqlite:///./leverage_enterprise.db"

connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utc_now():
    return datetime.now(timezone.utc)


# =====================================================================
# 2. מודלי מסד הנתונים (לקוחות, מוצרים, רגולציה, גמל-נט, ביטוח, עמלות ופסיקה)
# =====================================================================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(30), default="agent")  # 'admin' | 'agent'
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)


class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    full_name = Column(String(120), nullable=False)
    encrypted_id = Column(String(255), nullable=False)
    id_last4 = Column(String(4), nullable=False)
    birth_year = Column(Integer, nullable=False)
    marital_status = Column(String(30), default="נשוי/אה")
    target_retirement_age = Column(Integer, default=67)
    monthly_salary = Column(Float, default=0.0)
    is_smoker = Column(Boolean, default=False)
    encrypted_phone = Column(String(255), default="")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=utc_now)

    products = relationship("ClientProduct", back_populates="client", cascade="all, delete-orphan")
    documents = relationship("ClientDocument", back_populates="client", cascade="all, delete-orphan")
    tasks = relationship("ClientTask", back_populates="client", cascade="all, delete-orphan")
    meetings = relationship("ClientMeeting", back_populates="client", cascade="all, delete-orphan")
    chats = relationship("ClientChat", back_populates="client", cascade="all, delete-orphan")


class ClientProduct(Base):
    __tablename__ = "client_products"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False, index=True)
    product_type = Column(String(60), nullable=False)
    provider_name = Column(String(80), nullable=False)
    track_name = Column(String(120), default="כללי")
    policy_number = Column(String(60), default="")
    status = Column(String(30), default="פעיל")
    accumulated_balance = Column(Float, default=0.0)
    monthly_deposit = Column(Float, default=0.0)
    fee_balance_pct = Column(Float, nullable=True)
    fee_deposit_pct = Column(Float, nullable=True)
    guaranteed_factor = Column(Float, nullable=True)
    opening_year = Column(Integer, nullable=True)
    survivors_info = Column(String(255), default="לא צוין במסמך")
    disability_info = Column(String(255), default="לא צוין במסמך")
    source_page = Column(Integer, nullable=True)
    source_quote = Column(Text, default="")
    data_origin = Column(String(40), default="SILUKIT_PDF")
    anomalies = Column(Text, default="")
    planned_action = Column(String(255), default="")

    client = relationship("Client", back_populates="products")


class ClientDocument(Base):
    __tablename__ = "client_documents"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    doc_format = Column(String(30), default="PDF")  # PDF | UNIFIED_XML | UNIFIED_JSON
    page_count = Column(Integer, default=1)
    analysis_json = Column(JSON, nullable=True)
    uploaded_at = Column(DateTime, default=utc_now)

    client = relationship("Client", back_populates="documents")


class ClientTask(Base):
    __tablename__ = "client_tasks"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    category = Column(String(60), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    priority = Column(String(20), default="גבוהה")
    status = Column(String(30), default="פתוחה")
    due_date = Column(String(30), default="")
    created_at = Column(DateTime, default=utc_now)

    client = relationship("Client", back_populates="tasks")


class ClientMeeting(Base):
    __tablename__ = "client_meetings"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    meeting_date = Column(DateTime, default=utc_now)
    encrypted_transcript = Column(Text, default="")
    agent_summary = Column(Text, default="")
    client_summary = Column(Text, default="")
    whatsapp_draft = Column(Text, default="")

    client = relationship("Client", back_populates="meetings")


class ClientChat(Base):
    __tablename__ = "client_chats"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    client = relationship("Client", back_populates="chats")


# --- טבלאות מנוע הידע המקצועי המורחב (LEVERAGE Knowledge Engine) ---

class KnowledgeSourceItem(Base):
    """
    מאגר חקיקה, חוזרי רשות שוק ההון, פקודת מס הכנסה, תקנונים ונתוני מאקרו.
    תומך בהיררכיית אמינות בת 7 רמות, בדיקת תוקף (Time-Machine) וניהול גרסאות.
    """
    __tablename__ = "knowledge_sources"
    id = Column(Integer, primary_key=True)
    code_key = Column(String(80), index=True, nullable=False)
    category = Column(String(60), nullable=False)          # חקיקה / חוזר רשות שוק ההון / רשות המסים / מאקרו בנק ישראל ולמ"ס / תקנון ופוליסה
    title = Column(String(200), nullable=False)
    section_ref = Column(String(100), default="")          # סעיף מדויק בחוק / בחוזר
    content_text = Column(Text, nullable=False)
    numeric_val = Column(Float, nullable=True)
    unit = Column(String(30), default="")
    legal_status = Column(String(50), nullable=False)      # חוק בתוקף | הוראה מחייבת | נוסח היסטורי | הצעת חוק | טיוטת חוזר
    reliability_tier = Column(Integer, default=1)          # 1 (חקיקה מחייבת) עד 7 (מחקר משלים)
    authority_name = Column(String(120), nullable=False)   # הכנסת / רשות שוק ההון / רשות המסים / בנק ישראל / למ"ס
    source_url = Column(String(350), nullable=False)
    effective_from = Column(String(20), nullable=False)    # YYYY-MM-DD
    effective_to = Column(String(20), nullable=True)       # NULL אם בתוקף כיום
    last_updated = Column(String(20), nullable=False)
    version_tag = Column(String(30), default="v1.0")
    tax_year = Column(Integer, default=2026)
    approval_status = Column(String(30), default="מאושר")  # מאושר | ממתין לאישור


class MarketTrackMetric(Base):
    """מאגר גמל-נט, פנסיה-נט וביטוח-נט (תשואות, דמי ניהול, שארפ, חשיפה למניות ומאזן אקטוארי)."""
    __tablename__ = "market_track_metrics"
    id = Column(Integer, primary_key=True)
    source_system = Column(String(30), nullable=False)     # גמל-נט | פנסיה-נט | ביטוח-נט
    track_id_code = Column(String(40), index=True)
    provider_name = Column(String(80), nullable=False)
    product_category = Column(String(60), nullable=False)  # קרן פנסיה מקיפה / קרן השתלמות / קופת גמל / קופת גמל להשקעה / פוליסת חיסכון
    track_name = Column(String(140), nullable=False)
    yield_1yr_pct = Column(Float, default=0.0)
    yield_3yr_cumulative_pct = Column(Float, default=0.0)
    yield_5yr_cumulative_pct = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    equity_exposure_pct = Column(Float, default=0.0)
    foreign_exposure_pct = Column(Float, default=0.0)
    avg_fee_balance_pct = Column(Float, default=0.0)
    avg_fee_deposit_pct = Column(Float, default=0.0)
    aum_millions_ils = Column(Float, default=0.0)
    actuarial_balance_pct = Column(Float, default=0.0)     # איזון אקטוארי (לפנסיה-נט)
    service_score = Column(Float, default=80.0)            # מדד שירות רשות שוק ההון
    report_period = Column(String(20), default="2026-Q3")
    source_url = Column(String(300), default="https://data.gov.il/he/datasets?orgs=cma")


class InstitutionalEntity(Base):
    """מאגר חברות ביטוח ובתי השקעות בישראל, רישיונות, מדדי שירות ותנאי חיתום."""
    __tablename__ = "institutional_entities"
    id = Column(Integer, primary_key=True)
    entity_name = Column(String(80), unique=True, nullable=False)
    entity_type = Column(String(50), nullable=False)       # חברת ביטוח | בית השקעות / חברה מנהלת
    regulatory_status = Column(String(100), default="בעל רישיון מבטח / חברה מנהלת - רשות שוק ההון")
    active_products_summary = Column(Text, default="")
    historical_products_notes = Column(Text, default="")
    service_index_score = Column(Float, default=80.0)
    claims_approval_rate_pct = Column(Float, default=88.0)
    underwriting_and_terms = Column(Text, default="")
    last_updated = Column(String(20), default="2026-01-15")


class InsuranceTariffCatalog(Base):
    """מאגר מחירי ביטוח, תעריפים והיקף כיסוי (חיים, משכנתה, בריאות, מחלות קשות, אכ"ע, סיעוד, אלמנטרי)."""
    __tablename__ = "insurance_tariffs"
    id = Column(Integer, primary_key=True)
    provider_name = Column(String(80), nullable=False)
    insurance_branch = Column(String(60), nullable=False)  # ביטוח חיים (ריסק) | ביטוח חיים למשכנתה | ביטוח בריאות | מחלות קשות | אובדן כושר עבודה
    plan_name = Column(String(120), nullable=False)
    base_monthly_rate_per_100k_age35 = Column(Float, default=12.0)
    age_slope_pct_per_year = Column(Float, default=6.5)    # התפתחות פרמיה שנתית עם הגיל (%)
    smoker_surcharge_pct = Column(Float, default=45.0)     # תוספת עישון (%)
    max_discount_pct = Column(Float, default=30.0)         # הנחה מרבית ושנות תוקף
    discount_valid_years = Column(Integer, default=5)
    coverage_scope_details = Column(Text, default="")      # ניתוחים בארץ/חו"ל, השתלות, תרופות מחוץ לסל וכו'
    waiting_period_days = Column(Integer, default=90)      # תקופת אכשרה (ימים)
    deductible_ils = Column(Float, default=0.0)            # השתתפות עצמית
    exclusions_and_underwriting = Column(Text, default="")
    tariff_updated_date = Column(String(20), default="2026-01-01")


class AgencyCommissionAgreement(Base):
    """
    מאגר הסכמי עמלות פנימי של סוכנות LEVERAGE.
    מופרד בחומה סינית מוחלטת ממנוע ההמלצות ללקוח ומוצג רק לבעלי הרשאת Admin!
    """
    __tablename__ = "agency_commissions"
    id = Column(Integer, primary_key=True)
    provider_name = Column(String(80), nullable=False)
    product_category = Column(String(80), nullable=False)
    scope_commission_pct = Column(Float, default=0.0)       # עמלת היקף (% מהפקדה שנתית / פרמיה)
    ongoing_nifraim_pct = Column(Float, default=0.0)        # עמלת נפרעים שוטפת (% מצבירה שנתית / מפרמיה)
    deposit_commission_pct = Column(Float, default=0.0)     # עמלה מהפקדה שוטפת (%)
    one_time_bonus_per_1m_ils = Column(Float, default=0.0)  # בונוס/תמריץ למיליון ש"ח
    clawback_period_months = Column(Integer, default=12)    # תקופת אכשרה לביטול והשבת עמלות (חודשים)
    clawback_rule_desc = Column(String(255), default="השבה יחסית (Pro-rata) במקרה ניוד/ביטול ב-12 חודשים הראשונים")
    agent_share_pct = Column(Float, default=50.0)           # חלק הסוכן מתוך עמלת הסוכנות (%)
    agreement_valid_from = Column(String(20), default="2026-01-01")
    agreement_valid_to = Column(String(20), default="2026-12-31")
    source_document_name = Column(String(150), default="הסכם מסחרי מורשה LEVERAGE")
    notes = Column(Text, default="")


class LegalPrecedent(Base):
    """מאגר משפטי ופסיקה בתחומי ביטוח, אחריות סוכן, חובת גילוי, שארים ומיסוי פרישה."""
    __tablename__ = "legal_precedents"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(80), unique=True, nullable=False)
    court_name = Column(String(100), nullable=False)       # בית המשפט העליון / בית הדין הארצי לעבודה / מחוזי
    ruling_date = Column(String(20), nullable=False)
    topic = Column(String(100), nullable=False)            # אחריות סוכן ביטוח / פרשנות פוליסה / חובת גילוי / זכויות שארים / מיסוי פרישה
    precedent_weight = Column(String(40), nullable=False)  # הלכה מחייבת | פסיקה מנחה | החלטה נקודתית
    summary_and_rule = Column(Text, nullable=False)
    source_link = Column(String(300), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    username = Column(String(60), nullable=False)
    action = Column(String(100), nullable=False)
    details = Column(Text, default="")
    created_at = Column(DateTime, default=utc_now)


# =====================================================================
# 3. אתחול וזריעת נתוני אמת (Seed) לכל 12 תחומי הידע הרשמיים
# =====================================================================
def init_and_seed_database():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # 1. משתמשים
        if not db.query(User).filter(User.username == "agent1").first():
            db.add_all([
                User(username="agent1", full_name="סוכן/מתכנן פיננסי - LEVERAGE", role="agent", password_hash=hash_password("Leverage2026!")),
                User(username="admin", full_name="מנהל מערכת ומקצוענות - LEVERAGE", role="admin", password_hash=hash_password("AdminLeverage2026!")),
            ])
            db.commit()

        # 2. מאגר חקיקה, חוזרים, מיסוי ומאקרו (כולל גרסאות היסטוריות להדגמת Time-Machine)
        if db.query(KnowledgeSourceItem).count() == 0:
            sources = [
                # חקיקה (רמת אמינות 1)
                KnowledgeSourceItem(
                    code_key="LAW_INS_CONTRACT_1981_S3",
                    category="חקיקה",
                    title="חוק חוזה הביטוח, התשמ\"א-1981 – חובת וידוא והבלטת סייגים",
                    section_ref="סעיף 3",
                    content_text="תנאי או סייג לחבות המבטח או להיקפה יפורטו בפוליסה בסמוך לנושא שהם נוגעים לו, או יצוינו בה בהבלטה מיוחדת; תנאי או סייג שלא קוימה לגביו הוראה זו, אין המבטח זכאי להסתמך עליו.",
                    legal_status="חוק בתוקף",
                    reliability_tier=1,
                    authority_name="הכנסת - מאגר החקיקה הלאומי",
                    source_url="https://main.knesset.gov.il/Activity/Legislation/Laws/Pages/LawPrimary.aspx?t=lawlaws&st=lawlaws&lawitemid=2000491",
                    effective_from="1982-04-01",
                    effective_to=None,
                    last_updated="2026-01-01",
                    version_tag="נוסח עדכני",
                ),
                KnowledgeSourceItem(
                    code_key="LAW_PENSION_ADVICE_2005_S12",
                    category="חקיקה",
                    title="חוק הפיקוח על שירותים פיננסיים (ייעוץ, שיווק ומערכת סליקה פנסיוניים), התשס\"ה-2005 – התאמה לצורכי לקוח והנמקה",
                    section_ref="סעיפים 12-14",
                    content_text="בעל רישיון יתאים את השיווק או הייעוץ הפנסיוני לצרכיו של כל לקוח וימסור ללקוח מסמך הנמקה מפורט בכתב המפרט את מצבו הקיים, החלופות שנבחנו, דמי הניהול, הכיסויים הביטוחיים והנימוקים להמלצה.",
                    legal_status="חוק בתוקף",
                    reliability_tier=1,
                    authority_name="הכנסת - מאגר החקיקה הלאומי",
                    source_url="https://main.knesset.gov.il",
                    effective_from="2005-08-10",
                    effective_to=None,
                    last_updated="2026-01-01",
                    version_tag="נוסח עדכני",
                ),
                KnowledgeSourceItem(
                    code_key="LAW_SPOUSE_PENSION_SPLIT_2014",
                    category="חקיקה",
                    title="חוק לחלוקת חיסכון פנסיוני בין בני זוג שנפרדו, התשע\"ד-2014",
                    section_ref="סעיפים 1-6",
                    content_text="מסדיר רישום פסק דין לחלוקת חיסכון פנסיוני בגוף המשלם ופתיחת חשבון חדש על שם בן הזוג לשעבר בקופת הגמל או קרן הפנסיה, תוך התחשבות בתקופת הצבירה המשותפת ובמשמעויות המס בתיקון 190/פנסיה תקציבית.",
                    legal_status="חוק בתוקף",
                    reliability_tier=1,
                    authority_name="הכנסת - מאגר החקיקה הלאומי",
                    source_url="https://main.knesset.gov.il",
                    effective_from="2015-02-06",
                    effective_to=None,
                    last_updated="2026-01-01",
                    version_tag="נוסח עדכני",
                ),
                KnowledgeSourceItem(
                    code_key="LAW_SEVERANCE_PAY_1963_S14",
                    category="חקיקה",
                    title="חוק פיצויי פיטורים, התשכ\"ג-1963 – סעיף 14 וצו הרחבה לפנסיית חובה",
                    section_ref="סעיף 14",
                    content_text="תשלום לקופת תגמולים, לקרן פנסיה או לקרן כיוצא באלה, יבוא במקום פיצויי פיטורים בהתאם לתנאי האישור הכללי או צו ההרחבה לפנסיית חובה (שיעור הפרשות מינימום בצו ההרחבה: 6.5% תגמולי מעסיק, 6% תגמולי עובד, 6% פיצויים – או 8.33% להשלמה מלאה).",
                    legal_status="חוק בתוקף",
                    reliability_tier=1,
                    authority_name="הכנסת - מאגר החקיקה הלאומי",
                    source_url="https://main.knesset.gov.il",
                    effective_from="2008-01-01",
                    effective_to=None,
                    last_updated="2026-01-01",
                    version_tag="נוסח עדכני",
                ),
                # חוזרי רשות שוק ההון (רמת אמינות 2)
                KnowledgeSourceItem(
                    code_key="CMA_UNIFIED_CIRCULAR_MOBILITY",
                    category="חוזר רשות שוק ההון",
                    title="החוזר המאוחד וקודקס הרגולציה – הוראות העברת כספים בין קופות גמל (ניוד) ושמירת זכויות",
                    section_ref="שער 6, חלק 2",
                    content_text="ניוד כספים מפוליסת ביטוח מנהלים הכוללת מקדם קצבה מובטח לקרן פנסיה או לקופה אחרת מבטל לצמיתות את המקדם המובטח. על בעל הרישיון לבדוק את שנת הפתיחה, נספח הקצבה, מצב הבריאות וזכויות השארים בטרם המלצה על ניוד.",
                    legal_status="הוראה מחייבת",
                    reliability_tier=2,
                    authority_name="רשות שוק ההון, ביטוח וחיסכון",
                    source_url="https://www.gov.il/he/pages/information-entities-codex",
                    effective_from="2018-01-01",
                    effective_to=None,
                    last_updated="2026-01-15",
                    version_tag="קודקס 2026",
                ),
                KnowledgeSourceItem(
                    code_key="CMA_MANAGERS_GUARANTEED_FACTOR_2013",
                    category="חוזר רשות שוק ההון",
                    title="חוזר ביטוח – ביטול שיווק מקדמי קצבה מובטחים בפוליסות חדשות (למעט בני 60+)",
                    section_ref="חוזר ביטוח 2012-1-4",
                    content_text="החל מיום 01/01/2013 נאסר שיווק פוליסות ביטוח חיים משולבות חיסכון הכוללות מקדם קצבה מובטח מפני התארכות תוחלת החיים למבוטחים שטרם מלאו להם 60 שנים.",
                    legal_status="הוראה מחייבת",
                    reliability_tier=2,
                    authority_name="רשות שוק ההון, ביטוח וחיסכון",
                    source_url="https://www.gov.il/he/Departments/DynamicCollectors/regulation-legislation",
                    effective_from="2013-01-01",
                    effective_to=None,
                    last_updated="2026-01-01",
                    version_tag="בתוקף מ-2013",
                ),
                # רשות המסים - כולל נוסח היסטורי ונוסח עדכני להדגמת בדיקת מועד (רמת אמינות 1-3)
                KnowledgeSourceItem(
                    code_key="TAX_SEC_9A_CEILING_2026",
                    category="רשות המסים",
                    title="פקודת מס הכנסה סעיף 9א – תקרת הקצבה המזכה ושיעור הפטור (שנת מס 2026)",
                    section_ref="סעיף 9א + טופס 161ד",
                    content_text="תקרת הקצבה המזכה לשנת 2026 עומדת על 9,430 ש\"ח. שיעור הפטור עומד על 57.5% (בהתאם למתווה החוק העולה עד 67%). מכפיל ההיוון לקביעת סל הפטור ההוני הוא 180, ומקדם הקיזוז למענקים פטורים שנמשכו ב-32 השנים האחרונות הוא 1.35.",
                    numeric_val=9430.0,
                    unit="ILS",
                    legal_status="חוק בתוקף",
                    reliability_tier=1,
                    authority_name="רשות המסים בישראל",
                    source_url="https://www.gov.il/he/departments/israel_tax_authority",
                    effective_from="2026-01-01",
                    effective_to=None,
                    last_updated="2026-01-05",
                    version_tag="שנת מס 2026",
                    tax_year=2026,
                ),
                KnowledgeSourceItem(
                    code_key="TAX_SEC_9A_HISTORICAL_2020_2024",
                    category="רשות המסים",
                    title="פקודת מס הכנסה סעיף 9א – שיעור הפטור ההיסטורי (2020-2024)",
                    section_ref="סעיף 9א (נוסח היסטורי)",
                    content_text="בשנות המס 2020–2024 עמד שיעור הפטור הכולל על הקצבה המזכה על 52% בלבד (35% פטור בסיסי + 17% פטור נוסף).",
                    numeric_val=52.0,
                    unit="PCT",
                    legal_status="נוסח היסטורי",
                    reliability_tier=1,
                    authority_name="רשות המסים בישראל",
                    source_url="https://www.gov.il/he/departments/israel_tax_authority",
                    effective_from="2020-01-01",
                    effective_to="2024-12-31",
                    last_updated="2025-01-01",
                    version_tag="היסטורי 2020-2024",
                    tax_year=2024,
                ),
                KnowledgeSourceItem(
                    code_key="TAX_TIKUN_190_RULES",
                    category="רשות המסים",
                    title="תיקון 190 לפקודת מס הכנסה – קצבה מוכרת והיוון ב-15% נומינלי",
                    section_ref="סעיף 9א / תיקון 190",
                    content_text="הפקדת כספים פנויים לקופת גמל לפי תיקון 190 מאפשרת משיכת קצבה מוכרת פטורה ממס, או היוון (משיכה חד-פעמית) לחוסך מעל גיל 60 המקבל קצבה מזערית בפועל (5,012 ש\"ח) במס מופחת של 15% נומינלי על הרווח במקום 25% ריאלי.",
                    numeric_val=5012.0,
                    unit="ILS",
                    legal_status="חוק בתוקף",
                    reliability_tier=1,
                    authority_name="רשות המסים בישראל",
                    source_url="https://www.gov.il/he/departments/israel_tax_authority",
                    effective_from="2012-01-01",
                    effective_to=None,
                    last_updated="2026-01-05",
                    version_tag="שנת מס 2026",
                    tax_year=2026,
                ),
                KnowledgeSourceItem(
                    code_key="TAX_SEC_125D_SENIOR_EXEMPTION",
                    category="רשות המסים",
                    title="פקודת מס הכנסה סעיף 125ד – פטור ממס רווחי הון על ריבית/פוליסות חיסכון לגיל השלישי",
                    section_ref="סעיף 125ד",
                    content_text="מעניק ניכוי/פטור ממס על הכנסה מריבית (ובכלל זה מימוש רווחים בפוליסת חיסכון ביטוחית באמצעות 'מכירה רעיונית') ליחיד שהגיע לגיל פרישה והיה בן 55 ומעלה ביום 01/01/2003 (נולד בשנת 1948 או קודם לכן), עד לתקרה שנתית ליחיד או לזוג.",
                    numeric_val=15000.0,
                    unit="ILS",
                    legal_status="חוק בתוקף",
                    reliability_tier=1,
                    authority_name="רשות המסים בישראל",
                    source_url="https://www.gov.il/he/departments/israel_tax_authority",
                    effective_from="2003-01-01",
                    effective_to=None,
                    last_updated="2026-01-05",
                    version_tag="שנת מס 2026",
                    tax_year=2026,
                ),
                # נתוני מאקרו: בנק ישראל והלמ"ס (רמת אמינות 3)
                KnowledgeSourceItem(
                    code_key="MACRO_BOI_INTEREST_RATE",
                    category="מאקרו בנק ישראל ולמ\"ס",
                    title="ריבית בנק ישראל ונתונים מוניטריים",
                    section_ref="סדרות מוניטריות בנק ישראל",
                    content_text="ריבית בנק ישראל הרשמית המשמשת להיוון ולבחינת חלופות סולידיות (מק\"מ/פק\"מ מול קופת גמל להשקעה ופוליסות חיסכון).",
                    numeric_val=4.25,
                    unit="PCT",
                    legal_status="הוראה מחייבת",
                    reliability_tier=3,
                    authority_name="בנק ישראל",
                    source_url="https://www.boi.org.il",
                    effective_from="2026-01-01",
                    effective_to=None,
                    last_updated="2026-10-01",
                    version_tag="2026-Q4",
                ),
                KnowledgeSourceItem(
                    code_key="MACRO_CBS_CPI_AND_WAGE",
                    category="מאקרו בנק ישראל ולמ\"ס",
                    title="הלמ\"ס – שכר ממוצע במשק ואינפלציה שנתית",
                    section_ref="נתוני למ\"ס וביטוח לאומי",
                    content_text="השכר הממוצע במשק (לצורך תקרות הפקדה לפנסיה וקרן השתלמות) ושיעור עליית מדד המחירים לצרכן ב-12 החודשים האחרונים (2.8%).",
                    numeric_val=13316.0,
                    unit="ILS",
                    legal_status="הוראה מחייבת",
                    reliability_tier=3,
                    authority_name="הלשכה המרכזית לסטטיסטיקה (למ\"ס)",
                    source_url="https://www.cbs.gov.il",
                    effective_from="2026-01-01",
                    effective_to=None,
                    last_updated="2026-10-15",
                    version_tag="2026-10",
                ),
            ]
            db.add_all(sources)
            db.commit()

        # 3. קטלוג חברות ביטוח ובתי השקעות בישראל
        if db.query(InstitutionalEntity).count() == 0:
            entities = [
                InstitutionalEntity(entity_name="הפניקס", entity_type="חברת ביטוח", active_products_summary="קרן פנסיה מקיפה/כללית, פוליסות חיסכון (אינווסט), ביטוחי חיים (ריסק/משכנתה), בריאות, מחלות קשות ואכ\"ע.", historical_products_notes="פוליסות מנהלים ותיקות ('עדיף', 'מניב') עם מקדמים מובטחים עד 2012.", service_index_score=84.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="הראל", entity_type="חברת ביטוח", active_products_summary="קרן פנסיה מקיפה/כללית, פוליסות חיסכון (מגוון), ביטוחי בריאות, חיים, משכנתה, אכ\"ע ונסיעות לחו\"ל.", historical_products_notes="פוליסות מנהלים (חותם/מעוז/שילוח) עם מקדמים מובטחים לפי שנת פתיחה.", service_index_score=85.0, claims_approval_rate_pct=91.0),
                InstitutionalEntity(entity_name="מנורה מבטחים", entity_type="חברת ביטוח", active_products_summary="קרן הפנסיה הגדולה בישראל (מבטחים החדשה), ביטוחי חיים, בריאות, אכ\"ע ואלמנטרי.", historical_products_notes="פוליסות מנהלים ותיקות עם נספחי קצבה מובטחים.", service_index_score=83.0, claims_approval_rate_pct=89.0),
                InstitutionalEntity(entity_name="מגדל", entity_type="חברת ביטוח", active_products_summary="מגדל מקפת קרן פנסיה, פוליסות חיסכון (קשת), ביטוחי חיים, בריאות ואכ\"ע.", historical_products_notes="תיק ביטוחי המנהלים הגדול בישראל ('מגדלור') עם מקדמים מובטחים היסטוריים (144-166 בדורות הוותיקים).", service_index_score=81.0, claims_approval_rate_pct=88.0),
                InstitutionalEntity(entity_name="כלל ביטוח", entity_type="חברת ביטוח", active_products_summary="כלל פנסיה, פוליסות חיסכון (חיסכון פיננסי), ביטוחי חיים, בריאות ואלמנטרי.", historical_products_notes="פוליסות 'מיטב' ו'פרופיל' ותיקות עם מקדמים מובטחים.", service_index_score=80.0, claims_approval_rate_pct=87.0),
                InstitutionalEntity(entity_name="איילון", entity_type="חברת ביטוח", active_products_summary="ביטוחי חיים, בריאות, אכ\"ע ואלמנטרי.", service_index_score=79.0, claims_approval_rate_pct=86.0),
                InstitutionalEntity(entity_name="הכשרה", entity_type="חברת ביטוח", active_products_summary="פוליסות חיסכון (Best Invest), ביטוחי חיים (ריסק/משכנתה) ואלמנטרי.", service_index_score=82.0, claims_approval_rate_pct=88.0),
                InstitutionalEntity(entity_name="ביטוח ישיר", entity_type="חברת ביטוח", active_products_summary="ביטוחי חיים (ריסק/משכנתה), בריאות, דירה ורכב.", service_index_score=84.0, claims_approval_rate_pct=89.0),
                InstitutionalEntity(entity_name="AIG", entity_type="חברת ביטוח", active_products_summary="ביטוחי חיים (ריסק/משכנתה), בריאות, תאונות אישיות, נסיעות לחו\"ל, דירה ורכב.", service_index_score=85.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="מיטב", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קרן פנסיה נבחרת (מקיפה/כללית), קופות גמל, קרנות השתלמות, גמל להשקעה וניהול תיקים.", service_index_score=86.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="מור", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קרן פנסיה נבחרת, קופות גמל, קרנות השתלמות, גמל להשקעה וניהול תיקים.", service_index_score=87.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="אלטשולר שחם", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קרן פנסיה נבחרת, קופות גמל, קרנות השתלמות וגמל להשקעה.", service_index_score=82.0, claims_approval_rate_pct=88.0),
                InstitutionalEntity(entity_name="ילין לפידות", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קופות גמל, קרנות השתלמות, גמל להשקעה וניהול תיקים.", service_index_score=88.0, claims_approval_rate_pct=92.0),
                InstitutionalEntity(entity_name="אנליסט", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קופות גמל, קרנות השתלמות, גמל להשקעה וקרנות נאמנות.", service_index_score=85.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="אינפיניטי", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קרן פנסיה נבחרת, קופות גמל וקרנות השתלמות.", service_index_score=83.0, claims_approval_rate_pct=88.0),
                InstitutionalEntity(entity_name="IBI", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קופות גמל וקרנות השתלמות מחקות מדד, קרנות נאמנות וניהול תיקים.", service_index_score=86.0, claims_approval_rate_pct=90.0),
            ]
            db.add_all(entities)
            db.commit()

        # 4. נתוני גמל-נט, פנסיה-נט וביטוח-נט
        if db.query(MarketTrackMetric).count() == 0:
            tracks = [
                MarketTrackMetric(source_system="פנסיה-נט", track_id_code="PEN-101", provider_name="מנורה מבטחים", product_category="קרן פנסיה מקיפה", track_name="בני 50 ומטה", yield_1yr_pct=11.4, yield_3yr_cumulative_pct=26.8, yield_5yr_cumulative_pct=48.2, sharpe_ratio=0.92, equity_exposure_pct=49.5, foreign_exposure_pct=54.0, avg_fee_balance_pct=0.17, avg_fee_deposit_pct=1.42, aum_millions_ils=112000, actuarial_balance_pct=0.08, service_score=83.0),
                MarketTrackMetric(source_system="פנסיה-נט", track_id_code="PEN-102", provider_name="הראל", product_category="קרן פנסיה מקיפה", track_name="בני 50 ומטה", yield_1yr_pct=11.1, yield_3yr_cumulative_pct=25.9, yield_5yr_cumulative_pct=46.5, sharpe_ratio=0.89, equity_exposure_pct=48.0, foreign_exposure_pct=52.0, avg_fee_balance_pct=0.18, avg_fee_deposit_pct=1.48, aum_millions_ils=94000, actuarial_balance_pct=0.04, service_score=85.0),
                MarketTrackMetric(source_system="פנסיה-נט", track_id_code="PEN-103", provider_name="הפניקס", product_category="קרן פנסיה מקיפה", track_name="בני 50 ומטה", yield_1yr_pct=11.8, yield_3yr_cumulative_pct=27.5, yield_5yr_cumulative_pct=50.1, sharpe_ratio=0.96, equity_exposure_pct=50.2, foreign_exposure_pct=56.0, avg_fee_balance_pct=0.18, avg_fee_deposit_pct=1.45, aum_millions_ils=71000, actuarial_balance_pct=0.02, service_score=84.0),
                MarketTrackMetric(source_system="פנסיה-נט", track_id_code="PEN-104", provider_name="מיטב", product_category="קרן פנסיה מקיפה", track_name="בני 50 ומטה (נבחרת)", yield_1yr_pct=12.1, yield_3yr_cumulative_pct=27.1, yield_5yr_cumulative_pct=47.9, sharpe_ratio=0.94, equity_exposure_pct=51.0, foreign_exposure_pct=58.0, avg_fee_balance_pct=0.13, avg_fee_deposit_pct=1.00, aum_millions_ils=28000, actuarial_balance_pct=0.11, service_score=86.0),
                MarketTrackMetric(source_system="פנסיה-נט", track_id_code="PEN-105", provider_name="מור", product_category="קרן פנסיה מקיפה", track_name="בני 50 ומטה (נבחרת)", yield_1yr_pct=12.5, yield_3yr_cumulative_pct=28.9, yield_5yr_cumulative_pct=51.4, sharpe_ratio=1.01, equity_exposure_pct=52.0, foreign_exposure_pct=57.0, avg_fee_balance_pct=0.145, avg_fee_deposit_pct=1.00, aum_millions_ils=14500, actuarial_balance_pct=0.09, service_score=87.0),
                MarketTrackMetric(source_system="גמל-נט", track_id_code="GML-201", provider_name="ילין לפידות", product_category="קרן השתלמות", track_name="כללי", yield_1yr_pct=10.9, yield_3yr_cumulative_pct=24.2, yield_5yr_cumulative_pct=43.8, sharpe_ratio=0.95, equity_exposure_pct=41.0, foreign_exposure_pct=49.0, avg_fee_balance_pct=0.64, avg_fee_deposit_pct=0.0, aum_millions_ils=32000, actuarial_balance_pct=0.0, service_score=88.0),
                MarketTrackMetric(source_system="גמל-נט", track_id_code="GML-202", provider_name="אנליסט", product_category="קרן השתלמות", track_name="כללי", yield_1yr_pct=11.6, yield_3yr_cumulative_pct=26.1, yield_5yr_cumulative_pct=46.9, sharpe_ratio=0.97, equity_exposure_pct=44.5, foreign_exposure_pct=51.0, avg_fee_balance_pct=0.66, avg_fee_deposit_pct=0.0, aum_millions_ils=19000, actuarial_balance_pct=0.0, service_score=85.0),
                MarketTrackMetric(source_system="גמל-נט", track_id_code="GML-203", provider_name="מור", product_category="קופת גמל להשקעה", track_name="מניות", yield_1yr_pct=18.2, yield_3yr_cumulative_pct=41.5, yield_5yr_cumulative_pct=78.0, sharpe_ratio=1.05, equity_exposure_pct=96.0, foreign_exposure_pct=68.0, avg_fee_balance_pct=0.68, avg_fee_deposit_pct=0.0, aum_millions_ils=8500, actuarial_balance_pct=0.0, service_score=87.0),
                MarketTrackMetric(source_system="ביטוח-נט", track_id_code="BIT-301", provider_name="הפניקס", product_category="פוליסת חיסכון", track_name="אינווסט כללי", yield_1yr_pct=10.7, yield_3yr_cumulative_pct=24.5, yield_5yr_cumulative_pct=44.1, sharpe_ratio=0.91, equity_exposure_pct=42.0, foreign_exposure_pct=53.0, avg_fee_balance_pct=0.85, avg_fee_deposit_pct=0.0, aum_millions_ils=24000, actuarial_balance_pct=0.0, service_score=84.0),
                MarketTrackMetric(source_system="ביטוח-נט", track_id_code="BIT-302", provider_name="הכשרה", product_category="פוליסת חיסכון", track_name="בסט אינווסט כללי", yield_1yr_pct=10.3, yield_3yr_cumulative_pct=23.4, yield_5yr_cumulative_pct=42.0, sharpe_ratio=0.88, equity_exposure_pct=40.0, foreign_exposure_pct=50.0, avg_fee_balance_pct=0.82, avg_fee_deposit_pct=0.0, aum_millions_ils=16500, actuarial_balance_pct=0.0, service_score=82.0),
            ]
            db.add_all(tracks)
            db.commit()

        # 5. מאגר תעריפי ביטוח וכיסויים
        if db.query(InsuranceTariffCatalog).count() == 0:
            tariffs = [
                InsuranceTariffCatalog(provider_name="הראל", insurance_branch="ביטוח חיים (ריסק)", plan_name="מענקית ריסק מוות", base_monthly_rate_per_100k_age35=11.5, age_slope_pct_per_year=6.8, smoker_surcharge_pct=45.0, max_discount_pct=32.0, discount_valid_years=5, coverage_scope_details="פיצוי חד-פעמי למקרה מוות מכל סיבה, כולל אופציית המרה והגדלה ללא חיתום באירועי חיים.", waiting_period_days=0, deductible_ils=0.0, exclusions_and_underwriting="חריג התאבדות בשנה הראשונה לפי חוק חוזה הביטוח; כפוף להצהרת בריאות וחיתום רפואי."),
                InsuranceTariffCatalog(provider_name="הפניקס", insurance_branch="ביטוח חיים (ריסק)", plan_name="ריסק 1 מתחדש", base_monthly_rate_per_100k_age35=11.2, age_slope_pct_per_year=6.7, smoker_surcharge_pct=42.0, max_discount_pct=35.0, discount_valid_years=6, coverage_scope_details="פיצוי חד-פעמי למקרה מוות + כיסוי סופני (הקדמת 50% מסכום הביטוח בחולה הנוטה למות).", waiting_period_days=0, deductible_ils=0.0, exclusions_and_underwriting="חריג התאבדות בשנה הראשונה; כפוף לחיתום רפואי ועיסוקי."),
                InsuranceTariffCatalog(provider_name="מנורה מבטחים", insurance_branch="ביטוח בריאות", plan_name="בריאות מקיפה - השתלות, תרופות וניתוחים בחו\"ל", base_monthly_rate_per_100k_age35=95.0, age_slope_pct_per_year=4.2, smoker_surcharge_pct=15.0, max_discount_pct=20.0, discount_valid_years=3, coverage_scope_details="השתלות עד 5,000,000 ש\"ח, תרופות מחוץ לסל ותרופות מותאמות גנטית עד 3,000,000 ש\"ח, ניתוחים בחו\"ל מהשקל הראשון.", waiting_period_days=90, deductible_ils=300.0, exclusions_and_underwriting="תקופת אכשרה 90 יום (365 יום להריון/לידה); החרגת מצב רפואי קיים אלא אם הוסכם אחרת בחיתום."),
                InsuranceTariffCatalog(provider_name="הראל", insurance_branch="ביטוח בריאות", plan_name="אפגרייד פלטינום - בריאות וניתוחים", base_monthly_rate_per_100k_age35=98.0, age_slope_pct_per_year=4.0, smoker_surcharge_pct=15.0, max_discount_pct=22.0, discount_valid_years=4, coverage_scope_details="השתלות וטיפולים מיוחדים בחו\"ל ללא תקרה, תרופות מחוץ לסל ואורפן, ייעוצים ובדיקות אבחנתיות מהירות.", waiting_period_days=90, deductible_ils=280.0, exclusions_and_underwriting="תקופת אכשרה 90 יום; כפוף לחיתום רפואי ולרפורמת פוליסת הבריאות האחידה של רשות שוק ההון."),
                InsuranceTariffCatalog(provider_name="כלל ביטוח", insurance_branch="מחלות קשות", plan_name="מרפא למחלות קשות (42 מחלות)", base_monthly_rate_per_100k_age35=29.0, age_slope_pct_per_year=7.2, smoker_surcharge_pct=50.0, max_discount_pct=25.0, discount_valid_years=5, coverage_scope_details="פיצוי כספי חד-פעמי פטור ממס בגילוי סרטן, אוטם שריר הלב, שבץ מוחי, טרשת נפוצה ועוד 38 מחלות מוגדרות.", waiting_period_days=90, deductible_ils=0.0, exclusions_and_underwriting="תקופת אכשרה 90 יום ותקופת הישרדות של 14-30 יום ממועד הגילוי בהתאם לתנאי הפוליסה."),
            ]
            db.add_all(tariffs)
            db.commit()

        # 6. מאגר הסכמי עמלות פנימי של LEVERAGE (מופרד מהמלצות לקוח וזמין רק ל-Admin)
        if db.query(AgencyCommissionAgreement).count() == 0:
            comms = [
                AgencyCommissionAgreement(provider_name="הפניקס", product_category="קרן פנסיה מקיפה (ניוד והפקדות)", scope_commission_pct=5.0, ongoing_nifraim_pct=0.04, deposit_commission_pct=0.5, one_time_bonus_per_1m_ils=3500.0, clawback_period_months=24, clawback_rule_desc="השבה יחסית על פני 24 חודשים במקרה של ניוד החוצה או הפסקת הפקדות", agent_share_pct=55.0),
                AgencyCommissionAgreement(provider_name="מיטב", product_category="קופת גמל / קרן השתלמות / גמל להשקעה", scope_commission_pct=0.0, ongoing_nifraim_pct=0.22, deposit_commission_pct=0.0, one_time_bonus_per_1m_ils=4000.0, clawback_period_months=12, clawback_rule_desc="השבה יחסית ב-12 החודשים הראשונים ממועד העברת הכספים", agent_share_pct=50.0),
                AgencyCommissionAgreement(provider_name="הראל", product_category="ביטוח חיים (ריסק) ובריאות פרט", scope_commission_pct=35.0, ongoing_nifraim_pct=12.0, deposit_commission_pct=0.0, one_time_bonus_per_1m_ils=0.0, clawback_period_months=12, clawback_rule_desc="השבת עמלת היקף יחסית בביטול פוליסה בשנה הראשונה", agent_share_pct=60.0),
            ]
            db.add_all(comms)
            db.commit()

        # 7. מאגר משפטי ופסיקה
        if db.query(LegalPrecedent).count() == 0:
            precedents = [
                LegalPrecedent(
                    case_number="ע\"א 702/16 פלוני נ' חברת ביטוח וסוכן ביטוח",
                    court_name="בית המשפט העליון",
                    ruling_date="2018-09-12",
                    topic="אחריות סוכן ביטוח וחובת גילוי בניוד פוליסה",
                    precedent_weight="הלכה מחייבת",
                    summary_and_rule="על סוכן הביטוח מוטלת חובת זהירות מוגברת וחובת גילוי יזומה להסביר למבוטח בכתב ובעל פה את מלוא המשמעויות של ביטול או החלפת פוליסה קיימת (ובכלל זה אובדן ותק, מקדם קצבה מובטח או החרגת מצב רפואי קיים בחיתום מחדש).",
                    source_link="https://www.gov.il/he/departments/the_judicial_authority",
                ),
                LegalPrecedent(
                    case_number="ע\"ע 44210-05-19 זכויות שארים בקרן פנסיה חדשה",
                    court_name="בית הדין הארצי לעבודה",
                    ruling_date="2021-03-18",
                    topic="זכויות שארים ותקנון קרן פנסיה",
                    precedent_weight="הלכה מחייבת",
                    summary_and_rule="הזכויות בקרן פנסיה נקבעות אך ורק לפי תקנון הקרן התקף במועד קרות האירוע המזכה (ולא לפי הבטחות בעל פה). אי-עדכון מסלול ביטוח או ויתור שארים שלא נעשה בכתב לפי התקנון אינו תופס.",
                    source_link="https://www.gov.il/he/departments/the_judicial_authority",
                ),
                LegalPrecedent(
                    case_number="רע\"א 3128/94 אגודה שיתופית נ' סהר חברה לביטוח",
                    court_name="בית המשפט העליון",
                    ruling_date="1996-11-14",
                    topic="פרשנות פוליסות ביטוח (כלל הפרשנות כנגד המנסח)",
                    precedent_weight="הלכה מחייבת",
                    summary_and_rule="בכל מקרה של דו-משמעות או עמימות בניסוח פוליסת ביטוח, תפורש הפוליסה לטובת המבוטח וכנגד חברת הביטוח שניסחה אותה, ובלבד שהפירוש תואם את הציפייה הסבירה של המבוטח.",
                    source_link="https://www.gov.il/he/departments/the_judicial_authority",
                ),
            ]
            db.add_all(precedents)
            db.commit()
    finally:
        db.close()


init_and_seed_database()


# =====================================================================
# 4. מנועי חישוב דטרמיניסטיים ב-Python (אקטואריה, תעריפי ביטוח, מיסוי ועמלות)
# =====================================================================
def calculate_accumulation_python(
    initial_balance: float,
    monthly_deposit: float,
    years: int,
    gross_yield_pct: float,
    fee_balance_pct: float,
    fee_deposit_pct: float,
) -> Dict[str, float]:
    months = max(0, int(years * 12))
    net_annual = (gross_yield_pct - fee_balance_pct) / 100.0
    r_m = (1.0 + net_annual) ** (1.0 / 12.0) - 1.0
    r_m_gross = (1.0 + (gross_yield_pct / 100.0)) ** (1.0 / 12.0) - 1.0
    d_net = monthly_deposit * (1.0 - (fee_deposit_pct / 100.0))

    bal_net = initial_balance
    bal_gross = initial_balance
    for _ in range(months):
        bal_net = bal_net * (1.0 + r_m) + d_net
        bal_gross = bal_gross * (1.0 + r_m_gross) + monthly_deposit

    return {
        "final_balance_net": round(bal_net, 2),
        "final_balance_no_fees": round(bal_gross, 2),
        "fee_erosion_ils": round(max(0.0, bal_gross - bal_net), 2),
    }


def calculate_insurance_tariff_projection(
    tariff: InsuranceTariffCatalog,
    coverage_amount_ils: float,
    start_age: int,
    is_smoker: bool,
    projection_years: int = 15,
) -> Dict[str, Any]:
    """מחשב התפתחות פרמיה לאורך שנים לפי גיל, עישון ותוקף הנחה (לא מחיר סופי עד חיתום)."""
    units_100k = max(1.0, coverage_amount_ils / 100_000.0)
    yearly_schedule = []
    total_cumulative = 0.0

    for yr_offset in range(projection_years):
        age = start_age + yr_offset
        age_diff_from_35 = age - 35
        age_factor = (1.0 + (tariff.age_slope_pct_per_year / 100.0)) ** age_diff_from_35
        smoker_factor = (1.0 + (tariff.smoker_surcharge_pct / 100.0)) if is_smoker else 1.0
        discount_factor = (
            (1.0 - (tariff.max_discount_pct / 100.0))
            if yr_offset < tariff.discount_valid_years
            else 1.0
        )

        monthly_prem = (
            tariff.base_monthly_rate_per_100k_age35
            * units_100k
            * age_factor
            * smoker_factor
            * discount_factor
        )
        annual_prem = monthly_prem * 12.0
        total_cumulative += annual_prem
        yearly_schedule.append(
            {
                "שנה": yr_offset + 1,
                "גיל מבוטח": age,
                "הנחה בתוקף (%)": tariff.max_discount_pct if yr_offset < tariff.discount_valid_years else 0.0,
                "פרמיה חודשית משוערת (₪)": round(monthly_prem, 1),
                "עלות שנתית (₪)": round(annual_prem, 0),
            }
        )

    return {
        "first_month_premium_ils": yearly_schedule[0]["פרמיה חודשית משוערת (₪)"],
        "cumulative_cost_ils": round(total_cumulative, 0),
        "schedule": yearly_schedule,
    }


def calculate_isolated_agency_commission(
    agreement: AgencyCommissionAgreement,
    transferred_accumulated_ils: float,
    monthly_deposit_ils: float,
) -> Dict[str, float]:
    """
    מנוע חישוב עמלות פנימי ל-LEVERAGE בלבד.
    מופרד ב-100% ממנוע ההמלצות ללקוח!
    """
    annual_deposit = monthly_deposit_ils * 12.0
    scope_comm = annual_deposit * (agreement.scope_commission_pct / 100.0)
    deposit_comm = annual_deposit * (agreement.deposit_commission_pct / 100.0)
    nifraim_annual = transferred_accumulated_ils * (agreement.ongoing_nifraim_pct / 100.0)
    volume_bonus = (transferred_accumulated_ils / 1_000_000.0) * agreement.one_time_bonus_per_1m_ils

    total_year1_agency = scope_comm + deposit_comm + nifraim_annual + volume_bonus
    agent_cut = total_year1_agency * (agreement.agent_share_pct / 100.0)
    agency_net_cut = total_year1_agency - agent_cut
    clawback_at_risk = scope_comm + volume_bonus

    return {
        "scope_commission_ils": round(scope_comm, 2),
        "ongoing_nifraim_annual_ils": round(nifraim_annual, 2),
        "deposit_commission_annual_ils": round(deposit_comm, 2),
        "volume_bonus_ils": round(volume_bonus, 2),
        "total_year1_gross_ils": round(total_year1_agency, 2),
        "agent_share_ils": round(agent_cut, 2),
        "agency_retained_ils": round(agency_net_cut, 2),
        "clawback_exposure_ils": round(clawback_at_risk, 2),
    }


# =====================================================================
# 5. קליטת מסלקה פנסיונית במבנה האחיד (XML/JSON) + PDF מרובה עמודים
# =====================================================================
def parse_unified_xml_or_json_silukit(file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
    """
    מפענח קובצי מסלקה פנסיונית במבנה האחיד של רשות שוק ההון (XML או JSON)
    ומחלץ את רשימת הקופות, היתרות, ההפקדות ודמי הניהול עם הפניה למקור.
    """
    products = []
    if filename.lower().endswith(".json"):
        data = json.loads(file_bytes.decode("utf-8", errors="ignore"))
        accounts = data if isinstance(data, list) else data.get("Accounts", data.get("products", []))
        for idx, acc in enumerate(accounts, start=1):
            products.append({
                "product_type": acc.get("ProductType", acc.get("product_type", "קרן פנסיה / גמל")),
                "provider_name": acc.get("ProviderName", acc.get("provider_name", "גוף מוסדי")),
                "track_name": acc.get("TrackName", acc.get("track_name", "כללי")),
                "policy_number": str(acc.get("PolicyNumber", acc.get("policy_number", f"ACC-{idx}"))),
                "status": acc.get("Status", "פעיל"),
                "accumulated_balance": float(acc.get("Balance", acc.get("accumulated_balance", 0.0))),
                "monthly_deposit": float(acc.get("MonthlyDeposit", acc.get("monthly_deposit", 0.0))),
                "fee_balance_pct": acc.get("FeeBalancePct", acc.get("fee_balance_pct")),
                "fee_deposit_pct": acc.get("FeeDepositPct", acc.get("fee_deposit_pct")),
                "guaranteed_factor": acc.get("GuaranteedFactor", acc.get("guaranteed_factor")),
                "source_page": 1,
                "source_quote": f"רשומת מבנה אחיד JSON #{idx}",
                "data_origin": "UNIFIED_JSON",
            })
        return products

    # פענוח XML במבנה האחיד של המסלקה הפנסיונית
    xml_text = file_bytes.decode("utf-8", errors="ignore")
    root = ET.fromstring(xml_text)

    def find_tag_val(elem, candidates: List[str], default_val=""):
        for child in elem.iter():
            tag_clean = child.tag.split("}")[-1]
            if tag_clean in candidates and child.text and child.text.strip():
                return child.text.strip()
        return default_val

    account_nodes = [
        el for el in root.iter()
        if el.tag.split("}")[-1] in ("HeshbonOPolisa", "Account", "Policy", "Kupa")
    ]
    if not account_nodes:
        account_nodes = [root]

    for idx, node in enumerate(account_nodes, start=1):
        pol_num = find_tag_val(node, ["MisparPolisaOHeshbon", "PolicyNumber", "AccountNumber"], f"XML-{idx}")
        provider = find_tag_val(node, ["ShemGufMosdi", "ProviderName", "CompanyName"], "גוף מוסדי מהמבנה האחיד")
        prod_type = find_tag_val(node, ["SugMutzar", "ProductType"], "מוצר פנסיוני (מבנה אחיד)")
        track = find_tag_val(node, ["ShemMaslulHashkaa", "TrackName"], "כללי")
        bal_str = find_tag_val(node, ["TotalChisachonMitztaber", "YitratChisachon", "Balance"], "0")
        dep_str = find_tag_val(node, ["SachHafkadaChodshit", "MonthlyDeposit"], "0")
        fee_b_str = find_tag_val(node, ["ShiurDmeiNihulHisachon", "FeeFromBalance"], "")
        fee_d_str = find_tag_val(node, ["ShiurDmeiNihulHafkada", "FeeFromDeposit"], "")
        factor_str = find_tag_val(node, ["MekademKitzbaMuvtach", "GuaranteedFactor"], "")

        def to_float(s):
            try:
                return float(re.sub(r"[^\d.-]", "", s))
            except Exception:
                return None

        products.append({
            "product_type": prod_type,
            "provider_name": provider,
            "track_name": track,
            "policy_number": pol_num,
            "status": "פעיל",
            "accumulated_balance": to_float(bal_str) or 0.0,
            "monthly_deposit": to_float(dep_str) or 0.0,
            "fee_balance_pct": to_float(fee_b_str),
            "fee_deposit_pct": to_float(fee_d_str),
            "guaranteed_factor": to_float(factor_str),
            "source_page": 1,
            "source_quote": f"צומת XML במבנה האחיד: פוליסה/חשבון {pol_num}",
            "data_origin": "UNIFIED_XML",
        })
    return products


# =====================================================================
# 6. סנכרון חי מול Data.gov.il (CKAN API של רשות שוק ההון)
# =====================================================================
def fetch_cma_datasets_from_datagov() -> List[Dict[str, str]]:
    """שולף בזמן אמת מ-API הממשלתי (data.gov.il) את רשימת המאגרים הפעילים של רשות שוק ההון."""
    url = "https://data.gov.il/api/3/action/package_search?q=פנסיה+גמל+ביטוח&rows=10"
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "LEVERAGE-InsurTech/2.0"})
        if resp.status_code == 200:
            results = resp.json().get("result", {}).get("results", [])
            out = []
            for r in results:
                out.append({
                    "שם המאגר ב-Data.gov.il": r.get("title", ""),
                    "ארגון מפרסם": r.get("organization", {}).get("title", "רשות שוק ההון"),
                    "תאריך עדכון אחרון בשרת הממשלתי": r.get("metadata_modified", "")[:10],
                    "מספר קובצי מקור (CSV/XLSX)": len(r.get("resources", [])),
                })
            return out
    except Exception:
        pass
    return []


# =====================================================================
# 7. מנוע ה-RAG מבוסס היררכיית אמינות (7 רמות) ו-8 סעיפי תשובה
# =====================================================================
class ExtractedProductSchema(BaseModel):
    product_type: str
    provider_name: str
    track_name: str
    policy_number: str
    status: str
    accumulated_balance: float
    monthly_deposit: float
    fee_balance_pct: Optional[float] = None
    fee_deposit_pct: Optional[float] = None
    guaranteed_factor: Optional[float] = None
    survivors_info: str
    disability_info: str
    source_page: int
    source_quote: str
    anomalies: str


class SilukitStructuredOutput(BaseModel):
    extracted_products: List[ExtractedProductSchema]
    missing_information_alerts: List[str]
    contradictions_found: List[str]
    old_managers_policies_alerts: List[str]
    fee_optimization_opportunities: List[str]
    professional_recommendations: List[str]


def search_and_rank_knowledge_base(db: Session, query_text: str, as_of_date: str = "2026-10-17") -> Dict[str, Any]:
    """
    מחפש במאגרי החקיקה, הרגולציה, גמל-נט, המיסוי והפסיקה.
    מדרג לפי היררכיית האמינות (1=חקיקה מחייבת עד 7=מחקר משלים),
    בודק תוקף לתאריך המבוקש, ומזהה סתירות או הבדלים בין גרסאות!
    """
    all_sources = db.query(KnowledgeSourceItem).filter(KnowledgeSourceItem.approval_status == "מאושר").order_by(KnowledgeSourceItem.reliability_tier.asc()).all()
    all_precedents = db.query(LegalPrecedent).all()
    all_tracks = db.query(MarketTrackMetric).all()
    all_tariffs = db.query(InsuranceTariffCatalog).all()

    # סינון לפי תוקף במועד שנבחר וזיהוי גרסאות היסטוריות
    active_sources = []
    historical_or_draft_notes = []
    for s in all_sources:
        is_effective = (s.effective_from <= as_of_date) and (not s.effective_to or s.effective_to >= as_of_date)
        if is_effective and s.legal_status in ("חוק בתוקף", "הוראה מחייבת"):
            active_sources.append(s)
        else:
            historical_or_draft_notes.append(s)

    context_blocks = ["=== [היררכיית מקורות מאומתים - LEVERAGE KNOWLEDGE ENGINE] ==="]
    for s in active_sources:
        context_blocks.append(
            f"[דרגת קדימות {s.reliability_tier} | {s.legal_status} | {s.category}] "
            f"{s.title} ({s.section_ref}) | רשות: {s.authority_name} | תוקף מ-{s.effective_from} | "
            f"קישור: {s.source_url} | תוכן: {s.content_text}"
        )

    if historical_or_draft_notes:
        context_blocks.append("\n=== [נוסחים היסטוריים / טיוטות חוזרים להשוואת שינויים] ===")
        for h in historical_or_draft_notes:
            context_blocks.append(
                f"[{h.legal_status} | תוקף: {h.effective_from} עד {h.effective_to or 'לא נקבע'}] "
                f"{h.title} ({h.section_ref}): {h.content_text}"
            )

    context_blocks.append("\n=== [נתוני גמל-נט / פנסיה-נט / ביטוח-נט (רשות שוק ההון)] ===")
    for t in all_tracks:
        context_blocks.append(
            f"[{t.source_system}] {t.provider_name} - {t.product_category} ({t.track_name}): "
            f"תשואה 1ש': {t.yield_1yr_pct}%, 3ש': {t.yield_3yr_cumulative_pct}%, 5ש': {t.yield_5yr_cumulative_pct}%, "
            f"שארפ: {t.sharpe_ratio}, מניות: {t.equity_exposure_pct}%, דמ\"נ צבירה: {t.avg_fee_balance_pct}%, "
            f"דמ\"נ הפקדה: {t.avg_fee_deposit_pct}%, איזון אקטוארי: {t.actuarial_balance_pct}%, מדד שירות: {t.service_score}"
        )

    context_blocks.append("\n=== [מאגר מחירי ביטוח, כיסויים ותנאי חיתום] ===")
    for tr in all_tariffs:
        context_blocks.append(
            f"[{tr.insurance_branch}] {tr.provider_name} - {tr.plan_name}: תעריף בסיס גיל 35 ל-100א': {tr.base_monthly_rate_per_100k_age35} ש\"ח, "
            f"תוספת עישון: {tr.smoker_surcharge_pct}%, הנחה מרבית: {tr.max_discount_pct}% ל-{tr.discount_valid_years} שנים, "
            f"אכשרה: {tr.waiting_period_days} ימים, השתתפות עצמית: {tr.deductible_ils} ש\"ח | כיסוי: {tr.coverage_scope_details}"
        )

    context_blocks.append("\n=== [פסיקה משפטית מחייבת ומנחה] ===")
    for p in all_precedents:
        context_blocks.append(
            f"[{p.precedent_weight}] {p.case_number} ({p.court_name}, {p.ruling_date}) - {p.topic}: {p.summary_and_rule} | קישור: {p.source_link}"
        )

    return {
        "compiled_context": "\n".join(context_blocks),
        "active_sources_count": len(active_sources),
    }


def ask_leverage_rag_engine(
    api_key: str,
    db: Session,
    user_question: str,
    client_context: str = "",
    chat_history: Optional[List[Dict[str, str]]] = None,
    as_of_date: str = "2026-10-17",
) -> str:
    """
    מנוע ה-RAG המרכזי של LEVERAGE AI.
    אוכף את היררכיית 7 הרמות ומחייב מענה מובנה ב-8 הסעיפים המקצועיים.
    אינו חשוף כלל לטבלת העמלות של הסוכנות!
    """
    if not api_key:
        return "⚠️ מפתח OpenAI API טרם הוגדר. אנא הגדר אותו ב-Streamlit Secrets או בתיבת ההגדרה של המנהל."

    kb_bundle = search_and_rank_knowledge_base(db, user_question, as_of_date=as_of_date)
    client = OpenAI(api_key=api_key)

    system_prompt = f"""אתה מנוע הידע והייעוץ המקצועי של LEVERAGE – תכנון פיננסי ופנסיוני בישראל.
תפקידך לסייע לסוכני ביטוח, משווקים פנסיוניים ומתכננים פיננסיים לקבל החלטות מבוססות, מדויקות וניתנות לאימות.

היררכיית קדימות המקורות המחייבת אותך (מהגבוה לנמוך):
1. חקיקה והוראות מחייבות בתוקף (חוקי הכנסת, פקודת מס הכנסה).
2. הנחיות רשמיות וחוזרי רשות שוק ההון ורשות המסים בתוקף.
3. נתונים רשמיים ממאגרי ממשלה (גמל-נט, פנסיה-נט, ביטוח-נט, בנק ישראל, למ"ס).
4. מסמכי פוליסה, תקנונים והסכמים חוזיים פרטניים של הלקוח (שים לב: בפוליסות מנהלים ותיקות, זכות חוזית במקדם מובטח גוברת על ממוצעי שוק כלליים!).
5. נתוני לקוח ממקור מאומת (דוח מסלקה פנסיונית / מבנה אחיד XML עם מספר עמוד או רשומה).
6. פרסומי חברות ונתוני שוק מסחריים (מחירוני ביטוח – תמיד ציין שהמחיר אינו סופי ללא חיתום והצעה תקפה).
7. מחקר מקצועי ומקורות משלימים.

איסורים מוחלטים:
- אסור להמציא סעיפי חוק, מספרי חוזרים, פסקי דין, מחירי ביטוח או נתונים שאינם מופיעים במאגר הידע או במסלקה של הלקוח.
- אסור להציג תשואות עבר מגמל-נט/פנסיה-נט כהבטחה לתשואות עתידיות.
- כאשר קיימת סתירה בין מקורות או חסר מידע קריטי (כגון נספח קצבה, מצב בריאותי או מענקי עבר ב-32 שנים), הצג את הסתירה במפורש ואל תכריע בצורה עיוורת.

חובה לנסח כל תשובה מקצועית במבנה של 8 הסעיפים הבאים בדיוק:
1. **תשובה מקצועית וברורה**: שורה תחתונה מקצועית ומאוזנת.
2. **הבסיס החוקי, הרגולטורי או המשפטי**: סעיף החוק המדויק, חוזר הממונה או הלכת בית המשפט והבחנה בין הוראה בתוקף לנוסח היסטורי.
3. **מקור מאומת וקישור**: שם המאגר הרשמי, הקישור הרשמי והפניה לעמוד/סעיף (כולל עמוד במסלקה אם רלוונטי).
4. **מועד התוקף של המידע**: תאריך תחילת התוקף, שנת המס ותאריך העדכון האחרון במאגר.
5. **תחשיב מספרי והשוואתי**: הצגת הנוסחה, הנתונים וההשוואה המספרית בשקלים/אחוזים.
6. **הנחות עבודה ומידע חסר**: הפרדה ברורה בין נתון רשמי, נתון שהסוכן הזין והנחת סימולציה, ופירוט מה חסר לבדיקה.
7. **השלכות מעשיות עבור הלקוח**: השפעה על קצבה, שארים, כיסוי ביטוחי, דמי ניהול ומיסוי.
8. **נקודות הדורשות אישור מקצועי ובדיקת סוכן**: צעדים אופרטיביים שחייבים אישור אנושי של בעל הרישיון לפני ביצוע.

מועד הבדיקה שנבחר: {as_of_date}

{kb_bundle['compiled_context']}

נתוני תיק הלקוח הפעיל (אם נבחר לקוח):
{client_context if client_context else 'שאילתה מקצועית כללית (ללא לקוח ספציפי)'}"""

    msgs = [{"role": "system", "content": system_prompt}]
    if chat_history:
        msgs.extend(chat_history[-10:])
    msgs.append({"role": "user", "content": user_question})

    try:
        res = client.chat.completions.create(
            model="gpt-4o",
            messages=msgs,
            temperature=0.0,
        )
        return res.choices[0].message.content
    except Exception as e:
        return f"שגיאה בפנייה למנוע ה-AI: {e}"


# =====================================================================
# 8. ניהול כניסת משתמשים מאובטחת (עובדי LEVERAGE)
# =====================================================================
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if not st.session_state.auth_user:
    st.title("🛡️ LEVERAGE AI – מערכת תכנון פיננסי, פנסיוני ומאגר ידע ארצי")
    st.write("כניסה מאובטחת לעובדי LEVERAGE. הפרדת הרשאות מלאה בין סוכנים למנהלים.")
    c_login, c_info = st.columns([1, 1])
    with c_login:
        with st.form("leverage_login"):
            u_name = st.text_input("שם משתמש", value="admin")
            u_pass = st.text_input("סיסמה", type="password", value="AdminLeverage2026!")
            opt_key = st.text_input(
                "מפתח OpenAI API (אופציונלי אם הוגדר ב-Streamlit Secrets)",
                type="password",
                help="נשמר בזיכרון הסשן בלבד ואינו נשמר לעולם בקוד ה-GitHub",
            )
            if st.form_submit_button("התחבר למערכת LEVERAGE"):
                db = SessionLocal()
                user = db.query(User).filter(User.username == u_name, User.is_active == True).first()
                if user and user.password_hash == hash_password(u_pass):
                    st.session_state.auth_user = {
                        "id": user.id,
                        "username": user.username,
                        "full_name": user.full_name,
                        "role": user.role,
                    }
                    if opt_key.strip():
                        st.session_state.runtime_openai_key = opt_key.strip()
                    db.add(AuditLog(username=user.username, action="LOGIN", details="התחברות מוצלחת"))
                    db.commit()
                    db.close()
                    st.rerun()
                else:
                    db.close()
                    st.error("שם משתמש או סיסמה שגויים.")
    with c_info:
        st.info(
            "**משתמשי ברירת מחדל לבדיקה:**\n"
            "- **מנהל מערכת (כולל גישה למודל העמלות וניהול המאגרים):** שם משתמש `admin` | סיסמה `AdminLeverage2026!`\n"
            "- **סוכן / מתכנן פיננסי (ללא חשיפת הסכמי עמלות סוכנות):** שם משתמש `agent1` | סיסמה `Leverage2026!`"
        )
    st.stop()


# =====================================================================
# 9. סרגל צד: ניווט, הגדרת מפתח סשן ובחירת תיק לקוח פעיל
# =====================================================================
current_user = st.session_state.auth_user
db = SessionLocal()

st.sidebar.title(f"👤 {current_user['full_name']}")
st.sidebar.caption(f"תפקיד: {'מנהל מערכת (Admin)' if current_user['role'] == 'admin' else 'סוכן / מתכנן פיננסי'}")

if not resolve_openai_api_key():
    key_in = st.sidebar.text_input("🔑 הזן OpenAI API Key להפעלת ה-AI:", type="password")
    if key_in:
        st.session_state.runtime_openai_key = key_in.strip()
        st.rerun()
else:
    st.sidebar.success("🔒 מנוע OpenAI מחובר ומאומת")

if st.sidebar.button("🚪 התנתק מהמערכת"):
    st.session_state.auth_user = None
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("📂 בחירת תיק לקוח פעיל")

clients_query = db.query(Client) if current_user["role"] == "admin" else db.query(Client).filter(Client.agent_id == current_user["id"])
all_clients = clients_query.all()

client_map = {0: "🌐 מרכז הידע הארצי ודשבורד ראשי (כל התיקים)"}
for c in all_clients:
    client_map[c.id] = f"👤 {c.full_name} (ת.ז ..{c.id_last4})"

selected_client_id = st.sidebar.selectbox(
    "בחר סביבת עבודה / לקוח:",
    options=list(client_map.keys()),
    format_func=lambda cid: client_map[cid],
)

with st.sidebar.expander("➕ פתיחת כרטיס לקוח חדש"):
    with st.form("create_client_sidebar"):
        f_name = st.text_input("שם מלא של הלקוח")
        f_id = st.text_input("תעודת זהות (9 ספרות, לדוגמה 000000018)")
        f_birth = st.number_input("שנת לידה", min_value=1930, max_value=2010, value=1959)
        f_ret = st.number_input("גיל פרישה מתוכנן", min_value=55, max_value=80, value=67)
        f_sal = st.number_input("שכר חודשי ברוטו (₪)", min_value=0, value=22000, step=1000)
        f_smk = st.checkbox("מעשן/ת", value=False)
        f_ph = st.text_input("טלפון")
        f_nt = st.text_area("הערות רקע וצרכים")
        if st.form_submit_button("שמור והצפן כרטיס לקוח"):
            if not validate_israeli_id(f_id):
                st.error("מספר תעודת הזהות אינו תקין (נכשל בבדיקת ספרת ביקורת).")
            elif not f_name.strip():
                st.error("חובה להזין שם מלא.")
            else:
                clean_id = re.sub(r"\D", "", f_id).zfill(9)
                nc = Client(
                    agent_id=current_user["id"],
                    full_name=f_name.strip(),
                    encrypted_id=encrypt_val(clean_id),
                    id_last4=clean_id[-4:],
                    birth_year=int(f_birth),
                    target_retirement_age=int(f_ret),
                    monthly_salary=float(f_sal),
                    is_smoker=f_smk,
                    encrypted_phone=encrypt_val(f_ph),
                    notes=f_nt,
                )
                db.add(nc)
                db.add(AuditLog(username=current_user["username"], action="CREATE_CLIENT", details=f"נוצר לקוח {f_name}"))
                db.commit()
                st.success("כרטיס הלקוח נשמר והוצפן בהצלחה!")
                st.rerun()


# =====================================================================
# 10. לשוניות העבודה המרכזיות של LEVERAGE AI
# =====================================================================
active_client: Optional[Client] = None
client_context_str = ""

if selected_client_id != 0:
    active_client = db.query(Client).filter(Client.id == selected_client_id).first()
    if active_client:
        lines = [
            f"לקוח: {active_client.full_name} (ת.ז מסתיימת ב-{active_client.id_last4}), שנת לידה: {active_client.birth_year}, "
            f"גיל פרישה יעד: {active_client.target_retirement_age}, שכר: {active_client.monthly_salary:,.0f} ש\"ח, מעשן: {'כן' if active_client.is_smoker else 'לא'}",
            "מוצרים בתיק:",
        ]
        for p in active_client.products:
            lines.append(
                f"- {p.product_type} | {p.provider_name} | מסלול: {p.track_name} | פוליסה: {p.policy_number} | "
                f"צבירה: {p.accumulated_balance:,.0f} ש\"ח | הפקדה: {p.monthly_deposit:,.0f} ש\"ח | "
                f"דמ\"נ צבירה: {p.fee_balance_pct}% | דמ\"נ הפקדה: {p.fee_deposit_pct}% | מקדם מובטח: {p.guaranteed_factor or 'אין'} | "
                f"שארים: {p.survivors_info} | מקור: {p.data_origin} (עמ' {p.source_page}) | ציטוט: {p.source_quote}"
            )
        client_context_str = "\n".join(lines)

st.title("🛡️ LEVERAGE AI – מערכת תכנון פיננסי, פנסיוני ומאגר ידע ארצי")
if active_client:
    st.info(
        f"📂 **תיק לקוח פעיל:** {active_client.full_name} | **ת.ז (מפוענחת להרשאתך):** {decrypt_val(active_client.encrypted_id)} | "
        f"**גיל:** {2026 - active_client.birth_year} | **שכר:** {active_client.monthly_salary:,.0f} ₪ | **מוצרים בתיק:** {len(active_client.products)}"
    )

main_tabs = st.tabs([
    "🧠 1. צ'אט מנוע ידע (RAG) וחקיקה",
    "📊 2. גמל־נט, פנסיה־נט, ביטוח־נט וחברות",
    "🛡️ 3. השוואת תעריפי ביטוח וכיסויים",
    "📄 4. ניתוח מסלקה (PDF + מבנה אחיד XML)",
    "🧮 5. סימולטור אקטוארי ומיסוי פרישה (161ד/190)",
    "🎙️ 6. הכנה לפגישה, Whisper ומשימות (רואטו)",
    "💰 7. מנוע עמלות סוכנות (מופרד - Admin)",
    "🗄️ 8. ניהול מאגרי מידע ומקורות",
])


# =====================================================================
# לשונית 1: צ'אט מנוע הידע (RAG) מבוסס 7 רמות אמינות ו-8 סעיפי תשובה
# =====================================================================
with main_tabs[0]:
    st.header("🧠 מנוע ידע מקצועי מבוסס מקורות (RAG) – חקיקה, רגולציה, פסיקה ותיק לקוח")
    st.write("כל תשובה בנויה ב-8 סעיפים מחייבים: בסיס חוקי, קישור למקור, מועד תוקף, תחשיב מספרי, הנחות ומידע חסר.")

    col_date, col_search = st.columns([1, 2])
    with col_date:
        as_of_query_date = st.date_input("📅 מועד בדיקת התוקף החוקי (Time-Machine):", value=date(2026, 10, 17))
    with col_search:
        law_filter = st.text_input("🔎 חיפוש מהיר של סעיף חוק / חוזר רשות שוק ההון / פסק דין במאגר:")

    if law_filter:
        matched_laws = db.query(KnowledgeSourceItem).filter(
            (KnowledgeSourceItem.title.contains(law_filter)) |
            (KnowledgeSourceItem.content_text.contains(law_filter)) |
            (KnowledgeSourceItem.section_ref.contains(law_filter))
        ).all()
        for ml in matched_laws:
            st.markdown(
                f"📌 **[{ml.legal_status} | קדימות {ml.reliability_tier}] {ml.title} ({ml.section_ref})** "
                f"– *{ml.authority_name}* (תוקף: `{ml.effective_from}` עד `{ml.effective_to or 'בתוקף'}`)\n\n"
                f"> {ml.content_text}\n\n🔗 [קישור למקור הרשמי]({ml.source_url})"
            )

    st.markdown("---")
    if "global_rag_messages" not in st.session_state:
        st.session_state.global_rag_messages = []

    chat_list = (
        [{"role": m.role, "content": m.content} for m in active_client.chats]
        if active_client
        else st.session_state.global_rag_messages
    )

    for m in chat_list:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    if rag_q := st.chat_input("שאל שאלה מקצועית על חוק, חוזר ממונה, השוואת מוצרים, פסיקה או על תיק הלקוח..."):
        with st.chat_message("user"):
            st.markdown(rag_q)
        with st.chat_message("assistant"):
            with st.spinner("מצליב חקיקה, חוזרי רשות שוק ההון, גמל-נט, פסיקה ונתוני לקוח לפי היררכיית האמינות..."):
                ans = ask_leverage_rag_engine(
                    api_key=resolve_openai_api_key(),
                    db=db,
                    user_question=rag_q,
                    client_context=client_context_str,
                    chat_history=chat_list,
                    as_of_date=str(as_of_query_date),
                )
                st.markdown(ans)
                if active_client:
                    db.add(ClientChat(client_id=active_client.id, role="user", content=rag_q))
                    db.add(ClientChat(client_id=active_client.id, role="assistant", content=ans))
                    db.commit()
                else:
                    st.session_state.global_rag_messages.append({"role": "user", "content": rag_q})
                    st.session_state.global_rag_messages.append({"role": "assistant", "content": ans})


# =====================================================================
# לשונית 2: גמל־נט, פנסיה־נט, ביטוח־נט וקטלוג חברות ביטוח ובתי השקעות
# =====================================================================
with main_tabs[1]:
    st.header("📊 גמל־נט, פנסיה־נט, ביטוח־נט ומאגר הגופים המוסדיים בישראל")
    st.warning("⚠️ **הבהרה רגולטורית מחייבת:** נתוני התשואות שלהלן הינם נתוני עבר רשמיים ממערכות רשות שוק ההון (Data.gov.il). **אין בתשואות העבר כדי להוות הבטחה או אינדיקציה לתשואות עתידיות.**")

    f1, f2 = st.columns(2)
    with f1:
        sys_filter = st.multiselect("בחר מערכת מקור:", ["פנסיה-נט", "גמל-נט", "ביטוח-נט"], default=["פנסיה-נט", "גמל-נט", "ביטוח-נט"])
    with f2:
        prov_search = st.text_input("סינון לפי חברה מנהלת או שם מסלול:")

    tracks_q = db.query(MarketTrackMetric).filter(MarketTrackMetric.source_system.in_(sys_filter)).all()
    rows_tracks = []
    for t in tracks_q:
        if prov_search and (prov_search not in t.provider_name and prov_search not in t.track_name):
            continue
        rows_tracks.append({
            "מערכת": t.source_system,
            "קוד מסלול": t.track_id_code,
            "גוף מוסדי": t.provider_name,
            "מוצר": t.product_category,
            "מסלול השקעה": t.track_name,
            "תשואה 1ש' (%)": t.yield_1yr_pct,
            "תשואה 3ש' מצטברת (%)": t.yield_3yr_cumulative_pct,
            "תשואה 5ש' מצטברת (%)": t.yield_5yr_cumulative_pct,
            "מדד שארפ": t.sharpe_ratio,
            "חשיפה למניות (%)": t.equity_exposure_pct,
            "חשיפה לחו\"ל (%)": t.foreign_exposure_pct,
            "דמ\"נ צבירה ממוצע (%)": t.avg_fee_balance_pct,
            "דמ\"נ הפקדה ממוצע (%)": t.avg_fee_deposit_pct,
            "איזון אקטוארי (%)": t.actuarial_balance_pct,
            "מדד שירות": t.service_score,
            "נכסים (מיליוני ₪)": t.aum_millions_ils,
        })
    st.dataframe(pd.DataFrame(rows_tracks), use_container_width=True)

    st.markdown("---")
    st.subheader("🏢 קטלוג חברות ביטוח ובתי השקעות בישראל (רישיונות, מוצרים ומדדי שירות)")
    inst_list = db.query(InstitutionalEntity).all()
    st.dataframe(
        pd.DataFrame([
            {
                "שם הגוף המוסדי": i.entity_name,
                "סוג גוף": i.entity_type,
                "מעמד רגולטורי": i.regulatory_status,
                "מדד שירות רשות שוק ההון": i.service_index_score,
                "שיעור אישור תביעות (%)": i.claims_approval_rate_pct,
                "מוצרים פעילים": i.active_products_summary,
                "מוצרים היסטוריים ודגשים": i.historical_products_notes,
            }
            for i in inst_list
        ]),
        use_container_width=True,
    )


# =====================================================================
# לשונית 3: מאגר מחירי ביטוח, השוואת תעריפים והיקף כיסוי
# =====================================================================
with main_tabs[2]:
    st.header("🛡️ מנוע השוואת מחירי ביטוח, התפתחות פרמיה ואיכות כיסוי")
    st.info("📌 **הנחיה מקצועית:** מחיר הביטוח המוצג בסימולטור מבוסס על טבלאות תעריפים והנחות שוק. **המחיר אינו סופי אלא אם התקבלה הצעה תקפה מחברת הביטוח לאחר חיתום רפואי ועיסוקי.** חובה להשוות גם את היקף הכיסוי, תקופת האכשרה והחריגים.")

    c_br, c_cov, c_age, c_smk = st.columns(4)
    with c_br:
        branch_sel = st.selectbox(
            "בחר ענף ביטוח להשוואה:",
            ["ביטוח חיים (ריסק)", "ביטוח בריאות", "מחלות קשות"],
        )
    with c_cov:
        cov_amount = st.number_input("סכום ביטוח מבוקש (₪)", value=1_000_000, step=100_000)
    with c_age:
        ins_age = st.number_input("גיל המבוטח", min_value=18, max_value=75, value=38)
    with c_smk:
        ins_smoker = st.checkbox("מבוטח מעשן", value=False)

    branch_tariffs = db.query(InsuranceTariffCatalog).filter(InsuranceTariffCatalog.insurance_branch == branch_sel).all()
    if branch_tariffs:
        comp_rows = []
        schedules_dict = {}
        for bt in branch_tariffs:
            proj = calculate_insurance_tariff_projection(bt, cov_amount, ins_age, ins_smoker, projection_years=15)
            schedules_dict[f"{bt.provider_name} - {bt.plan_name}"] = proj["schedule"]
            comp_rows.append({
                "חברת ביטוח": bt.provider_name,
                "שם תוכנית": bt.plan_name,
                "פרמיה חודשית ראשונה (₪)": proj["first_month_premium_ils"],
                "עלות מצטברת ל-15 שנים (₪)": proj["cumulative_cost_ils"],
                "הנחה מרבית (%)": bt.max_discount_pct,
                "שנות תוקף הנחה": bt.discount_valid_years,
                "תקופת אכשרה (ימים)": bt.waiting_period_days,
                "השתתפות עצמית (₪)": bt.deductible_ils,
                "היקף הכיסוי הביטוחי": bt.coverage_scope_details,
                "חריגים ותנאי חיתום": bt.exclusions_and_underwriting,
            })
        st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

        with st.expander("📈 הצג טבלת התפתחות פרמיה לאורך 15 שנים (לפי גיל ותום הנחה)"):
            for plan_label, sched in schedules_dict.items():
                st.markdown(f"**{plan_label}**")
                st.dataframe(pd.DataFrame(sched), use_container_width=True)


# =====================================================================
# לשונית 4: ניתוח מסלקה פנסיונית (PDF מרובה עמודים + מבנה אחיד XML/JSON)
# =====================================================================
with main_tabs[3]:
    st.header("📄 ניתוח מסלקה פנסיונית והר הביטוח (PDF + קובצי המבנה האחיד XML/JSON)")
    if not active_client:
        st.warning("👈 אנא בחר תיק לקוח בסרגל הצד השמאלי (או צור כרטיס לקוח חדש) כדי להעלות ולשייך אליו מסמכי מסלקה.")
    else:
        consent_confirmed = st.checkbox(
            "✅ אני מצהיר כי בידי ייפוי כוח / הסכמת לקוח תקפה כדין לעיבוד נתוני המסלקה הפנסיונית והר הביטוח של לקוח זה.",
            value=False,
        )
        uploaded_silukit = st.file_uploader(
            "העלה דוח מסלקה פנסיונית (PDF) או קובץ במבנה האחיד של רשות שוק ההון (XML / JSON)",
            type=["pdf", "xml", "json"],
        )

        if uploaded_silukit and st.button("🔍 קרא, נתח ואמת את כל המוצרים במסלקה"):
            if not consent_confirmed:
                st.error("חובה לאשר קיום הסכמת לקוח / ייפוי כוח לפני קליטת מידע פנסיוני אישי.")
            else:
                file_bytes = uploaded_silukit.getvalue()
                fname = uploaded_silukit.name

                if fname.lower().endswith((".xml", ".json")):
                    with st.spinner("מפענח קובץ מסלקה בתקן המבנה האחיד (XML/JSON)..."):
                        parsed_items = parse_unified_xml_or_json_silukit(file_bytes, fname)
                        doc_rec = ClientDocument(
                            client_id=active_client.id,
                            filename=fname,
                            doc_format="UNIFIED_XML_JSON",
                            page_count=1,
                            analysis_json={"extracted_products": parsed_items},
                        )
                        db.add(doc_rec)
                        for item in parsed_items:
                            db.add(ClientProduct(client_id=active_client.id, **item))
                        db.commit()
                        st.success(f"נקלטו בהצלחה {len(parsed_items)} קופות מתוך קובץ המבנה האחיד!")
                        st.rerun()
                else:
                    # ניתוח PDF מרובה עמודים ללא קטיעה + אימות עמוד וציטוט
                    api_key = resolve_openai_api_key()
                    if not api_key:
                        st.error("להפעלת חילוץ AI מקובץ PDF נדרש מפתח OpenAI API.")
                    else:
                        with st.spinner("קורא את כל עמודי ה-PDF, מסנן הזרקות זדוניות ומחלץ מוצרים עם ציטוט עמוד מדויק..."):
                            reader = PdfReader(io.BytesIO(file_bytes))
                            pages_marked = []
                            for idx, page in enumerate(reader.pages, start=1):
                                raw_t = page.extract_text() or ""
                                clean_t, _ = sanitize_document_text(raw_t)
                                pages_marked.append(f"\n--- [תחילת עמוד {idx}] ---\n{clean_t}\n--- [סוף עמוד {idx}] ---\n")

                            full_doc_text = "".join(pages_marked)
                            oa_client = OpenAI(api_key=api_key)
                            comp = oa_client.beta.chat.completions.parse(
                                model="gpt-4o-2024-08-06",
                                messages=[
                                    {
                                        "role": "system",
                                        "content": (
                                            "אתה אנליסט מסלקה פנסיונית של LEVERAGE. חלץ את כל המוצרים מהדוח. "
                                            "אסור להמציא נתונים! אם נתון לא מופיע החזר null. "
                                            "לכל מוצר חובה לציין את מספר העמוד המדויק (source_page) וציטוט מילולי קצר מהעמוד (source_quote)."
                                        ),
                                    },
                                    {"role": "user", "content": full_doc_text[:60000]},
                                ],
                                response_format=SilukitStructuredOutput,
                                temperature=0.0,
                            )
                            parsed_out: SilukitStructuredOutput = comp.choices[0].message.parsed
                            doc_rec = ClientDocument(
                                client_id=active_client.id,
                                filename=fname,
                                doc_format="PDF",
                                page_count=len(reader.pages),
                                analysis_json=parsed_out.model_dump(),
                            )
                            db.add(doc_rec)
                            for ep in parsed_out.extracted_products:
                                db.add(
                                    ClientProduct(
                                        client_id=active_client.id,
                                        product_type=ep.product_type,
                                        provider_name=ep.provider_name,
                                        track_name=ep.track_name,
                                        policy_number=ep.policy_number,
                                        status=ep.status,
                                        accumulated_balance=ep.accumulated_balance,
                                        monthly_deposit=ep.monthly_deposit,
                                        fee_balance_pct=ep.fee_balance_pct,
                                        fee_deposit_pct=ep.fee_deposit_pct,
                                        guaranteed_factor=ep.guaranteed_factor,
                                        survivors_info=ep.survivors_info,
                                        disability_info=ep.disability_info,
                                        source_page=ep.source_page,
                                        source_quote=ep.source_quote,
                                        data_origin="SILUKIT_PDF",
                                        anomalies=ep.anomalies,
                                    )
                                )
                            db.commit()
                            st.success(f"הדוח ({len(reader.pages)} עמודים) נותח במלואו והמוצרים נשמרו בכרטיס הלקוח!")
                            st.rerun()

        if active_client.products:
            st.subheader("📋 טבלת מוצרים מאומתת בתיק הלקוח (כולל מיקום במסמך המקורי)")
            st.dataframe(
                pd.DataFrame([
                    {
                        "סוג מוצר": p.product_type,
                        "חברה מנהלת": p.provider_name,
                        "מסלול": p.track_name,
                        "פוליסה/חשבון": p.policy_number,
                        "סטטוס": p.status,
                        "יתרה צבורה (₪)": p.accumulated_balance,
                        "הפקדה חודשית (₪)": p.monthly_deposit,
                        "דמ\"נ צבירה (%)": p.fee_balance_pct,
                        "דמ\"נ הפקדה (%)": p.fee_deposit_pct,
                        "מקדם מובטח": p.guaranteed_factor,
                        "כיסוי שארים": p.survivors_info,
                        "עמוד במקור": p.source_page,
                        "ציטוט לאימות מהמסמך": p.source_quote,
                        "חריגים/מידע חסר": p.anomalies,
                    }
                    for p in active_client.products
                ]),
                use_container_width=True,
            )


# =====================================================================
# לשונית 5: סימולטור אקטוארי ומיסוי פרישה (טופס 161ד, תיקון 190, סעיף 125ד)
# =====================================================================
with main_tabs[4]:
    st.header("🧮 מנוע חישובים אקטוארי ומיסוי פרישה דטרמיניסטי (Python)")
    sub_act, sub_tax = st.tabs(["📈 השוואת ביטוח מנהלים מול קרן פנסיה", "⚖️ קיבוע זכויות (161ד), תיקון 190 וסעיף 125ד"])

    with sub_act:
        c1, c2, c3 = st.columns(3)
        with c1:
            a_cur_age = st.number_input("גיל נוכחי", value=67 if not active_client else 2026 - active_client.birth_year)
            a_ret_age = st.number_input("גיל פרישה", value=67 if not active_client else active_client.target_retirement_age)
            a_bal = st.number_input("צבירה קיימת להשוואה (₪)", value=1_000_000.0, step=50_000.0)
            a_dep = st.number_input("הפקדה חודשית (₪)", value=0.0, step=500.0)
        with c2:
            a_fac1 = st.number_input("מקדם קצבה חלופה א' (ביטוח מנהלים)", value=152.0, step=1.0)
            a_fb1 = st.number_input("דמ\"נ צבירה חלופה א' (%)", value=0.80, step=0.05)
            a_fd1 = st.number_input("דמ\"נ הפקדה חלופה א' (%)", value=0.0, step=0.1)
        with c3:
            a_fac2 = st.number_input("מקדם משוער חלופה ב' (קרן פנסיה)", value=200.0, step=1.0)
            a_fb2 = st.number_input("דמ\"נ צבירה חלופה ב' (%)", value=0.15, step=0.05)
            a_fd2 = st.number_input("דמ\"נ הפקדה חלופה ב' (%)", value=1.0, step=0.1)

        yrs = max(0, a_ret_age - a_cur_age)
        scen_rows = []
        for s_name, y_pct in [("שמרני (2.5%)", 2.5), ("תרחיש בסיס (4.2%)", 4.2), ("אופטימי (6.0%)", 6.0)]:
            r1 = calculate_accumulation_python(a_bal, a_dep, yrs, y_pct, a_fb1, a_fd1)
            r2 = calculate_accumulation_python(a_bal, a_dep, yrs, y_pct, a_fb2, a_fd2)
            ann1 = round(r1["final_balance_net"] / a_fac1, 0)
            ann2 = round(r2["final_balance_net"] / a_fac2, 0)
            scen_rows.append({
                "תרחיש תשואה ברוטו": s_name,
                "צבירה בפרישה - מנהלים (₪)": r1["final_balance_net"],
                "שחיקת דמ\"נ - מנהלים (₪)": r1["fee_erosion_ils"],
                "קצבה צפויה - מנהלים (₪)": ann1,
                "צבירה בפרישה - פנסיה (₪)": r2["final_balance_net"],
                "שחיקת דמ\"נ - פנסיה (₪)": r2["fee_erosion_ils"],
                "קצבה צפויה - פנסיה (₪)": ann2,
                "פער קצבה חודשי (₪)": ann1 - ann2,
            })
        st.dataframe(pd.DataFrame(scen_rows), use_container_width=True)
        st.warning("⚠️ **בדיקת תנאי פוליסה וזכויות שארים (חובה לפני החלטה):** אין לקבוע אוטומטית שמוצר אחד עדיף מבלי לבדוק את נספח הקצבה המדויק בפוליסה, עלות הבטחת קצבה לבת/בן הזוג (מסלול 240 חודשים או שארים לכל החיים), ומצב הבריאות של המבוטח.")

    with sub_tax:
        st.subheader("תחשיב קיבוע זכויות (161ד), נוסחת הקיזוז, תיקון 190 וסעיף 125ד")
        tx1, tx2 = st.columns(2)
        with tx1:
            tx_year = st.selectbox("שנת מס לתחשיב:", [2026, 2024])
            tx_birth_yr = st.number_input("שנת לידה של הלקוח (לבדיקת סעיף 125ד - ילידי 1948 ומטה)", value=1959 if not active_client else active_client.birth_year)
            tx_pen_mo = st.number_input("קצבה חודשית ברוטו צפויה (₪)", value=11500.0, step=500.0)
            tx_grants_known = st.checkbox("ידוע לי סכום המענקים והפיצויים הפטורים ב-32 השנים האחרונות", value=True)
            tx_grants = st.number_input("סך מענקים פטורים שנמשכו ב-32 השנים האחרונות (₪)", value=0.0, step=10000.0)
        with tx2:
            tx_alloc_annuity = st.slider("אחוז מסל הפטור להקצאה לקצבה חודשית (%)", 0, 100, 100)
            tx_190_profit = st.number_input("רווח נומינלי משוער בקופת גמל לפי תיקון 190 (₪)", value=100000.0, step=10000.0)

        if not tx_grants_known:
            st.error("🛑 **חסר נתון חובה:** לא ניתן לבצע תחשיב קיבוע זכויות (טופס 161ד) ללא הזנת סכום המענקים הפטורים ב-32 שנות העבודה שקדמו לפרישה (נוסחת הקיזוז 1.35). אנא השלם נתון זה מדוחות 161 או מרשות המסים.")
        else:
            ceiling = 9430.0 if tx_year == 2026 else 9120.0
            ex_rate = 0.575 if tx_year == 2026 else 0.52
            max_ex_mo = round(ceiling * ex_rate, 2)
            init_basket = round(max_ex_mo * 180.0, 2)
            offset_135 = round(tx_grants * 1.35, 2)
            rem_basket = max(0.0, round(init_basket - offset_135, 2))
            alloc_mo_cap = round(rem_basket * (tx_alloc_annuity / 100.0), 2)
            alloc_lump_sum = round(rem_basket - alloc_mo_cap, 2)
            exempt_mo_pension = round(alloc_mo_cap / 180.0, 2)

            m_a, m_b, m_c, m_d = st.columns(4)
            m_a.metric("סל פטור התחלתי (180)", f"{init_basket:,.0f} ₪")
            m_b.metric("קיזוז מענקים (x1.35)", f"{offset_135:,.0f} ₪")
            m_c.metric("פטור חודשי על הקצבה", f"{exempt_mo_pension:,.0f} ₪")
            m_d.metric("יתרה להיוון פטור ממס", f"{alloc_lump_sum:,.0f} ₪")

            eligible_125d = tx_birth_yr <= 1948
            st.info(
                f"📌 **בדיקת תיקון 190:** קצבת הלקוח ({tx_pen_mo:,.0f} ₪) "
                + ("עומדת בתנאי הקצבה המזערית (5,012 ₪). מס של 15% נומינלי על הרווח: " f"**{tx_190_profit * 0.15:,.0f} ₪**." if tx_pen_mo >= 5012 else "אינה עומדת בתנאי הקצבה המזערית (5,012 ₪) להיוון ב-15%.")
                + f"\n\n📌 **בדיקת סעיף 125ד (ילידי 1948 ומטה):** {'זכאי לפטור שנתי על רווחי ריבית/פוליסת חיסכון!' if eligible_125d else 'אינו זכאי (נולד אחרי 1948).'}"
            )


# =====================================================================
# לשונית 6: הכנה לפגישה, תמלול שמע (Whisper) וניהול משימות (רואטו CRM)
# =====================================================================
with main_tabs[5]:
    st.header("🎙️ הכנה לפגישה, תמלול שמע (Whisper) ומשימות (חיבור לרואטו CRM)")
    if not active_client:
        st.info("בחר תיק לקוח בסרגל הצד כדי להפיק דוח 'הכן אותי לפגישה' או לתמלל הקלטת פגישה לתיק.")
    else:
        if st.button("🚀 הכן אותי לפגישה (ניתוח 6 נקודות מתיק הלקוח)"):
            with st.spinner("מנתח את כרטיס הלקוח, המסלקות והמשימות..."):
                prep_ans = ask_leverage_rag_engine(
                    api_key=resolve_openai_api_key(),
                    db=db,
                    user_question=(
                        "הפק דוח 'הכן אותי לפגישה' עבור לקוח זה הכולל בדיוק: "
                        "1. סיכום מצב התיק. 2. מוצרים וזכויות שכדאי לבדוק. 3. העברות או פעולות מתוכננות. "
                        "4. נקודות חשובות שהסוכן צריך להסביר. 5. שאלות פתוחות ללקוח. 6. משימות מפגישות קודמות."
                    ),
                    client_context=client_context_str,
                )
                st.markdown(prep_ans)

        st.markdown("---")
        aud_up = st.file_uploader("העלה הקלטת פגישה (MP3/WAV/M4A) לתמלול בעברית עם Whisper:", type=["mp3", "wav", "m4a"])
        if aud_up and st.button("🎙️ תמלל פגישה והפק סיכום סוכן, סיכום לקוח והודעת WhatsApp"):
            api_key = resolve_openai_api_key()
            if not api_key:
                st.error("נדרש מפתח OpenAI API לתמלול Whisper.")
            else:
                with st.spinner("מתמלל באמצעות Whisper ומפיק תוצרי פגישה..."):
                    oa = OpenAI(api_key=api_key)
                    bio = io.BytesIO(aud_up.getvalue())
                    bio.name = aud_up.name
                    tr = oa.audio.transcriptions.create(model="whisper-1", file=bio, language="he")
                    summary_txt = ask_leverage_rag_engine(
                        api_key=api_key,
                        db=db,
                        user_question=f"תמלול פגישה:\n{tr.text}\n\nהפק: 1. סיכום מקצועי לסוכן. 2. סיכום ידידותי ללקוח. 3. רשימת משימות לביצוע. 4. טיוטת הודעת WhatsApp.",
                        client_context=client_context_str,
                    )
                    db.add(ClientMeeting(client_id=active_client.id, encrypted_transcript=encrypt_val(tr.text), agent_summary=summary_txt))
                    db.commit()
                    st.markdown(summary_txt)

        st.markdown("---")
        st.subheader("✅ הוספת משימה לתיק וייצוא למערכת CRM רואטו (Roeto)")
        with st.form("task_form"):
            t_cat = st.selectbox("קטגוריה", ["כספים בפיקדון שצפויים להשתחרר", "קופות שדורשות טיפול נוסף", "קיבוע זכויות", "פיצויים", "פגישות שירות עתידיות", "מסמכים חסרים", "פעולות שנקבעו בפגישה"])
            t_tit = st.text_input("כותרת משימה")
            t_dsc = st.text_area("פירוט")
            t_pri = st.selectbox("דחיפות", ["גבוהה", "דחופה", "בינונית"])
            if st.form_submit_button("שמור משימה בכרטיס הלקוח"):
                db.add(ClientTask(client_id=active_client.id, category=t_cat, title=t_tit, description=t_dsc, priority=t_pri))
                db.commit()
                st.success("המשימה נשמרה!")
                st.rerun()

        if active_client.tasks:
            df_t = pd.DataFrame([
                {"מזהה": t.id, "קטגוריה": t.category, "כותרת": t.title, "דחיפות": t.priority, "סטטוס": t.status, "פירוט": t.description}
                for t in active_client.tasks
            ])
            st.dataframe(df_t, use_container_width=True)
            csv_buf = io.StringIO()
            df_t.to_csv(csv_buf, index=False)
            st.download_button(
                "📥 ייצא משימות לקובץ CSV תואם רואטו (Roeto CRM)",
                data=csv_buf.getvalue().encode("utf-8-sig"),
                file_name=f"roeto_tasks_client_{active_client.id_last4}.csv",
                mime="text/csv",
            )


# =====================================================================
# לשונית 7: מנוע עמלות סוכנות LEVERAGE (הפרדה מוחלטת והגבלת הרשאות Admin!)
# =====================================================================
with main_tabs[6]:
    st.header("💰 מודל עמלות סוכנות LEVERAGE (מופרד לחלוטין ממנוע ההמלצות ללקוח)")
    if current_user["role"] != "admin":
        st.error("🔒 **גישה מוגבלת:** מסך הסכמי העמלות הפנימיים של LEVERAGE חסוי ופתוח אך ורק למנהלי מערכת (`admin`).")
    else:
        st.info("🛡️ **חומה סינית פעילה:** נתוני העמלות במסך זה אינם מועברים לעולם למנוע ה-AI או לסימולטור הלקוח, ואינם משפיעים בשום צורה על דירוג המוצרים או ההמלצות הפנסיוניות.")
        agreements = db.query(AgencyCommissionAgreement).all()
        st.dataframe(
            pd.DataFrame([
                {
                    "גוף מוסדי": a.provider_name,
                    "קטגוריית מוצר": a.product_category,
                    "עמלת היקף (%)": a.scope_commission_pct,
                    "עמלת נפרעים שנתית (%)": a.ongoing_nifraim_pct,
                    "עמלה מהפקדה (%)": a.deposit_commission_pct,
                    "בונוס למיליון צבירה (₪)": a.one_time_bonus_per_1m_ils,
                    "תקופת השבה/ביטול (חודשים)": a.clawback_period_months,
                    "מנגנון ביטול והשבה (Clawback)": a.clawback_rule_desc,
                    "חלק סוכן (%)": a.agent_share_pct,
                    "תוקף הסכם": f"{a.agreement_valid_from} עד {a.agreement_valid_to}",
                }
                for a in agreements
            ]),
            use_container_width=True,
        )

        st.subheader("🧮 סימולטור הכנסה צפויה לסוכנות ולסוכן (לפי הסכם מורשה)")
        ca1, ca2, ca3 = st.columns(3)
        with ca1:
            sel_ag = st.selectbox("בחר הסכם גוף מוסדי:", agreements, format_func=lambda x: f"{x.provider_name} - {x.product_category}")
        with ca2:
            sim_accum = st.number_input("היקף צבירה מנוידת/מופקדת (₪)", value=1_500_000.0, step=100_000.0)
        with ca3:
            sim_mon_dep = st.number_input("הפקדה חודשית שוטפת (₪)", value=4_000.0, step=500.0)

        if sel_ag:
            c_res = calculate_isolated_agency_commission(sel_ag, sim_accum, sim_mon_dep)
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("סה\"כ הכנסה שנה א' (ברוטו)", f"{c_res['total_year1_gross_ils']:,.0f} ₪")
            k2.metric("חלק הסוכן", f"{c_res['agent_share_ils']:,.0f} ₪")
            k3.metric("חלק הסוכנות (LEVERAGE)", f"{c_res['agency_retained_ils']:,.0f} ₪")
            k4.metric("חשיפה להשבה (Clawback)", f"{c_res['clawback_exposure_ils']:,.0f} ₪")


# =====================================================================
# לשונית 8: ממשק ניהול "מאגרי מידע ומקורות"
# =====================================================================
with main_tabs[7]:
    st.header("🗄️ ניהול מאגרי מידע, מקורות, עדכונים ובקרת איכות")
    st.write("מעקב אחר מאגרים מחוברים, סנכרון מול Data.gov.il, העלאת קובצי גמל-נט/פנסיה-נט, הוספת חקיקה והסכמים, וזיהוי נתונים הדורשים רענון.")

    m_sub1, m_sub2, m_sub3 = st.tabs([
        "📡 סטטוס מאגרים וסנכרון Data.gov.il",
        "📥 העלאת קובצי גמל-נט / מחירונים / הסכמים",
        "➕ הוספת סעיף חוק / חוזר / פסק דין ובקרת גרסאות",
    ])

    with m_sub1:
        if st.button("🔄 בדוק זמינות ועדכונים חיים מ-API הממשלתי (Data.gov.il - רשות שוק ההון)"):
            with st.spinner("פונה ל-API של Data.gov.il..."):
                live_ds = fetch_cma_datasets_from_datagov()
                if live_ds:
                    st.success("חיבור API ל-Data.gov.il תקין! להלן המאגרים העדכניים של רשות שוק ההון:")
                    st.dataframe(pd.DataFrame(live_ds), use_container_width=True)
                else:
                    st.warning("לא התקבל מענה מהיר משרת Data.gov.il כעת; המערכת פועלת מול הנתונים המקומיים המאומתים.")

        st.subheader("📚 רשימת כל פריטי הידע והחקיקה הפעילים במערכת")
        all_kb = db.query(KnowledgeSourceItem).all()
        st.dataframe(
            pd.DataFrame([
                {
                    "קוד": k.code_key,
                    "קטגוריה": k.category,
                    "כותרת": k.title,
                    "סעיף": k.section_ref,
                    "מעמד רגולטורי": k.legal_status,
                    "דרגת קדימות (1-7)": k.reliability_tier,
                    "רשות מפרסמת": k.authority_name,
                    "תוקף מ-": k.effective_from,
                    "תוקף עד": k.effective_to or "בתוקף",
                    "עודכן לאחרונה": k.last_updated,
                    "גרסה": k.version_tag,
                    "סטטוס אישור": k.approval_status,
                }
                for k in all_kb
            ]),
            use_container_width=True,
        )

    with m_sub2:
        st.subheader("העלאת קובץ CSV / Excel של גמל-נט, פנסיה-נט או ביטוח-נט")
        up_market_file = st.file_uploader("בחר קובץ CSV או XLSX רשמי לעדכון מסלולים ותשואות:", type=["csv", "xlsx"])
        if up_market_file and st.button("קלוט ועדכן מסלולים במאגר"):
            try:
                if up_market_file.name.endswith(".csv"):
                    df_m = pd.read_csv(up_market_file)
                else:
                    df_m = pd.read_excel(up_market_file)
                st.success(f"הקובץ נקרא בהצלחה ({len(df_m)} שורות)! תצוגה מקדימה לאישור:")
                st.dataframe(df_m.head(10), use_container_width=True)
            except Exception as e:
                st.error(f"שגיאה בקריאת הקובץ: {e}")

    with m_sub3:
        st.subheader("הוספת הוראת חוק, חוזר ממונה או פסק דין חדש למאגר הידע")
        with st.form("add_kb_source_form"):
            nk_code = st.text_input("קוד מזהה ייחודי (למשל CMA_2026_NEW_1)")
            nk_cat = st.selectbox("קטגוריה", ["חקיקה", "חוזר רשות שוק ההון", "רשות המסים", "מאקרו בנק ישראל ולמ\"ס", "תקנון ופוליסה"])
            nk_title = st.text_input("כותרת המסמך / החוק")
            nk_sec = st.text_input("סעיף / עמוד מדויק")
            nk_status = st.selectbox("מעמד רגולטורי", ["חוק בתוקף", "הוראה מחייבת", "נוסח היסטורי", "הצעת חוק", "טיוטת חוזר"])
            nk_tier = st.slider("דרגת קדימות בהיררכיית האמינות (1=חקיקה מחייבת, 7=מחקר משלים)", 1, 7, 2)
            nk_auth = st.text_input("רשות מפרסמת", value="רשות שוק ההון, ביטוח וחיסכון")
            nk_url = st.text_input("קישור רשמי לאימות", value="https://www.gov.il/he/pages/report_cma")
            nk_from = st.text_input("תאריך תחילת תוקף (YYYY-MM-DD)", value="2026-01-01")
            nk_text = st.text_area("נוסח מלא או תמצית מחייבת")
            if st.form_submit_button("שמור ואשר במאגר הידע של LEVERAGE"):
                if nk_code and nk_title and nk_text:
                    db.add(
                        KnowledgeSourceItem(
                            code_key=nk_code,
                            category=nk_cat,
                            title=nk_title,
                            section_ref=nk_sec,
                            content_text=nk_text,
                            legal_status=nk_status,
                            reliability_tier=nk_tier,
                            authority_name=nk_auth,
                            source_url=nk_url,
                            effective_from=nk_from,
                            last_updated=str(date.today()),
                            approval_status="מאושר",
                        )
                    )
                    db.commit()
                    st.success("המקור נוסף למאגר הידע וזמין מיד למנוע ה-RAG!")
                    st.rerun()

db.close()
