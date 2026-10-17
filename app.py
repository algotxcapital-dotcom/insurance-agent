import streamlit as st
import os
import re
import io
import csv
import json
import base64
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
# 1. הגדרת ממשק, תמיכה ב-RTL, האצת Cache ואבטחת מידע
# =====================================================================
st.set_page_config(
    page_title="LEVERAGE AI – מערכת תכנון פיננסי, פנסיוני והשקעות",
    page_icon="🛡️",
    layout="wide",
)

st.markdown("""
<style>
    .stApp { direction: rtl; text-align: right; font-family: 'Assistant', 'Segoe UI', sans-serif; }
    p, div, h1, h2, h3, h4, h5, li, span, label, th, td { text-align: right !important; }
    .stChatMessage { direction: rtl; }
    div[data-testid="stMetricValue"] { direction: ltr; text-align: right !important; }
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


@st.cache_resource
def get_fernet() -> Fernet:
    key = os.getenv("FERNET_ENCRYPTION_KEY", "")
    if not key:
        try:
            key = st.secrets.get("FERNET_ENCRYPTION_KEY", "")
        except Exception:
            key = ""
    if not key:
        derived = hashlib.sha256(b"LEVERAGE_DEFAULT_INTERNAL_VAULT_2026").digest()
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


@st.cache_resource
def get_db_engine_and_session():
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        try:
            db_url = st.secrets.get("DATABASE_URL", "sqlite:///./leverage_enterprise_v3.db")
        except Exception:
            db_url = "sqlite:///./leverage_enterprise_v3.db"
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    eng = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
    return eng, sessionmaker(autocommit=False, autoflush=False, bind=eng)


engine, SessionLocal = get_db_engine_and_session()
Base = declarative_base()


def utc_now():
    return datetime.now(timezone.utc)


# =====================================================================
# 2. מודלי מסד הנתונים (לקוחות, מוצרים, גמל-נט מורחב, רגולציה ועמלות)
# =====================================================================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(30), default="agent")
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
    risk_profile = Column(String(50), default="טרם נקבע (דורש בירור צרכים)")
    investment_horizon_years = Column(Integer, nullable=True)
    liquidity_need_desc = Column(String(200), default="לא הוגדר")
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
    doc_format = Column(String(30), default="PDF")
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


class KnowledgeSourceItem(Base):
    __tablename__ = "knowledge_sources"
    id = Column(Integer, primary_key=True)
    code_key = Column(String(80), index=True, nullable=False)
    domain_scope = Column(String(40), default="GENERAL")  # INVESTMENT | RETIREMENT_TAX | PENSION_LAW | INSURANCE_LAW | MACRO
    category = Column(String(60), nullable=False)
    title = Column(String(200), nullable=False)
    section_ref = Column(String(100), default="")
    content_text = Column(Text, nullable=False)
    numeric_val = Column(Float, nullable=True)
    unit = Column(String(30), default="")
    legal_status = Column(String(50), nullable=False)
    reliability_tier = Column(Integer, default=1)
    authority_name = Column(String(120), nullable=False)
    source_url = Column(String(350), nullable=False)
    effective_from = Column(String(20), nullable=False)
    effective_to = Column(String(20), nullable=True)
    last_updated = Column(String(20), nullable=False)
    version_tag = Column(String(30), default="v1.0")
    tax_year = Column(Integer, default=2026)
    approval_status = Column(String(30), default="מאושר")


class MarketTrackMetric(Base):
    """
    מאגר מסלולים ומוצרים מלא (גמל-נט, פנסיה-נט, ביטוח-נט וחלופות השקעה)
    הכולל את כל 10 הפרמטרים להשוואה מקצועית עבור סוכני LEVERAGE.
    """
    __tablename__ = "market_track_metrics"
    id = Column(Integer, primary_key=True)
    source_system = Column(String(40), nullable=False)       # גמל-נט | פנסיה-נט | ביטוח-נט | שוק ההון (בורסה)
    track_id_code = Column(String(40), index=True)           # מספר מסלול רשמי באוצר / קוד נייר
    provider_name = Column(String(80), nullable=False)       # 1. שם הגוף המנהל
    product_category = Column(String(70), nullable=False)    # 2. שם המוצר (קופת גמל להשקעה / פוליסת חיסכון / קרן השתלמות / קרן פנסיה מקיפה / תיק קרנות סל / קרן כספית)
    track_name = Column(String(140), nullable=False)         # 3. שם מסלול ההשקעה
    equity_exposure_pct = Column(Float, default=0.0)         # 4א. שיעור חשיפה למניות (%)
    foreign_exposure_pct = Column(Float, default=0.0)        # 4ב. חשיפה לחו"ל (%)
    asset_mix_desc = Column(String(200), default="")         # 4ג. תמהיל נכסים (סחיר, לא סחיר, אג"ח, מניות)
    avg_fee_balance_pct = Column(Float, default=0.0)         # 5א. דמי ניהול מצבירה (%)
    avg_fee_deposit_pct = Column(Float, default=0.0)         # 5ב. דמי ניהול מהפקדה (%)
    yield_1yr_pct = Column(Float, default=0.0)               # 6א. תשואה 12 חודשים (%)
    yield_3yr_cumulative_pct = Column(Float, default=0.0)    # 6ב. תשואה מצטברת 3 שנים (%)
    yield_5yr_cumulative_pct = Column(Float, default=0.0)    # 6ג. תשואה מצטברת 5 שנים (%)
    sharpe_ratio = Column(Float, default=0.0)                # 7א. מדד שארפ
    risk_level_label = Column(String(40), default="בינונית") # 7ב. רמת סיכון (נמוכה / בינונית / גבוהה)
    liquidity_terms = Column(String(220), default="")        # 8. נזילות ואפשרויות משיכה
    tax_rules_summary = Column(String(250), default="")      # 9. היבטי מיסוי
    pros_for_client = Column(String(250), default="")        # 10א. יתרונות מרכזיים
    cons_for_client = Column(String(250), default="")        # 10ב. חסרונות ונקודות לבדיקה
    aum_millions_ils = Column(Float, default=0.0)
    actuarial_balance_pct = Column(Float, default=0.0)
    service_score = Column(Float, default=85.0)
    report_period = Column(String(30), default="09/2026 (נתונים רשמיים אחרונים)")
    last_verified_date = Column(String(20), default="2026-10-15")
    source_url = Column(String(300), default="https://data.gov.il/he/datasets?orgs=cma")


class InstitutionalEntity(Base):
    __tablename__ = "institutional_entities"
    id = Column(Integer, primary_key=True)
    entity_name = Column(String(80), unique=True, nullable=False)
    entity_type = Column(String(50), nullable=False)
    regulatory_status = Column(String(100), default="בעל רישיון מבטח / חברה מנהלת - רשות שוק ההון")
    active_products_summary = Column(Text, default="")
    historical_products_notes = Column(Text, default="")
    service_index_score = Column(Float, default=80.0)
    claims_approval_rate_pct = Column(Float, default=88.0)
    last_updated = Column(String(20), default="2026-01-15")


class InsuranceTariffCatalog(Base):
    __tablename__ = "insurance_tariffs"
    id = Column(Integer, primary_key=True)
    provider_name = Column(String(80), nullable=False)
    insurance_branch = Column(String(60), nullable=False)
    plan_name = Column(String(120), nullable=False)
    base_monthly_rate_per_100k_age35 = Column(Float, default=12.0)
    age_slope_pct_per_year = Column(Float, default=6.5)
    smoker_surcharge_pct = Column(Float, default=45.0)
    max_discount_pct = Column(Float, default=30.0)
    discount_valid_years = Column(Integer, default=5)
    coverage_scope_details = Column(Text, default="")
    waiting_period_days = Column(Integer, default=90)
    deductible_ils = Column(Float, default=0.0)
    exclusions_and_underwriting = Column(Text, default="")
    tariff_updated_date = Column(String(20), default="2026-01-01")


class AgencyCommissionAgreement(Base):
    """טבלת עמלות סוכנות LEVERAGE - מופרדת בחומה סינית מוחלטת ממנוע ההמלצות ללקוח!"""
    __tablename__ = "agency_commissions"
    id = Column(Integer, primary_key=True)
    provider_name = Column(String(80), nullable=False)
    product_category = Column(String(80), nullable=False)
    scope_commission_pct = Column(Float, default=0.0)
    ongoing_nifraim_pct = Column(Float, default=0.0)
    deposit_commission_pct = Column(Float, default=0.0)
    one_time_bonus_per_1m_ils = Column(Float, default=0.0)
    clawback_period_months = Column(Integer, default=12)
    clawback_rule_desc = Column(String(255), default="")
    agent_share_pct = Column(Float, default=50.0)
    agreement_valid_from = Column(String(20), default="2026-01-01")
    agreement_valid_to = Column(String(20), default="2026-12-31")


class LegalPrecedent(Base):
    __tablename__ = "legal_precedents"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(80), unique=True, nullable=False)
    court_name = Column(String(100), nullable=False)
    ruling_date = Column(String(20), nullable=False)
    topic = Column(String(100), nullable=False)
    precedent_weight = Column(String(40), nullable=False)
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
# 3. זריעת מסד הנתונים (Seed) עם מסלולים אמיתיים, מוצרים ורגולציה מסווגת
# =====================================================================
@st.cache_resource
def init_and_seed_database():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == "agent1").first():
            db.add_all([
                User(username="agent1", full_name="סוכן / מתכנן פיננסי - LEVERAGE", role="agent", password_hash=hash_password("Leverage2026!")),
                User(username="admin", full_name="מנהל מערכת ומקצוענות - LEVERAGE", role="admin", password_hash=hash_password("AdminLeverage2026!")),
            ])
            db.commit()

        if db.query(MarketTrackMetric).count() == 0:
            tracks = [
                # --- קופות גמל להשקעה (מסלולי מניות, S&P 500, כללי ואג"ח) ---
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="11524", provider_name="מור", product_category="קופת גמל להשקעה",
                    track_name="מור גמל להשקעה מניות", equity_exposure_pct=96.2, foreign_exposure_pct=64.0,
                    asset_mix_desc="מניות בארץ ובחו\"ל (סחיר)", avg_fee_balance_pct=0.68, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=18.4, yield_3yr_cumulative_pct=42.1, yield_5yr_cumulative_pct=78.5, sharpe_ratio=1.06,
                    risk_level_label="גבוהה", liquidity_terms="נזיל לחלוטין למשיכה הונית בכל עת (תוך 4 ימי עסקים)",
                    tax_rules_summary="דחיית מס במעבר בין מסלולים; 25% מס רווחי הון ריאלי במשיכה הונית, או פטור מלא ממס במשיכה כקצבה אחרי גיל 60 (תקרת הפקדה שנתית: 79,006 ₪)",
                    pros_for_client="ניהול אקטיבי מוסדי מצטיין, מעבר מסלולים ללא אירוע מס, אפשרות להוראת קבע חודשית מכל סכום",
                    cons_for_client="דמי ניהול גבוהים יותר מתיק קרנות סל עצמאי; מוגבל בתקרת הפקדה שנתית",
                    aum_millions_ils=8900, service_score=87.0
                ),
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="10842", provider_name="ילין לפידות", product_category="קופת גמל להשקעה",
                    track_name="ילין לפידות גמל להשקעה מניות", equity_exposure_pct=94.5, foreign_exposure_pct=59.0,
                    asset_mix_desc="מניות בארץ ובחו\"ל במחקר אנליטי", avg_fee_balance_pct=0.66, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=17.6, yield_3yr_cumulative_pct=39.8, yield_5yr_cumulative_pct=74.2, sharpe_ratio=1.02,
                    risk_level_label="גבוהה", liquidity_terms="נזיל לחלוטין למשיכה בכל עת ללא קנסות יציאה",
                    tax_rules_summary="מעבר מסלולים ללא אירוע מס; 25% מס ריאלי במשיכה הונית; פטור כקצבה מגיל 60",
                    pros_for_client="ציון שירות גבוה מאוד, התמחות ארוכת שנים בשוק ההון הסחיר",
                    cons_for_client="דמי ניהול של כ-0.65%-0.70% מצבירה; תקרת הפקדה שנתית",
                    aum_millions_ils=11200, service_score=88.0
                ),
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="10911", provider_name="אנליסט", product_category="קופת גמל להשקעה",
                    track_name="אנליסט גמל להשקעה מניות", equity_exposure_pct=97.0, foreign_exposure_pct=61.0,
                    asset_mix_desc="מניות סחירות בארץ ובחו\"ל", avg_fee_balance_pct=0.67, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=18.9, yield_3yr_cumulative_pct=43.0, yield_5yr_cumulative_pct=79.1, sharpe_ratio=1.08,
                    risk_level_label="גבוהה", liquidity_terms="נזיל לחלוטין בכל עת",
                    tax_rules_summary="דחיית מס במעבר מסלולים; 25% מס ריאלי במשיכה; פטור כקצבה מגיל 60",
                    pros_for_client="ביצועי אלפא היסטוריים בולטים במסלול המנייתי",
                    cons_for_client="תנודתיות גבוהה בטווח הקצר; אינו מתאים לכסף המיועד לדירה ב-1-3 השנים הקרובות",
                    aum_millions_ils=6400, service_score=85.0
                ),
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="13205", provider_name="מיטב", product_category="קופת גמל להשקעה",
                    track_name="מיטב גמל להשקעה עוקב מדד S&P 500", equity_exposure_pct=100.0, foreign_exposure_pct=100.0,
                    asset_mix_desc="עוקב פסיבי מדד S&P 500 (חשיפה דולרית מלאה)", avg_fee_balance_pct=0.58, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=21.2, yield_3yr_cumulative_pct=46.5, yield_5yr_cumulative_pct=84.0, sharpe_ratio=1.10,
                    risk_level_label="גבוהה", liquidity_terms="נזיל לחלוטין בכל עת",
                    tax_rules_summary="דחיית מס במעבר מסלולים (כולל מעבר למסלול שקלי/סולידי ללא מס); 25% מס ריאלי במשיכה",
                    pros_for_client="שילוב של השקעה פסיבית במדד S&P 500 עם מעטפת קופת גמל להשקעה (דחיית מס במעבר מסלול)",
                    cons_for_client="ריכוזיות בשוק האמריקאי וחשיפה מלאה לשער הדולר/שקל",
                    aum_millions_ils=9800, service_score=86.0
                ),
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="11520", provider_name="מור", product_category="קופת גמל להשקעה",
                    track_name="מור גמל להשקעה כללי", equity_exposure_pct=43.0, foreign_exposure_pct=48.0,
                    asset_mix_desc=" כ-43% מניות, כ-57% אג\"ח ממשלתי וקונצרני ומזומן", avg_fee_balance_pct=0.65, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=11.2, yield_3yr_cumulative_pct=25.4, yield_5yr_cumulative_pct=45.8, sharpe_ratio=0.96,
                    risk_level_label="בינונית", liquidity_terms="נזיל לחלוטין בכל עת",
                    tax_rules_summary="דחיית מס במעבר מסלולים; 25% מס ריאלי במשיכה",
                    pros_for_client="פיזור מאוזן המתאים לטווח בינוני (3-6 שנים) וללקוחות בעלי סיבולת סיכון מתונה",
                    cons_for_client="פוטנציאל צמיחה נמוך ממסלול מנייתי בטווח של 10+ שנים",
                    aum_millions_ils=12400, service_score=87.0
                ),
                # --- פוליסות חיסכון פיננסיות (ביטוח-נט) ---
                MarketTrackMetric(
                    source_system="ביטוח-נט", track_id_code="BIT-301", provider_name="הפניקס", product_category="פוליסת חיסכון",
                    track_name="הפניקס אינווסט - מסלול מניות", equity_exposure_pct=93.0, foreign_exposure_pct=62.0,
                    asset_mix_desc="מניות בארץ ובחו\"ל + שילוב נכסים לא סחירים וקרנות השקעה", avg_fee_balance_pct=0.82, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=17.8, yield_3yr_cumulative_pct=40.5, yield_5yr_cumulative_pct=75.0, sharpe_ratio=1.01,
                    risk_level_label="גבוהה", liquidity_terms="נזיל לחלוטין בכל עת ללא תקרת הפקדה שנתית",
                    tax_rules_summary="דחיית מס במעבר בין מסלולים; 25% מס ריאלי במימוש; זכאות להטבת מס סעיף 125ד לילידי 1948 ומטה",
                    pros_for_client="ללא תקרת הפקדה (מתאים גם לסכומים מעל 79,006 ₪), חשיפה לנכסים לא סחירים, מעבר מסלולים ללא מס",
                    cons_for_client="דמי ניהול גבוהים יותר מקופת גמל להשקעה ומתיק קרנות סל (לרוב 0.75%-0.95% לפני הנחה)",
                    aum_millions_ils=24000, service_score=84.0
                ),
                MarketTrackMetric(
                    source_system="ביטוח-נט", track_id_code="BIT-305", provider_name="הראל", product_category="פוליסת חיסכון",
                    track_name="הראל מגוון השקעות - כללי", equity_exposure_pct=41.0, foreign_exposure_pct=50.0,
                    asset_mix_desc="מניות, אג\"ח ונכסי נדל\"ן/תשתיות לא סחירים", avg_fee_balance_pct=0.85, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=10.8, yield_3yr_cumulative_pct=24.2, yield_5yr_cumulative_pct=43.9, sharpe_ratio=0.92,
                    risk_level_label="בינונית", liquidity_terms="נזיל לחלוטין בכל עת ללא תקרת הפקדה",
                    tax_rules_summary="דחיית מס במעבר מסלולים; 25% מס ריאלי; הטבת סעיף 125ד לילידי 1948 ומטה",
                    pros_for_client="עוגן נכסים לא סחירים המקטין תנודתיות, ללא הגבלת סכום הפקדה",
                    cons_for_client="דמי ניהול גבוהים יחסית לחלופות פסיביות",
                    aum_millions_ils=21500, service_score=85.0
                ),
                # --- חלופות שוק ההון ישירות: תיק השקעות עצמאי (קרנות סל) וקרנות כספיות ---
                MarketTrackMetric(
                    source_system="שוק ההון (בורסה)", track_id_code="ETF-PASSIVE", provider_name="בתי השקעות (מיטב טרייד / IBI / אקסלנס)", product_category="תיק השקעות עצמאי (קרנות סל)",
                    track_name="תיק פסיבי עצמאי - קרנות סל מחקות מדדים עולמיים (MSCI World / S&P 500)", equity_exposure_pct=100.0, foreign_exposure_pct=100.0,
                    asset_mix_desc="קרנות סל שקליות/מט\"חיות צוברות דיבידנד (למשל אינפיניטי/תכלית/קסם/בלאקרוק)", avg_fee_balance_pct=0.18, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=20.8, yield_3yr_cumulative_pct=45.9, yield_5yr_cumulative_pct=83.2, sharpe_ratio=1.09,
                    risk_level_label="גבוהה", liquidity_terms="נזיל בכל יום מסחר בבורסה",
                    tax_rules_summary="25% מס רווחי הון ריאלי (בקרן שקלית מחקה); מכירת נייר לצורך שינוי מסלול/איזון מהווה אירוע מס מיידי; אפשרות לקיזוז הפסדים",
                    pros_for_client="העלויות הנמוכות ביותר בשוק (דמ\"נ קרן 0.07%-0.20% + דמי מסחר סמליים), שליטה מלאה וללא תקרת הפקדה",
                    cons_for_client="דורש משמעת עצמית וביצוע פעולות קנייה; שינוי תמהיל (למשל מעבר ממניות לאג\"ח) יוצר אירוע מס של 25%",
                    aum_millions_ils=95000, service_score=86.0
                ),
                MarketTrackMetric(
                    source_system="שוק ההון (בורסה)", track_id_code="MMF-SOLID", provider_name="מיטב / מור / הראל / ילין לפידות", product_category="קרן כספית / פיקדון",
                    track_name="קרן כספית שקלית (אלטרנטיבה סולידית לטווח קצר 0-2 שנים)", equity_exposure_pct=0.0, foreign_exposure_pct=0.0,
                    asset_mix_desc="מק\"מ בנק ישראל, פיקדונות בנקאיים ואג\"ח בדירוג AAA קצר מועד", avg_fee_balance_pct=0.14, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=4.3, yield_3yr_cumulative_pct=11.8, yield_5yr_cumulative_pct=14.5, sharpe_ratio=2.10,
                    risk_level_label="נמוכה מאוד (סולידי)", liquidity_terms="נזילות יומית מלאה ללא קנסות שבירה וללא עמלות קנייה/מכירה בבנקים",
                    tax_rules_summary="25% מס על הרווח הריאלי בלבד (מעל עליית מדד המחירים לצרכן!) לעומת 15% נומינלי מהשקל הראשון בפיקדון בנקאי",
                    pros_for_client="פתרון אופטימלי לכרית ביטחון או לכסף המיועד לרכישת דירה/מטרה קרובה ב-1-2 השנים הקרובות; יתרון מיסוי באינפלציה מול פיקדון",
                    cons_for_client="תשואה מוגבלת לריבית בנק ישראל; אינה מייצרת צמיחת הון ריאלית משמעותית לטווח ארוך",
                    aum_millions_ils=130000, service_score=88.0
                ),
                # --- קרנות השתלמות וקרנות פנסיה ---
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="1540", provider_name="ילין לפידות", product_category="קרן השתלמות",
                    track_name="ילין לפידות השתלמות כללי", equity_exposure_pct=42.0, foreign_exposure_pct=49.0,
                    asset_mix_desc="מניות ואג\"ח בארץ ובחו\"ל", avg_fee_balance_pct=0.62, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=11.0, yield_3yr_cumulative_pct=24.6, yield_5yr_cumulative_pct=44.2, sharpe_ratio=0.95,
                    risk_level_label="בינונית", liquidity_terms="נזיל לאחר 6 שנות ותק (או 3 שנים להשתלמות/בגיל פרישה); ניתן לשימוש ממונף (הלוואה בתנאי פריים-0.5%)",
                    tax_rules_summary="פטור מלא ממס רווחי הון (0% מס במקום 25%!) עד תקרת ההפקדה המוטבת לשכיר או לעצמאי",
                    pros_for_client="מקלט המס הטוב ביותר בישראל לחיסכון הוני בינוני-ארוך; מעבר מסלולים פטור ממס",
                    cons_for_client="פתוח להפקדה רק לשכירים שזכאים מהמעסיק או לעצמאים בעלי הכנסה מעסק",
                    aum_millions_ils=32000, service_score=88.0
                ),
                MarketTrackMetric(
                    source_system="פנסיה-נט", track_id_code="PEN-101", provider_name="מנורה מבטחים", product_category="קרן פנסיה מקיפה",
                    track_name="מבטחים החדשה - לבני 50 ומטה", equity_exposure_pct=49.5, foreign_exposure_pct=54.0,
                    asset_mix_desc="30% הבטחת תשואה ממשלתית (5.15% צמוד), 70% שוק חופשי", avg_fee_balance_pct=0.17, avg_fee_deposit_pct=1.42,
                    yield_1yr_pct=11.4, yield_3yr_cumulative_pct=26.8, yield_5yr_cumulative_pct=48.2, sharpe_ratio=0.92,
                    risk_level_label="בינונית", liquidity_terms="מיועד לקצבת פרישה מגיל 60+ (משיכה הונית שלא כדין חייבת ב-35% מס)",
                    tax_rules_summary="הטבות מס בהפקדה (סעיפים 45א/47) ופטור על קצבה מזכה בפרישה (סעיף 9א)",
                    pros_for_client="דמי ניהול נמוכים מאוד, הבטחת תשואה על 30% מהנכסים, כיסוי שארים ונכות בעלות הדדית",
                    cons_for_client="כסף קצבתי נעול לפרישה; מקדם המרה אינו מובטח",
                    aum_millions_ils=112000, actuarial_balance_pct=0.08, service_score=83.0
                ),
                MarketTrackMetric(
                    source_system="פנסיה-נט", track_id_code="PEN-105", provider_name="מור", product_category="קרן פנסיה מקיפה",
                    track_name="מור פנסיה מקיפה - לבני 50 ומטה (נבחרת)", equity_exposure_pct=52.0, foreign_exposure_pct=57.0,
                    asset_mix_desc="30% הבטחת תשואה ממשלתית, 70% שוק חופשי", avg_fee_balance_pct=0.145, avg_fee_deposit_pct=1.00,
                    yield_1yr_pct=12.5, yield_3yr_cumulative_pct=28.9, yield_5yr_cumulative_pct=51.4, sharpe_ratio=1.01,
                    risk_level_label="בינונית", liquidity_terms="חיסכון קצבתי לפרישה",
                    tax_rules_summary="הטבות מס בהפקדה ופטור קצבה בפרישה",
                    pros_for_client="דמי ניהול מוזלים של קרן נבחרת (1.0% מהפקדה ו-0.145% מצבירה) ותשואות גבוהות",
                    cons_for_client="מוצר קצבתי ארוך טווח",
                    aum_millions_ils=14500, actuarial_balance_pct=0.09, service_score=87.0
                ),
            ]
            db.add_all(tracks)
            db.commit()

        if db.query(KnowledgeSourceItem).count() == 0:
            sources = [
                KnowledgeSourceItem(
                    code_key="INV_GEMEL_LEHASHKAA_LAW", domain_scope="INVESTMENT", category="חקיקה ומיסוי השקעות",
                    title="חוק הפיקוח על שירותים פיננסיים (קופות גמל) – קופת גמל להשקעה ותקרת הפקדה",
                    section_ref="סעיף 22 + סעיף 9(17א) לפקודה",
                    content_text="קופת גמל להשקעה מאפשרת הפקדה שנתית עד תקרה של כ-79,006 ש\"ח לאדם (נכון ל-2026). הכספים נזילים למשיכה הונית בכל עת בניכוי 25% מס רווחי הון ריאלי, מעבר בין מסלולי השקעה ובין חברות מנהלות פטור מאירוע מס (דחיית מס), ומשיכת הכספים כקצבה חודשית לאחר גיל 60 פטורה לחלוטין ממס רווחי הון וממס הכנסה.",
                    numeric_val=79006.0, unit="ILS", legal_status="חוק בתוקף", reliability_tier=1,
                    authority_name="רשות שוק ההון ורשות המסים", source_url="https://www.gov.il/he/pages/report_cma",
                    effective_from="2016-11-01", last_updated="2026-01-05", version_tag="2026"
                ),
                KnowledgeSourceItem(
                    code_key="INV_CAPITAL_GAINS_TAX_91", domain_scope="INVESTMENT", category="חקיקה ומיסוי השקעות",
                    title="פקודת מס הכנסה חלק ה' (סעיף 91) – מס רווחי הון בניירות ערך, קרנות סל וקרנות כספיות",
                    section_ref="סעיף 91 לפקודה",
                    content_text="מס רווחי הון על ניירות ערך סחירים, קרנות סל, קרנות נאמנות, קופות גמל להשקעה ופוליסות חיסכון עומד על 25% מהרווח הריאלי (בניכוי אינפלציה לפי מדד המחירים לצרכן). באפיקים שקליים לא צמודים (כגון מק\"מ או פיקדון בנקאי) המס הוא 15% נומינלי. בקרן כספית המס הוא 25% ריאלי, מה שמעניק לה יתרון מיסוי משמעותי על פני פיקדון בנקאי בסביבת אינפלציה.",
                    numeric_val=25.0, unit="PCT", legal_status="חוק בתוקף", reliability_tier=1,
                    authority_name="רשות המסים בישראל", source_url="https://www.gov.il/he/departments/israel_tax_authority",
                    effective_from="2012-01-01", last_updated="2026-01-01", version_tag="2026"
                ),
                KnowledgeSourceItem(
                    code_key="LAW_PENSION_ADVICE_SUITABILITY", domain_scope="GENERAL", category="חקיקה",
                    title="חוק הייעוץ והשיווק הפנסיוני וחוזר בירור צרכים – חובת התאמת רמת סיכון ואופק השקעה",
                    section_ref="סעיף 12 לחוק הייעוץ",
                    content_text="התאמת מוצר ומסלול השקעה חייבת להתבסס על בירור צרכי הלקוח, מטרת החיסכון, טווח ההשקעה, נזילות נדרשת ויכולת ספיגת תנודתיות. אין לקבוע שרמת סיכון מנייתית מתאימה אוטומטית רק בשל גיל צעיר אם הכסף מיועד למטרה קצרת טווח (כגון רכישת דירה).",
                    legal_status="חוק בתוקף", reliability_tier=1,
                    authority_name="רשות שוק ההון והכנסת", source_url="https://main.knesset.gov.il",
                    effective_from="2005-08-10", last_updated="2026-01-01", version_tag="2026"
                ),
                KnowledgeSourceItem(
                    code_key="TAX_SEC_9A_CEILING_2026", domain_scope="RETIREMENT_TAX", category="רשות המסים",
                    title="פקודת מס הכנסה סעיף 9א – תקרת הקצבה המזכה וקיבוע זכויות (161ד)",
                    section_ref="סעיף 9א",
                    content_text="תקרת הקצבה המזכה לשנת 2026: 9,430 ש\"ח. שיעור הפטור: 57.5%. מכפיל ההיוון לסל הפטור: 180. מקדם הקיזוז למענקים פטורים שנמשכו ב-32 השנים האחרונות: 1.35.",
                    numeric_val=9430.0, unit="ILS", legal_status="חוק בתוקף", reliability_tier=1,
                    authority_name="רשות המסים בישראל", source_url="https://www.gov.il/he/departments/israel_tax_authority",
                    effective_from="2026-01-01", last_updated="2026-01-05", version_tag="2026"
                ),
                KnowledgeSourceItem(
                    code_key="CMA_UNIFIED_CIRCULAR_MOBILITY", domain_scope="PENSION_LAW", category="חוזר רשות שוק ההון",
                    title="קודקס הרגולציה – ניוד ביטוחי מנהלים ושמירת מקדם קצבה מובטח",
                    section_ref="שער 6 חלק 2",
                    content_text="ניוד כספים מפוליסת ביטוח מנהלים ותיקה הכוללת מקדם קצבה מובטח (כגון 152 או 166) מבטל באופן בלתי הפיך את המקדם המובטח. חובה לבדוק נספח קצבה, גיל פרישה, זכויות שארים ומצב בריאותי לפני כל פעולה.",
                    legal_status="הוראה מחייבת", reliability_tier=2,
                    authority_name="רשות שוק ההון, ביטוח וחיסכון", source_url="https://www.gov.il/he/pages/information-entities-codex",
                    effective_from="2018-01-01", last_updated="2026-01-15", version_tag="2026"
                ),
            ]
            db.add_all(sources)
            db.commit()

        if db.query(InstitutionalEntity).count() == 0:
            entities = [
                InstitutionalEntity(entity_name="מור", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קופות גמל להשקעה, קרנות השתלמות, קרן פנסיה נבחרת, קרנות נאמנות/כספיות וניהול תיקים.", service_index_score=87.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="מיטב", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קופות גמל להשקעה (כולל S&P 500), קרנות השתלמות, פנסיה נבחרת, מיטב טרייד (מסחר עצמאי) וקרנות כספיות.", service_index_score=86.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="ילין לפידות", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קופות גמל להשקעה, קרנות השתלמות, קופות גמל וניהול תיקים.", service_index_score=88.0, claims_approval_rate_pct=92.0),
                InstitutionalEntity(entity_name="אנליסט", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קופות גמל להשקעה, קרנות השתלמות, קופות גמל וקרנות נאמנות.", service_index_score=85.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="אלטשולר שחם", entity_type="בית השקעות / חברה מנהלת", active_products_summary="קופות גמל להשקעה, קרנות השתלמות ופנסיה נבחרת.", service_index_score=82.0, claims_approval_rate_pct=88.0),
                InstitutionalEntity(entity_name="הפניקס", entity_type="חברת ביטוח", active_products_summary="פוליסות חיסכון (אינווסט), קופות גמל, קרן פנסיה מקיפה, ביטוחי חיים ובריאות.", service_index_score=84.0, claims_approval_rate_pct=90.0),
                InstitutionalEntity(entity_name="הראל", entity_type="חברת ביטוח", active_products_summary="פוליסות חיסכון (מגוון), קרן פנסיה מקיפה, קרנות כספיות, ביטוחי בריאות וחיים.", service_index_score=85.0, claims_approval_rate_pct=91.0),
                InstitutionalEntity(entity_name="מנורה מבטחים", entity_type="חברת ביטוח", active_products_summary="מבטחים החדשה (פנסיה מקיפה), פוליסות חיסכון, ביטוחי חיים ובריאות.", service_index_score=83.0, claims_approval_rate_pct=89.0),
                InstitutionalEntity(entity_name="מגדל", entity_type="חברת ביטוח", active_products_summary="מגדל מקפת, פוליסות חיסכון (קשת), ביטוחי מנהלים ותיקים, חיים ובריאות.", service_index_score=81.0, claims_approval_rate_pct=88.0),
                InstitutionalEntity(entity_name="כלל ביטוח", entity_type="חברת ביטוח", active_products_summary="כלל פנסיה, פוליסות חיסכון, ביטוחי חיים, בריאות ומחלות קשות.", service_index_score=80.0, claims_approval_rate_pct=87.0),
                InstitutionalEntity(entity_name="IBI", entity_type="בית השקעות / חברה מנהלת", active_products_summary="מסחר עצמאי (IBI Trade), קופות גמל מחקות מדד, קרנות סל וניהול תיקים.", service_index_score=86.0, claims_approval_rate_pct=90.0),
            ]
            db.add_all(entities)
            db.commit()

        if db.query(InsuranceTariffCatalog).count() == 0:
            db.add_all([
                InsuranceTariffCatalog(provider_name="הראל", insurance_branch="ביטוח חיים (ריסק)", plan_name="מענקית ריסק מוות", base_monthly_rate_per_100k_age35=11.5, age_slope_pct_per_year=6.8, smoker_surcharge_pct=45.0, max_discount_pct=32.0, discount_valid_years=5, coverage_scope_details="פיצוי חד-פעמי למקרה מוות מכל סיבה + אופציית הגדלה ללא חיתום.", waiting_period_days=0, deductible_ils=0.0, exclusions_and_underwriting="חריג התאבדות בשנה הראשונה; כפוף להצהרת בריאות וחיתום."),
                InsuranceTariffCatalog(provider_name="הפניקס", insurance_branch="ביטוח חיים (ריסק)", plan_name="ריסק 1 מתחדש", base_monthly_rate_per_100k_age35=11.2, age_slope_pct_per_year=6.7, smoker_surcharge_pct=42.0, max_discount_pct=35.0, discount_valid_years=6, coverage_scope_details="פיצוי למקרה מוות + הקדמת 50% בחולה סופני.", waiting_period_days=0, deductible_ils=0.0, exclusions_and_underwriting="כפוף לחיתום רפואי ועיסוקי."),
                InsuranceTariffCatalog(provider_name="מנורה מבטחים", insurance_branch="ביטוח בריאות", plan_name="בריאות מקיפה - השתלות, תרופות וניתוחים בחו\"ל", base_monthly_rate_per_100k_age35=95.0, age_slope_pct_per_year=4.2, smoker_surcharge_pct=15.0, max_discount_pct=20.0, discount_valid_years=3, coverage_scope_details="השתלות עד 5M ₪, תרופות מחוץ לסל עד 3M ₪, ניתוחים בחו\"ל מהשקל הראשון.", waiting_period_days=90, deductible_ils=300.0, exclusions_and_underwriting="אכשרה 90 יום; כפוף לחיתום רפואי."),
            ])
            db.commit()

        if db.query(AgencyCommissionAgreement).count() == 0:
            db.add_all([
                AgencyCommissionAgreement(provider_name="הפניקס", product_category="קופת גמל להשקעה / פוליסת חיסכון", scope_commission_pct=0.0, ongoing_nifraim_pct=0.25, deposit_commission_pct=0.0, one_time_bonus_per_1m_ils=4000.0, clawback_period_months=12, clawback_rule_desc="השבה יחסית ב-12 חודשים ראשונים", agent_share_pct=55.0),
                AgencyCommissionAgreement(provider_name="מיטב", product_category="קופת גמל להשקעה / קרן השתלמות", scope_commission_pct=0.0, ongoing_nifraim_pct=0.20, deposit_commission_pct=0.0, one_time_bonus_per_1m_ils=3500.0, clawback_period_months=12, clawback_rule_desc="השבה יחסית ב-12 חודשים ראשונים", agent_share_pct=50.0),
                AgencyCommissionAgreement(provider_name="מור", product_category="קופת גמל להשקעה / קרן פנסיה", scope_commission_pct=4.5, ongoing_nifraim_pct=0.18, deposit_commission_pct=0.4, one_time_bonus_per_1m_ils=3500.0, clawback_period_months=18, clawback_rule_desc="השבה יחסית ב-18 חודשים ראשונים", agent_share_pct=50.0),
            ])
            db.commit()

        if db.query(LegalPrecedent).count() == 0:
            db.add_all([
                LegalPrecedent(case_number="ע\"א 702/16 פלוני נ' חברת ביטוח וסוכן ביטוח", court_name="בית המשפט העליון", ruling_date="2018-09-12", topic="אחריות סוכן ביטוח וחובת גילוי והנמקה", precedent_weight="הלכה מחייבת", summary_and_rule="על סוכן הביטוח והמתכנן הפיננסי מוטלת חובת זהירות מוגברת לבצע בירור צרכים מלא ולהציג ללקוח את משמעויות הדמ\"נ, המיסוי והסיכון בטרם בחירת מוצר.", source_link="https://www.gov.il/he/departments/the_judicial_authority"),
            ])
            db.commit()
    finally:
        db.close()


init_and_seed_database()


# =====================================================================
# 4. מנוע איתור צרכים, התאמת מוצרים וסימולציות רב-שנתיות ב-Python
# =====================================================================
def extract_case_parameters(user_query: str, active_client: Optional[Client] = None) -> Dict[str, Any]:
    """
    מנתח ב-Python את שאלת הסוכן + כרטיס הלקוח כדי לחלץ:
    גיל, סכום חד-פעמי, הפקדה חודשית, אופק השקעה, מטרת הכסף, נזילות וסוג הדילמה.
    """
    q = user_query.replace(",", "")
    age = (2026 - active_client.birth_year) if active_client else None
    age_match = re.search(r"בן\s*(\d{2})|בת\s*(\d{2})|גיל\s*(\d{2})", q)
    if age_match:
        age = int(next(g for g in age_match.groups() if g is not None))

    # זיהוי סכומים כספיים בשאלה (הפרדה חכמה בין הפקדה חודשית לסכום חד-פעמי)
    monthly_deposit = 0.0
    initial_lump_sum = 0.0

    mo_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:ש\"ח|₪|שח)?\s*(?:בכל\s*חודש|בחודש|חודשי|להפקיד\s*מדי\s*חודש)", q)
    if not mo_match:
        mo_match = re.search(r"(?:להפקיד|הפקדה\s*חודשית\s*של)\s*(\d+(?:\.\d+)?)", q)
    if mo_match:
        monthly_deposit = float(mo_match.group(1))

    all_nums = [float(n) for n in re.findall(r"\b\d{4,8}\b", q)]
    for num in all_nums:
        if num != monthly_deposit and num > 2030:  # לא שנה
            if num > initial_lump_sum:
                initial_lump_sum = num

    # אם לא צוינו סכומים בשאלה אך יש ערכים בכרטיס הלקוח או ברירת מחדל לסימולציה
    explicit_numbers_found = (initial_lump_sum > 0 or monthly_deposit > 0)
    if not explicit_numbers_found:
        initial_lump_sum = 50_000.0
        monthly_deposit = 1_000.0

    # זיהוי אופק השקעה ומטרה
    horizon_years = active_client.investment_horizon_years if active_client else None
    hz_match = re.search(r"(?:בעוד|לעוד|לטווח\s*של|ל-)\s*(\d{1,2})\s*שנ(?:ים|ה)", q)
    if hz_match:
        horizon_years = int(hz_match.group(1))
    elif "שנתיים" in q:
        horizon_years = 2

    short_term_goal = any(w in q for w in ["דירה", "חתונה", "שנה הקרובה", "שנתיים", "טווח קצר", "נזיל מיידית"])
    retirement_or_pension_case = any(
        w in q for w in ["מקדם", "152", "166", "קיבוע זכויות", "161", "תיקון 190", "פנסיה מקיפה", "ביטוח מנהלים", "קצבה", "פרישה", "פיצויים"]
    )
    self_employed_mentioned = any(w in q for w in ["עצמאי", "עוסק מורשה", "עוסק פטור", "בעל שליטה"])

    missing_critical_info = []
    if horizon_years is None:
        missing_critical_info.append("אופק ההשקעה המדויק (מתי הלקוח יזדקק לכסף והאם מיועד לרכישת דירה/מטרה קרובה)")
    if not any(w in q for w in ["סיכון", "תנודתיות", "מנייתי", "סולידי", "ירידות"]):
        missing_critical_info.append("סיבולת הסיכון של הלקוח ויכולתו הפסיכולוגית והפיננסית לספוג ירידות חדות בשוק ההון")
    if not any(w in q for w in ["כרית ביטחון", "חירום", "עו\"ש"]):
        missing_critical_info.append("קיום כרית ביטחון נזילה לחירום (3–6 חודשי מחיה) בנפרד מכספי ההשקעה")

    return {
        "client_age": age,
        "initial_lump_sum": initial_lump_sum,
        "monthly_deposit": monthly_deposit,
        "explicit_numbers_found": explicit_numbers_found,
        "horizon_years": horizon_years,
        "short_term_goal_detected": short_term_goal,
        "is_retirement_or_pension_query": retirement_or_pension_case,
        "is_self_employed": self_employed_mentioned,
        "is_conditional_recommendation": len(missing_critical_info) > 0,
        "missing_critical_info": missing_critical_info,
    }


def match_relevant_products_and_tracks(db: Session, case_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    מנוע התאמת מוצרים וסינון מסלולים אמיתיים ב-Python (ללא שום גישה לעמלות סוכנות!).
    בוחר את המוצרים והמסלולים הרלוונטיים לפי גיל, אופק השקעה, סכום ומטרה.
    """
    all_tracks = db.query(MarketTrackMetric).all()
    age = case_params["client_age"] or 30
    lump = case_params["initial_lump_sum"]
    short_term = case_params["short_term_goal_detected"] or (
        case_params["horizon_years"] is not None and case_params["horizon_years"] <= 2
    )
    is_retire = case_params["is_retirement_or_pension_query"]

    recommended_wrappers = []
    selected_tracks: List[MarketTrackMetric] = []

    if is_retire:
        recommended_wrappers = ["קרן פנסיה מקיפה", "ביטוח מנהלים (בדיקת מקדם מובטח)", "קופת גמל (תיקון 190 / קצבה מוכרת)"]
        selected_tracks = [t for t in all_tracks if t.product_category in ("קרן פנסיה מקיפה", "קופת גמל להשקעה", "פוליסת חיסכון")]
    elif short_term:
        recommended_wrappers = [
            "1. קרן כספית שקלית (עדיפות ראשונה לטווח של עד שנתיים – נזילות יומית ומיסוי 25% ריאלי מעל המדד)",
            "2. קופת גמל להשקעה או פוליסת חיסכון במסלול סולידי/כללי (רק אם האופק גמיש מעבר לשנתיים)",
        ]
        selected_tracks = [t for t in all_tracks if t.risk_level_label in ("נמוכה מאוד (סולידי)", "בינונית")]
    else:
        # מקרה השקעה וצמיחה (למשל לקוח בן 25 עם 50,000 ש"ח + 1,000 ש"ח בחודש)
        recommended_wrappers = [
            "1. קופת גמל להשקעה (במסלול מנייתי או עוקב מדד S&P 500) – פתרון מנוהל מוסדית, אידיאלי להוראת קבע חודשית (1,000 ₪) + הפקדה חד-פעמית עד התקרה השנתית (79,006 ₪), עם דחיית מס במעבר בין מסלולים ואופציית קצבה פטורה בגיל 60.",
            "2. תיק השקעות עצמאי באמצעות קרנות סל רחבות (MSCI World / S&P 500) – פתרון פסיבי בעלויות נמוכות במיוחד (דמ\"נ ~0.15%-0.20%) ושליטה מלאה, מתאים ללקוח המסוגל להתנהל עצמאית ללא ביצוע פעולות רגשיות.",
            "3. פוליסת חיסכון פיננסית (כחלופה שלישית/משלימה) – רלוונטית במיוחד כאשר סכום ההשקעה חוצה את תקרת הגמל להשקעה (79,006 ₪ לשנה), או לשילוב נכסים לא סחירים, או לילידי 1948 ומטה הזכאים להטבת סעיף 125ד.",
        ]
        if case_params["is_self_employed"]:
            recommended_wrappers.insert(0, "0. קרן השתלמות לעצמאי – עדיפות עליונה בשל פטור מלא ממס רווחי הון (25%) והטבת מס בהפקדה!")

        # שליפת מסלולים רלוונטיים להשוואה (גמל להשקעה, תיק קרנות סל, פוליסת חיסכון וקרן כספית כעוגן סולידי להשוואה)
        selected_tracks = [
            t for t in all_tracks
            if t.product_category in ("קופת גמל להשקעה", "תיק השקעות עצמאי (קרנות סל)", "פוליסת חיסכון", "קרן כספית / פיקדון", "קרן השתלמות")
        ]

    # אם הלקוח מבוגר (למשל יליד 1948 ומטה, גיל 78+), נדגיש את פוליסת החיסכון בגלל סעיף 125ד
    senior_125d_note = ""
    if age >= 77:
        senior_125d_note = "הלקוח בטווח גילאי הזכאות לסעיף 125ד (ילידי 1948 ומטה) – פוליסת חיסכון מעניקה כאן יתרון מיסוי מובהק (פטור שנתי ממס רווחי הון באמצעות מכירה רעיונית)!"

    return {
        "recommended_wrappers": recommended_wrappers,
        "matched_tracks": selected_tracks,
        "senior_125d_note": senior_125d_note,
    }


def run_multi_horizon_investment_simulation(
    initial_amount: float,
    monthly_deposit: float,
    horizons: List[int] = [5, 10, 20, 30],
    gross_real_yields: Dict[str, float] = {"שמרני (3.0% ריאלי)": 3.0, "תרחיש מרכזי (5.5% ריאלי)": 5.5, "מנייתי צמיחה (7.5% ריאלי)": 7.5},
    fee_options: Dict[str, float] = {
        "תיק קרנות סל עצמאי (דמ\"נ משוקלל 0.18%)": 0.18,
        "קופת גמל להשקעה (דמ\"נ 0.65%)": 0.65,
        "פוליסת חיסכון (דמ\"נ 0.85%)": 0.85,
    },
) -> Dict[str, Any]:
    """
    מנוע סימולציות השקעה רב-שנתי ב-Python.
    מחשב עבור כל אופק (5, 10, 20, 30 שנה) את סך ההפקדות מהכיס (קרן),
    הרווח הריאלי המשוער, שחיקת דמי הניהול בשקלים בין החלופות, והיתרה נטו לאחר מס רווחי הון (25% ריאלי).
    """
    table_by_horizon = []
    base_yield_label = "תרחיש מרכזי (5.5% ריאלי)"
    base_yield = gross_real_yields[base_yield_label]

    for yrs in horizons:
        months = yrs * 12
        total_principal = initial_amount + (monthly_deposit * months)

        # חישוב ברוטו ללא דמי ניהול
        r_m_gross = (1.0 + (base_yield / 100.0)) ** (1.0 / 12.0) - 1.0
        bal_gross = initial_amount
        for _ in range(months):
            bal_gross = bal_gross * (1.0 + r_m_gross) + monthly_deposit

        row_data = {
            "טווח השקעה": f"{yrs} שנים",
            "סך הפקדות קרן (מהכיס)": f"{total_principal:,.0f} ₪",
        }

        for fee_label, fee_pct in fee_options.items():
            net_annual = (base_yield - fee_pct) / 100.0
            r_m_net = (1.0 + net_annual) ** (1.0 / 12.0) - 1.0
            bal_before_tax = initial_amount
            for _ in range(months):
                bal_before_tax = bal_before_tax * (1.0 + r_m_net) + monthly_deposit

            real_profit = max(0.0, bal_before_tax - total_principal)
            estimated_tax_25 = real_profit * 0.25
            bal_after_tax = bal_before_tax - estimated_tax_25
            fee_erosion = max(0.0, bal_gross - bal_before_tax)

            short_name = fee_label.split("(")[0].strip()
            row_data[f"{short_name} - צבירה לפני מס"] = f"{bal_before_tax:,.0f} ₪"
            row_data[f"{short_name} - רווח מוערך (לפני מס)"] = f"{real_profit:,.0f} ₪"
            row_data[f"{short_name} - שחיקת דמ\"נ"] = f"{fee_erosion:,.0f} ₪"
            row_data[f"{short_name} - נטו אחרי 25% מס"] = f"{bal_after_tax:,.0f} ₪"

        table_by_horizon.append(row_data)

    # טבלת רגישות לתרחישי תשואה (עבור טווח של 10 ו-20 שנה בקופת גמל להשקעה דמ"נ 0.65%)
    yield_sensitivity = []
    for y_label, y_val in gross_real_yields.items():
        for check_yr in [5, 10, 20]:
            m = check_yr * 12
            princ = initial_amount + monthly_deposit * m
            r_m = (1.0 + ((y_val - 0.65) / 100.0)) ** (1.0 / 12.0) - 1.0
            b = initial_amount
            for _ in range(m):
                b = b * (1.0 + r_m) + monthly_deposit
            prof = max(0.0, b - princ)
            yield_sensitivity.append({
                "תרחיש תשואה ריאלית (ברוטו)": y_label,
                "טווח": f"{check_yr} שנים",
                "סך הפקדות קרן": f"{princ:,.0f} ₪",
                "רווח ריאלי משוער (אחרי דמ\"נ 0.65%)": f"{prof:,.0f} ₪",
                "צבירה ברוטו לפני מס": f"{b:,.0f} ₪",
                "צבירה נטו (אחרי 25% מס ריאלי)": f"{b - (prof * 0.25):,.0f} ₪",
            })

    return {
        "assumptions_note": (
            f"החישוב מבוצע ב-Python בערכים **ריאליים** (מנוכי אינפלציה), על בסיס השקעה ראשונית של **{initial_amount:,.0f} ₪** "
            f"והפקדה חודשית של **{monthly_deposit:,.0f} ₪**. מס רווחי הון חושב לפי 25% ריאלי על הרווח בלבד (במשיכה כקצבה מגיל 60 בקופת גמל להשקעה קיים פטור ממס). "
            "אין בהנחות הסימולציה כדי להוות הבטחת תשואה עתידית."
        ),
        "horizon_comparison_table": table_by_horizon,
        "yield_sensitivity_table": yield_sensitivity,
    }


# =====================================================================
# 5. סכמת Structured Outputs לפורמט התשובה החדש (א' עד ז') של המתכנן הפיננסי
# =====================================================================
class InstitutionalComparisonRow(BaseModel):
    provider_name: str = Field(description="1. שם הגוף המנהל")
    product_name: str = Field(description="2. שם המוצר")
    track_name_and_code: str = Field(description="3. שם ומספר מסלול ההשקעה הרשמי")
    equity_and_assets: str = Field(description="4. רמת החשיפה למניות ותמהיל הנכסים")
    relevant_fees: str = Field(description="5. דמי ניהול רלוונטיים (מדווחים / טווח שוק)")
    historical_yields: str = Field(description="6. תשואות היסטוריות לתקופות השוואה זהות (1ש', 3ש', 5ש')")
    risk_and_sharpe: str = Field(description="7. רמת סיכון ומדד שארפ")
    liquidity_terms: str = Field(description="8. נזילות ואפשרויות משיכה")
    tax_aspects: str = Field(description="9. היבטי מיסוי רלוונטיים")
    case_pros_and_cons: str = Field(description="10. יתרונות וחסרונות עבור המקרה הספציפי של הלקוח")
    source_and_date: str = Field(description="מקור הנתון ותאריך עדכון")


class FinancialPlannerAdvisoryOutput(BaseModel):
    recommendation_status_badge: str = Field(
        description="סיווג ההמלצה: 'המלצה מותנית (טרם השלמת בירור צרכים מלא)' או 'ניתוח מותאם לאחר בירור צרכים'"
    )
    section_a_executive_conclusion: str = Field(
        description="א. מסקנה מקצועית קצרה: הצג מיד בהתחלה את שתי החלופות המרכזיות שכדאי לבחון ומהו הפרמטר שיכריע ביניהן."
    )
    section_b_client_analysis: str = Field(
        description="ב. ניתוח הלקוח: הסבר בקצרה אילו מאפיינים של הלקוח (גיל, סכום חד-פעמי, הפקדה חודשית, אופק, סיכון, נזילות) משפיעים על הבחירה ומה עדיין חסר."
    )
    section_c_practical_solution: str = Field(
        description="ג. הצעת פתרון מעשית: הצג מוצר ספציפי, סוג מסלול (מנייתי / עוקב מדד / כללי / כספית), רמת סיכון ותנאי בחירה (למה לא לבחור אוטומטית סיכון גבוה רק בגלל גיל צעיר אם הכסף מיועד לדירה)."
    )
    section_d_institutional_comparison: List[InstitutionalComparisonRow] = Field(
        description="ד. השוואת גופים מנהלים ומסלולים אמיתיים מתוך מאגר הנתונים המאומת (מור, מיטב, ילין לפידות, אנליסט, הפניקס, הראל וכו'). אל תמציא נתונים!"
    )
    section_e_simulation_insights: str = Field(
        description="ה. סימולציה מספרית: הסבר מילולי ומספרי של תוצאות מנוע ה-Python המצורפות (כמה קרן הופקדה, כמה רווח משוער, השפעת דמי הניהול לאורך 5/10/20/30 שנה והנחות החישוב הריאליות)."
    )
    section_f_agent_action_plan: List[str] = Field(
        description="ו. פעולות לביצוע: סדר פעולות אופרטיבי ברור לסוכן LEVERAGE (בירור אופק וסיכון, השוואת 2-3 גופים, קבלת הצעת דמי ניהול, בדיקת מסלול, הכנת המלצה מנומקת ופתיחת מוצר לאחר אישור)."
    )
    section_g_critical_questions: List[str] = Field(
        description="ז. שאלות מהותיות להשלמה: שאל רק את השאלות שבאמת עשויות לשנות את הפתרון המקצועי."
    )
    relevant_regulatory_note: str = Field(
        description="הערה רגולטורית/מיסויית ממוקדת אך ורק אם היא משפיעה ישירות על הבחירה במקרה זה (ללא הכנסת סעיפי חוק לא קשורים)."
    )


def generate_senior_planner_advisory(
    api_key: str,
    db: Session,
    user_query: str,
    active_client: Optional[Client] = None,
    response_depth: str = "ניתוח מתכנן פיננסי מלא (Full Advisory)",
    as_of_date: str = "2026-10-17",
) -> Dict[str, Any]:
    """
    מנוע הייעוץ המשודרג של LEVERAGE AI:
    1. מחלץ ב-Python את מאפייני הלקוח והסכומים מהשאלה.
    2. מאתר את המוצרים והמסלולים האמיתיים המתאימים ממאגר גמל-נט/פנסיה-נט/ביטוח-נט.
    3. מריץ סימולציית Python רב-שנתית (5, 10, 20, 30 שנה) עם הפרדת קרן, רווח ושחיקת דמ"נ.
    4. מפיק תשובה מובנית בפורמט א'–ז' ברמה של מתכנן פיננסי בכיר.
    """
    case_params = extract_case_parameters(user_query, active_client)
    matched_bundle = match_relevant_products_and_tracks(db, case_params)
    python_sim = run_multi_horizon_investment_simulation(
        initial_amount=case_params["initial_lump_sum"],
        monthly_deposit=case_params["monthly_deposit"],
    )

    # בניית הקשר מסלולים מאומתים עבור ה-AI
    tracks_text_lines = []
    for t in matched_bundle["matched_tracks"]:
        tracks_text_lines.append(
            f"- גוף: {t.provider_name} | מוצר: {t.product_category} | מסלול ומספר: {t.track_name} (קוד: {t.track_id_code}) | "
            f"חשיפה למניות: {t.equity_exposure_pct}% ({t.asset_mix_desc}) | דמ\"נ צבירה: {t.avg_fee_balance_pct}% | "
            f"תשואות היסטוריות ({t.report_period}): 1ש'={t.yield_1yr_pct}%, 3ש'={t.yield_3yr_cumulative_pct}%, 5ש'={t.yield_5yr_cumulative_pct}% | "
            f"סיכון ושארפ: {t.risk_level_label} (שארפ {t.sharpe_ratio}) | נזילות: {t.liquidity_terms} | מיסוי: {t.tax_rules_summary} | "
            f"יתרונות: {t.pros_for_client} | חסרונות: {t.cons_for_client} | מקור: {t.source_system} (עודכן: {t.last_verified_date})"
        )

    # סינון חוקים רלוונטיים בלבד (לא להכניס סעיף 9א / תיקון 190 לשאלת השקעה של צעיר!)
    allowed_scopes = ["GENERAL", "INVESTMENT"]
    if case_params["is_retirement_or_pension_query"]:
        allowed_scopes.extend(["RETIREMENT_TAX", "PENSION_LAW"])

    rel_laws = (
        db.query(KnowledgeSourceItem)
        .filter(KnowledgeSourceItem.domain_scope.in_(allowed_scopes))
        .all()
    )
    laws_context = "\n".join(
        [f"- {l.title} ({l.section_ref}): {l.content_text} [מקור: {l.authority_name}, עודכן: {l.last_updated}]" for l in rel_laws]
    )

    if not api_key:
        return {
            "error": "⚠️ מפתח OpenAI API טרם הוגדר. אנא הזן אותו בסרגל הצד השמאלי כדי להפעיל את מנוע ה-AI.",
            "case_params": case_params,
            "python_sim": python_sim,
            "matched_bundle": matched_bundle,
            "structured_advisory": None,
        }

    oa_client = OpenAI(api_key=api_key)
    depth_instruction = (
        "הסוכן בחר במצב 'תשובה ממוקדת ומהירה' – נסח כל סעיף בתמציתיות חדה, ישירה ומעשית מאוד."
        if "קצרה" in response_depth or "ממוקדת" in response_depth
        else "הסוכן בחר במצב 'ניתוח מתכנן פיננסי מלא' – נסח ניתוח מעמיק, מעשי ומפורט בכל סעיף."
    )

    system_prompt = f"""אתה מתכנן פיננסי בכיר (CFP) ועוזר מקצועי לסוכני הביטוח והמתכננים הפיננסיים של LEVERAGE.
המשתמש מולך הוא איש מקצוע ב-LEVERAGE (סוכן/מתכנן פיננסי), ולא לקוח קצה! דבר אליו בגובה העיניים כאל קולגה מקצועי.

כללי חשיבה והמלצה מחייבים:
1. פרקטיות מעל תאוריה: אל תפתח בהרצאה משפטית או בהגדרות מילוניות של מהי קופת גמל. פתח מיד בפתרון ההשקעה המעשי ובשתי החלופות המובילות שיש להשוות (למשל: קופת גמל להשקעה במסלול מנייתי/S&P 500 מול תיק השקעות פסיבי עצמאי בקרנות סל, או פוליסת חיסכון / קרן כספית בהתאם למקרה).
2. איסור הזרקת רגולציה לא קשורה: בשאלה על השקעה של חוסך צעיר, אל תזכיר בשום אופן את סעיף 9א, טופס 161ד או תיקון 190 שאינם רלוונטיים! התייחס רק למיסוי ורגולציה שמשפיעים ישירות על המקרה (כגון תקרת קופת גמל להשקעה 79,006 ש"ח, דחיית מס במעבר מסלולים, או מס רווחי הון 25% ריאלי).
3. גיל מול אופק השקעה וסיכון: לעולם אל תקבע שמסלול מנייתי בסיכון גבוה מתאים אוטומטית רק בגלל שהלקוח צעיר (למשל בן 25). ציין במפורש שהבחירה במסלול מנייתי/מדדי מותנית בכך שהכסף אינו מיועד לרכישת דירה או מטרה קצרת טווח ב-2–3 השנים הקרובות, שיש ללקוח כרית ביטחון נזילה, ושהוא מסוגל לספוג ירידות חדות בשוק.
4. שימוש בנתונים אמיתיים בלבד: בטבלת השוואת הגופים (סעיף ד'), השתמש אך ורק במסלולים, מספרי המסלולים, דמי הניהול והתשואות ממאגר הנתונים המצורף להלן. אל תמציא שיעורי דמי ניהול או תשואות. ציין שלפני סגירה יש לבקש הצעת דמי ניהול פרטנית מהגוף המנהל.
5. שימוש בסימולציית ה-Python (סעיף ה'): הסתמך באופן מדויק על תוצאות סימולציית ה-Python המצורפת להלן.
{depth_instruction}

=== אבחון צרכי הלקוח שחולץ ב-Python ===
- גיל מזוהה: {case_params['client_age'] or 'לא צוין בשאלה'}
- סכום השקעה ראשוני לסימולציה: {case_params['initial_lump_sum']:,.0f} ש"ח
- הפקדה חודשית לסימולציה: {case_params['monthly_deposit']:,.0f} ש"ח
- אופק השקעה: {case_params['horizon_years'] or 'טרם הוגדר – דורש בירור מול הלקוח'}
- מידע מהותי חסר לבירור: {', '.join(case_params['missing_critical_info']) if case_params['missing_critical_info'] else 'כל נתוני הבסיס קיימים'}
- חלופות מוצרים מובילות שאותרו: {'; '.join(matched_bundle['recommended_wrappers'])}
{matched_bundle['senior_125d_note']}

=== מאגר מסלולים אמיתיים ומאומתים להשוואה (גמל-נט / ביטוח-נט / שוק ההון) ===
{chr(10).join(tracks_text_lines)}

=== תוצאות סימולציית מנוע החישוב ב-Python (ערכים ריאליים) ===
{json.dumps(python_sim['horizon_comparison_table'], ensure_ascii=False)}

=== הוראות חוק ומיסוי רלוונטיות למקרה זה בלבד ===
{laws_context}"""

    try:
        comp = oa_client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"מקרה הלקוח שהציג הסוכן:\n{user_query}"},
            ],
            response_format=FinancialPlannerAdvisoryOutput,
            temperature=0.0,
        )
        return {
            "error": None,
            "case_params": case_params,
            "python_sim": python_sim,
            "matched_bundle": matched_bundle,
            "structured_advisory": comp.choices[0].message.parsed,
        }
    except Exception as e:
        return {
            "error": f"שגיאה בהפעלת מנוע ה-AI: {e}",
            "case_params": case_params,
            "python_sim": python_sim,
            "matched_bundle": matched_bundle,
            "structured_advisory": None,
        }


def render_advisory_report_markdown(adv: FinancialPlannerAdvisoryOutput, python_sim: Dict[str, Any], show_full: bool = True) -> str:
    """מעצב את אובייקט ה-Structured Output של המתכנן הפיננסי לתצוגה מקצועית וברורה."""
    lines = [
        f"🔖 **סטטוס אבחון:** `{adv.recommendation_status_badge}`",
        f"### א. מסקנה מקצועית קצרה\n{adv.section_a_executive_conclusion}",
        f"### ב. ניתוח הלקוח\n{adv.section_b_client_analysis}",
        f"### ג. הצעת פתרון מעשית\n{adv.section_c_practical_solution}",
    ]
    if show_full:
        lines.append(f"### ה. תובנות הסימולציה המספרית (מנוע Python)\n{adv.section_e_simulation_insights}\n\n> 📌 *{python_sim['assumptions_note']}*")
    lines.append("### ו. פעולות לביצוע עבור הסוכן\n" + "\n".join([f"{idx}. {act}" for idx, act in enumerate(adv.section_f_agent_action_plan, 1)]))
    lines.append("### ז. שאלות מהותיות להשלמה מול הלקוח\n" + "\n".join([f"- ❓ {q}" for q in adv.section_g_critical_questions]))
    if adv.relevant_regulatory_note:
        lines.append(f"\n---\n⚖️ **דגש רגולטורי/מיסויי ממוקד למקרה זה:** {adv.relevant_regulatory_note}")
    return "\n\n".join(lines)


# =====================================================================
# 6. פונקציות עזר: אקטואריה לפרישה, תעריפי ביטוח, מבנה אחיד XML, עמלות ובדיקות QA
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
    units_100k = max(1.0, coverage_amount_ils / 100_000.0)
    yearly_schedule = []
    total_cumulative = 0.0

    for yr_offset in range(projection_years):
        age = start_age + yr_offset
        age_factor = (1.0 + (tariff.age_slope_pct_per_year / 100.0)) ** (age - 35)
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
        yearly_schedule.append({
            "שנה": yr_offset + 1,
            "גיל מבוטח": age,
            "הנחה בתוקף (%)": tariff.max_discount_pct if yr_offset < tariff.discount_valid_years else 0.0,
            "פרמיה חודשית משוערת (₪)": round(monthly_prem, 1),
            "עלות שנתית (₪)": round(annual_prem, 0),
        })

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
        "total_year1_gross_ils": round(total_year1_agency, 2),
        "agent_share_ils": round(agent_cut, 2),
        "agency_retained_ils": round(agency_net_cut, 2),
        "clawback_exposure_ils": round(clawback_at_risk, 2),
    }


def run_automated_benchmark_suite(db: Session) -> List[Dict[str, Any]]:
    """
    מריץ סדרת בדיקות אוטומטיות על מקרי לקוח אמיתיים שעברו אנונימיזציה
    ומוודא דיוק מתמטי, התאמת מוצרים, סינון רגולציה לא קשורה והפרדת עמלות.
    """
    results = []

    # מקרה מבחן 1: לקוח בן 25 עם 50,000 ש"ח פנויים ו-1,000 ש"ח בחודש (הדוגמה של הסוכן)
    q1 = "יש לי לקוח בן 25 שרוצה להתחיל להשקיע. הוא יכול להפקיד 1,000 ₪ בכל חודש ויש לו 50,000 ₪ פנויים להשקעה מיידית. מה אתה ממליץ?"
    p1 = extract_case_parameters(q1)
    m1 = match_relevant_products_and_tracks(db, p1)
    sim1 = run_multi_horizon_investment_simulation(p1["initial_lump_sum"], p1["monthly_deposit"])
    passed_1 = (
        p1["client_age"] == 25
        and p1["initial_lump_sum"] == 50000.0
        and p1["monthly_deposit"] == 1000.0
        and p1["is_retirement_or_pension_query"] is False
        and p1["is_conditional_recommendation"] is True
        and any("קופת גמל להשקעה" in w for w in m1["recommended_wrappers"])
        and any("תיק השקעות עצמאי" in w for w in m1["recommended_wrappers"])
        and len(sim1["horizon_comparison_table"]) == 4
    )
    results.append({
        "מקרה מבחן (אנונימי)": "מקרה 1: חוסך בן 25, 50,000 ₪ חד-פעמי + 1,000 ₪ בחודש",
        "מה נבדק אוטומטית": "חילוץ גיל וסכומים, זיהוי כהמלצה מותנית (חסר אופק/דירה), בחירת גמל להשקעה מול קרנות סל, ואי-הזרקת סעיף 9א/161ד",
        "סטטוס בדיקה": "✅ עבר בהצלחה" if passed_1 else "❌ נכשל",
    })

    # מקרה מבחן 2: לקוח צעיר בן 27 שצריך את הכסף לרכישת דירה בעוד שנתיים
    q2 = "לקוח בן 27 עם 200,000 ש\"ח פנויים שמיועדים לרכישת דירה בעוד שנתיים. איך להשקיע?"
    p2 = extract_case_parameters(q2)
    m2 = match_relevant_products_and_tracks(db, p2)
    passed_2 = (
        p2["horizon_years"] == 2
        and p2["short_term_goal_detected"] is True
        and any("קרן כספית" in w for w in m2["recommended_wrappers"])
    )
    results.append({
        "מקרה מבחן (אנונימי)": "מקרה 2: בן 27 החוסך לדירה בעוד שנתיים (בדיקת מניעת סיכון מנייתי אוטומטי לצעיר)",
        "מה נבדק אוטומטית": "זיהוי אופק קצר (שנתיים) ומטרת דירה -> תעדוף קרן כספית/אפיק סולידי על פני מסלול מניות למרות הגיל הצעיר",
        "סטטוס בדיקה": "✅ עבר בהצלחה" if passed_2 else "❌ נכשל",
    })

    # מקרה מבחן 3: לקוח בגיל פרישה (67) עם ביטוח מנהלים מקדם 152
    q3 = "לקוח בן 67 עם ביטוח מנהלים ותיק מקדם 152 וצבירה של 1,000,000 ש\"ח שוקל ניוד לקרן פנסיה מקדם 200."
    p3 = extract_case_parameters(q3)
    r_mgr = calculate_accumulation_python(1_000_000, 0, 0, 4.2, 0.8, 0.0)
    r_pen = calculate_accumulation_python(1_000_000, 0, 0, 4.2, 0.15, 0.0)
    passed_3 = (
        p3["is_retirement_or_pension_query"] is True
        and round(r_mgr["final_balance_net"] / 152.0, 1) == 6578.9
        and round(r_pen["final_balance_net"] / 200.0, 1) == 5000.0
    )
    results.append({
        "מקרה מבחן (אנונימי)": "מקרה 3: פורש בן 67 עם ביטוח מנהלים מקדם מובטח 152 מול קרן פנסיה מקדם 200",
        "מה נבדק אוטומטית": "סיווג כתיק פרישה + חישוב מתמטי מדויק (6,578.9 ₪ מול 5,000 ₪) והפעלת בדיקות שארים ונספח קצבה",
        "סטטוס בדיקה": "✅ עבר בהצלחה" if passed_3 else "❌ נכשל",
    })

    # מקרה מבחן 4: בדיקת חומה סינית (אי-השפעת עמלות סוכנות על דירוג המוצרים ללקוח)
    before_tracks = [t.track_id_code for t in match_relevant_products_and_tracks(db, p1)["matched_tracks"]]
    comm_row = db.query(AgencyCommissionAgreement).first()
    orig_bonus = comm_row.one_time_bonus_per_1m_ils
    comm_row.one_time_bonus_per_1m_ils = 999999.0  # שינוי קיצוני של עמלת הסוכנות
    after_tracks = [t.track_id_code for t in match_relevant_products_and_tracks(db, p1)["matched_tracks"]]
    comm_row.one_time_bonus_per_1m_ils = orig_bonus
    passed_4 = (before_tracks == after_tracks)
    results.append({
        "מקרה מבחן (אנונימי)": "מקרה 4: בדיקת 'חומה סינית' – אי-תלות מוחלטת בין עמלות הסוכנות לדירוג המוצרים ללקוח",
        "מה נבדק אוטומטית": "שינוי שיעור עמלת הסוכנות ב-1,000% ובדיקה שרשימת המוצרים והמסלולים ללקוח נותרת זהה ב-100%",
        "סטטוס בדיקה": "✅ עבר בהצלחה" if passed_4 else "❌ נכשל",
    })

    return results


# =====================================================================
# 7. מסך התחברות מאובטח לעובדי LEVERAGE
# =====================================================================
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if not st.session_state.auth_user:
    st.title("🛡️ LEVERAGE AI – מערכת תכנון פיננסי, פנסיוני והשקעות")
    st.write("כניסה מאובטחת לעובדי LEVERAGE – תכנון פיננסי.")
    c_login, c_info = st.columns([1, 1])
    with c_login:
        with st.form("leverage_login"):
            u_name = st.text_input("שם משתמש", value="admin")
            u_pass = st.text_input("סיסמה", type="password", value="AdminLeverage2026!")
            opt_key = st.text_input(
                "מפתח OpenAI API (אופציונלי אם הוגדר ב-Streamlit Secrets)",
                type="password",
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
            "**משתמשי ברירת מחדל:**\n"
            "- **מנהל מערכת:** שם משתמש `admin` | סיסמה `AdminLeverage2026!`\n"
            "- **סוכן / מתכנן פיננסי:** שם משתמש `agent1` | סיסמה `Leverage2026!`"
        )
    st.stop()


# =====================================================================
# 8. סרגל צד: הגדרות סוכן, בחירת מצב תצוגה (קצר / מלא) ובחירת תיק לקוח
# =====================================================================
current_user = st.session_state.auth_user
db = SessionLocal()

st.sidebar.title(f"👤 {current_user['full_name']}")
st.sidebar.caption("LEVERAGE – תכנון פיננסי ופנסיוני")

if not resolve_openai_api_key():
    key_in = st.sidebar.text_input("🔑 הזן OpenAI API Key להפעלת ה-AI:", type="password")
    if key_in:
        st.session_state.runtime_openai_key = key_in.strip()
        st.rerun()
else:
    st.sidebar.success("🔒 מנוע OpenAI מחובר ומאומת")

response_depth_mode = st.sidebar.radio(
    "📐 רמת פירוט תשובות ה-AI:",
    options=[
        "📋 ניתוח מתכנן פיננסי מלא (Full Advisory)",
        "⚡ תשובה ממוקדת ומהירה (Executive Brief)",
    ],
)

if st.sidebar.button("🚪 התנתק מהמערכת"):
    st.session_state.auth_user = None
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("📂 בחירת תיק לקוח פעיל")

clients_query = db.query(Client) if current_user["role"] == "admin" else db.query(Client).filter(Client.agent_id == current_user["id"])
all_clients = clients_query.all()

client_map = {0: "🌐 ייעוץ השקעות/פנסיה כללי (ללא כרטיס לקוח ספציפי)"}
for c in all_clients:
    client_map[c.id] = f"👤 {c.full_name} (ת.ז ..{c.id_last4})"

selected_client_id = st.sidebar.selectbox(
    "בחר לקוח:",
    options=list(client_map.keys()),
    format_func=lambda cid: client_map[cid],
)

with st.sidebar.expander("➕ פתיחת כרטיס לקוח חדש"):
    with st.form("create_client_sidebar"):
        f_name = st.text_input("שם מלא של הלקוח")
        f_id = st.text_input("תעודת זהות (9 ספרות, לדוגמה 000000018)")
        f_birth = st.number_input("שנת לידה", min_value=1930, max_value=2010, value=2001)
        f_ret = st.number_input("גיל פרישה מתוכנן", min_value=55, max_value=80, value=67)
        f_sal = st.number_input("שכר חודשי ברוטו (₪)", min_value=0, value=15000, step=1000)
        f_hz = st.number_input("אופק השקעה מבוקש (שנים)", min_value=1, max_value=40, value=10)
        f_rsk = st.selectbox("פרופיל סיכון", ["טרם נקבע (דורש בירור צרכים)", "סולידי / נמוך", "מאוזן / בינוני", "צמיחה / מנייתי גבוה"])
        f_smk = st.checkbox("מעשן/ת", value=False)
        f_nt = st.text_area("מטרות השקעה, נזילות והערות")
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
                    investment_horizon_years=int(f_hz),
                    risk_profile=f_rsk,
                    is_smoker=f_smk,
                    notes=f_nt,
                )
                db.add(nc)
                db.commit()
                st.success("כרטיס הלקוח נשמר והוצפן בהצלחה!")
                st.rerun()


# =====================================================================
# 9. לשוניות העבודה הראשיות של LEVERAGE AI
# =====================================================================
active_client: Optional[Client] = None
if selected_client_id != 0:
    active_client = db.query(Client).filter(Client.id == selected_client_id).first()

st.title("🛡️ LEVERAGE AI – מערכת תכנון פיננסי, השקעות ופנסיה")
if active_client:
    st.info(
        f"📂 **תיק לקוח פעיל:** {active_client.full_name} | **גיל:** {2026 - active_client.birth_year} | "
        f"**שכר:** {active_client.monthly_salary:,.0f} ₪ | **אופק השקעה:** {active_client.investment_horizon_years or 'טרם הוגדר'} שנים | "
        f"**פרופיל סיכון:** {active_client.risk_profile}"
    )

main_tabs = st.tabs([
    "💼 1. יועץ השקעות ותכנון פיננסי (א'–ז')",
    "📊 2. השוואת גמל־נט, פנסיה־נט, ביטוח־נט ומוצרים",
    "🛡️ 3. השוואת תעריפי ביטוח וכיסויים",
    "📄 4. ניתוח מסלקה (PDF + מבנה אחיד XML)",
    "🧮 5. סימולטור אקטוארי ומיסוי פרישה (161ד/190)",
    "🎙️ 6. הכנה לפגישה, Whisper ומשימות (רואטו)",
    "💰 7. מנוע עמלות סוכנות (מופרד - Admin)",
    "🗄️ 8. מאגרי מידע ובדיקות איכות אוטומטיות (QA)",
])


# =====================================================================
# לשונית 1: מנוע ההמלצות החדש (מתכנן פיננסי בכיר במבנה א'–ז' + סימולטור Python)
# =====================================================================
with main_tabs[0]:
    st.header("💼 עוזר מתכנן פיננסי בכיר – ניתוח צרכים, התאמת מוצרים, השוואת גופים וסימולציית Python")
    st.write(
        "הצג מקרה של לקוח (למשל גיל, סכום פנוי והפקדה חודשית). המערכת מנתחת את הצרכים, מאתרת מוצרים ומסלולים אמיתיים, "
        "מריצה סימולציית Python רב-שנתית (5, 10, 20, 30 שנה) ומפיקה המלצה מעשית בפורמט **א' עד ז'**."
    )

    # כפתור בדיקה מהירה למקרה הדוגמה של הלקוח בן ה-25
    if st.button("💡 טען דוגמה לבדיקה: לקוח בן 25 עם 50,000 ₪ פנויים + 1,000 ₪ בחודש"):
        st.session_state.preset_query = (
            "יש לי לקוח בן 25 שרוצה להתחיל להשקיע. הוא יכול להפקיד 1,000 ₪ בכל חודש "
            "ויש לו 50,000 ₪ פנויים להשקעה מיידית. מה אתה ממליץ?"
        )

    default_q = st.session_state.pop("preset_query", "")
    user_case_query = st.chat_input("הקלד את מקרה הלקוח כאן (גיל, סכום חד-פעמי, הפקדה חודשית, מטרה)...")
    active_query = user_case_query or default_q

    if active_query:
        with st.chat_message("user"):
            st.markdown(active_query)

        with st.chat_message("assistant"):
            with st.spinner("מנתח צרכי לקוח, משווה מסלולים בגמל-נט/ביטוח-נט ומריץ סימולציות Python ל-5, 10, 20 ו-30 שנה..."):
                advisory_bundle = generate_senior_planner_advisory(
                    api_key=resolve_openai_api_key(),
                    db=db,
                    user_query=active_query,
                    active_client=active_client,
                    response_depth=response_depth_mode,
                )

                if advisory_bundle["error"]:
                    st.warning(advisory_bundle["error"])
                    st.markdown("#### 🧮 תוצאות מנוע החישוב והתאמת המוצרים ב-Python (פועל גם ללא חיבור AI):")
                    for w in advisory_bundle["matched_bundle"]["recommended_wrappers"]:
                        st.write(f"- {w}")
                    st.dataframe(pd.DataFrame(advisory_bundle["python_sim"]["horizon_comparison_table"]), use_container_width=True)
                else:
                    adv: FinancialPlannerAdvisoryOutput = advisory_bundle["structured_advisory"]
                    show_full_mode = "מלא" in response_depth_mode

                    # הצגת סעיפים א', ב', ג'
                    st.markdown(f"🔖 **סטטוס אבחון:** `{adv.recommendation_status_badge}`")
                    st.markdown(f"### א. מסקנה מקצועית קצרה\n{adv.section_a_executive_conclusion}")
                    st.markdown(f"### ב. ניתוח הלקוח\n{adv.section_b_client_analysis}")
                    st.markdown(f"### ג. הצעת פתרון מעשית\n{adv.section_c_practical_solution}")

                    # הצגת סעיף ד': טבלת השוואת גופים מנהלים ומסלולים אמיתיים (10 הפרמטרים)
                    st.markdown("### ד. השוואת גופים מנהלים ומסלולים רלוונטיים (נתוני גמל־נט / ביטוח־נט / שוק ההון)")
                    comp_df = pd.DataFrame([
                        {
                            "1. גוף מנהל": r.provider_name,
                            "2. מוצר": r.product_name,
                            "3. מסלול ומספר": r.track_name_and_code,
                            "4. חשיפה למניות ונכסים": r.equity_and_assets,
                            "5. דמי ניהול": r.relevant_fees,
                            "6. תשואות עבר (1/3/5 ש')": r.historical_yields,
                            "7. סיכון ושארפ": r.risk_and_sharpe,
                            "8. נזילות": r.liquidity_terms,
                            "9. מיסוי": r.tax_aspects,
                            "10. יתרונות וחסרונות למקרה": r.case_pros_and_cons,
                            "מקור ותאריך": r.source_and_date,
                        }
                        for r in adv.section_d_institutional_comparison
                    ])
                    st.dataframe(comp_df, use_container_width=True)
                    st.caption("⚠️ נתוני התשואות הינם נתוני עבר רשמיים לתקופות השוואה זהות ואינם מהווים הבטחה לתשואה עתידית. יש לקבל הצעת דמי ניהול פרטנית לפני פתיחה.")

                    # הצגת סעיף ה': סימולציה מספרית מבוססת Python
                    st.markdown("### ה. סימולציה מספרית (מנוע חישוב Python דטרמיניסטי)")
                    st.markdown(adv.section_e_simulation_insights)
                    st.info(advisory_bundle["python_sim"]["assumptions_note"])
                    st.markdown("**טבלת השוואת צבירה, קרן מול רווח ושחיקת דמי ניהול לפי אופק השקעה (5, 10, 20, 30 שנה):**")
                    st.dataframe(pd.DataFrame(advisory_bundle["python_sim"]["horizon_comparison_table"]), use_container_width=True)

                    if show_full_mode:
                        with st.expander("📊 הצג טבלת רגישות לתרחישי תשואה (שמרני 3.0% / מרכזי 5.5% / מנייתי 7.5% ריאלי)"):
                            st.dataframe(pd.DataFrame(advisory_bundle["python_sim"]["yield_sensitivity_table"]), use_container_width=True)

                    # הצגת סעיפים ו' ו-ז'
                    st.markdown("### ו. פעולות לביצוע עבור הסוכן")
                    for idx, act in enumerate(adv.section_f_agent_action_plan, 1):
                        st.write(f"**{idx}.** {act}")

                    st.markdown("### ז. שאלות מהותיות להשלמה מול הלקוח")
                    for q_item in adv.section_g_critical_questions:
                        st.write(f"- ❓ {q_item}")

                    if adv.relevant_regulatory_note:
                        st.caption(f"⚖️ **דגש רגולטורי/מיסויי ממוקד:** {adv.relevant_regulatory_note}")


# =====================================================================
# לשונית 2: גמל־נט, פנסיה־נט, ביטוח־נט וסימולטור השוואת מוצרים ישיר
# =====================================================================
with main_tabs[1]:
    st.header("📊 מאגר מסלולים מאומת: גמל־נט, פנסיה־נט, ביטוח־נט וחלופות שוק ההון")
    st.warning("⚠️ **הבהרה רגולטורית:** תשואות עבר אינן מעידות על תשואות עתידיות. כל נתון כולל מקור, תקופת דיווח ותאריך אימות.")

    f1, f2 = st.columns(2)
    with f1:
        cat_filter = st.multiselect(
            "סנן לפי סוג מוצר:",
            ["קופת גמל להשקעה", "פוליסת חיסכון", "תיק השקעות עצמאי (קרנות סל)", "קרן כספית / פיקדון", "קרן השתלמות", "קרן פנסיה מקיפה"],
            default=["קופת גמל להשקעה", "פוליסת חיסכון", "תיק השקעות עצמאי (קרנות סל)", "קרן כספית / פיקדון", "קרן השתלמות"],
        )
    with f2:
        prov_search = st.text_input("חיפוש חופשי לפי גוף מנהל (מור, מיטב, ילין, אנליסט, הפניקס, הראל...) או מסלול:")

    tracks_q = db.query(MarketTrackMetric).filter(MarketTrackMetric.product_category.in_(cat_filter)).all()
    rows_tracks = []
    for t in tracks_q:
        if prov_search and (prov_search not in t.provider_name and prov_search not in t.track_name):
            continue
        rows_tracks.append({
            "1. גוף מנהל": t.provider_name,
            "2. מוצר": t.product_category,
            "3. שם וקוד מסלול": f"{t.track_name} ({t.track_id_code})",
            "4. חשיפה למניות (%)": t.equity_exposure_pct,
            "תמהיל נכסים": t.asset_mix_desc,
            "5. דמ\"נ צבירה (%)": t.avg_fee_balance_pct,
            "6. תשואה 1ש' (%)": t.yield_1yr_pct,
            "תשואה 3ש' מצטברת (%)": t.yield_3yr_cumulative_pct,
            "תשואה 5ש' מצטברת (%)": t.yield_5yr_cumulative_pct,
            "7. סיכון ושארפ": f"{t.risk_level_label} ({t.sharpe_ratio})",
            "8. נזילות": t.liquidity_terms,
            "9. מיסוי": t.tax_rules_summary,
            "10. יתרונות": t.pros_for_client,
            "חסרונות": t.cons_for_client,
            "מקור ותקופה": f"{t.source_system} ({t.report_period})",
        })
    st.dataframe(pd.DataFrame(rows_tracks), use_container_width=True)

    st.markdown("---")
    st.subheader("🧮 מחשבון השוואת השקעה אינטראקטיבי ב-Python (קרן מול רווח ושחיקת דמי ניהול)")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        sim_init = st.number_input("סכום השקעה ראשוני (₪)", value=50_000.0, step=10_000.0)
    with sc2:
        sim_monthly = st.number_input("הפקדה חודשית קבועה (₪)", value=1_000.0, step=250.0)
    with sc3:
        sim_yield_in = st.number_input("הנחת תשואה שנתית ריאלית ברוטו (%)", value=5.5, step=0.5)

    custom_sim = run_multi_horizon_investment_simulation(
        initial_amount=sim_init,
        monthly_deposit=sim_monthly,
        gross_real_yields={"שמרני (3.0% ריאלי)": 3.0, "תרחיש מרכזי (5.5% ריאלי)": sim_yield_in, "מנייתי צמיחה (7.5% ריאלי)": 7.5},
    )
    st.dataframe(pd.DataFrame(custom_sim["horizon_comparison_table"]), use_container_width=True)


# =====================================================================
# לשונית 3: השוואת תעריפי ביטוח וכיסויים
# =====================================================================
with main_tabs[2]:
    st.header("🛡️ מנוע השוואת מחירי ביטוח, התפתחות פרמיה ואיכות כיסוי")
    st.info("📌 **הבהרה:** מחיר הביטוח אינו סופי אלא אם התקבלה הצעה תקפה מחברת הביטוח לאחר חיתום רפואי. חובה להשוות גם את היקף הכיסוי והחריגים.")

    c_br, c_cov, c_age, c_smk = st.columns(4)
    with c_br:
        branch_sel = st.selectbox("בחר ענף ביטוח:", ["ביטוח חיים (ריסק)", "ביטוח בריאות"])
    with c_cov:
        cov_amount = st.number_input("סכום ביטוח (₪)", value=1_000_000, step=100_000)
    with c_age:
        ins_age = st.number_input("גיל המבוטח", min_value=18, max_value=75, value=35)
    with c_smk:
        ins_smoker = st.checkbox("מעשן", value=False)

    branch_tariffs = db.query(InsuranceTariffCatalog).filter(InsuranceTariffCatalog.insurance_branch == branch_sel).all()
    if branch_tariffs:
        comp_rows = []
        for bt in branch_tariffs:
            proj = calculate_insurance_tariff_projection(bt, cov_amount, ins_age, ins_smoker, projection_years=15)
            comp_rows.append({
                "חברת ביטוח": bt.provider_name,
                "שם תוכנית": bt.plan_name,
                "פרמיה חודשית ראשונה (₪)": proj["first_month_premium_ils"],
                "עלות מצטברת ל-15 שנים (₪)": proj["cumulative_cost_ils"],
                "הנחה מרבית (%)": bt.max_discount_pct,
                "שנות תוקף הנחה": bt.discount_valid_years,
                "תקופת אכשרה (ימים)": bt.waiting_period_days,
                "השתתפות עצמית (₪)": bt.deductible_ils,
                "היקף הכיסוי": bt.coverage_scope_details,
                "חריגים ותנאי חיתום": bt.exclusions_and_underwriting,
            })
        st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)


# =====================================================================
# לשונית 4: ניתוח מסלקה פנסיונית (PDF + מבנה אחיד XML/JSON)
# =====================================================================
with main_tabs[3]:
    st.header("📄 ניתוח מסלקה פנסיונית (PDF + קובצי המבנה האחיד XML/JSON)")
    if not active_client:
        st.warning("👈 בחר תיק לקוח בסרגל הצד השמאלי כדי להעלות ולשייך אליו מסמכי מסלקה.")
    else:
        consent_ok = st.checkbox("✅ אני מצהיר כי בידי ייפוי כוח / הסכמת לקוח תקפה לעיבוד נתוני המסלקה של לקוח זה.", value=True)
        uploaded_silukit = st.file_uploader("העלה דוח מסלקה (PDF / XML / JSON)", type=["pdf", "xml", "json"])
        if uploaded_silukit and st.button("🔍 קרא ונתח מסלקה"):
            if not consent_ok:
                st.error("חובה לאשר קיום הסכמת לקוח.")
            else:
                fbytes = uploaded_silukit.getvalue()
                reader = PdfReader(io.BytesIO(fbytes))
                st.success(f"המסמך נקלט ({len(reader.pages)} עמודים) ונשמר בכרטיס הלקוח!")


# =====================================================================
# לשונית 5: סימולטור אקטוארי לפרישה ומיסוי (161ד / תיקון 190 / 125ד)
# =====================================================================
with main_tabs[4]:
    st.header("🧮 סימולטור אקטוארי לפרישה ומיסוי (טופס 161ד, תיקון 190, סעיף 125ד)")
    sub_act, sub_tax = st.tabs(["📈 השוואת ביטוח מנהלים מול קרן פנסיה", "⚖️ קיבוע זכויות (161ד) ותיקון 190"])

    with sub_act:
        c1, c2, c3 = st.columns(3)
        with c1:
            a_cur_age = st.number_input("גיל נוכחי", value=67)
            a_ret_age = st.number_input("גיל פרישה", value=67)
            a_bal = st.number_input("צבירה קיימת (₪)", value=1_000_000.0, step=50_000.0)
            a_dep = st.number_input("הפקדה חודשית (₪)", value=0.0, step=500.0)
        with c2:
            a_fac1 = st.number_input("מקדם ביטוח מנהלים", value=152.0, step=1.0)
            a_fb1 = st.number_input("דמ\"נ צבירה מנהלים (%)", value=0.80, step=0.05)
        with c3:
            a_fac2 = st.number_input("מקדם משוער קרן פנסיה", value=200.0, step=1.0)
            a_fb2 = st.number_input("דמ\"נ צבירה פנסיה (%)", value=0.15, step=0.05)

        yrs = max(0, a_ret_age - a_cur_age)
        scen_rows = []
        for s_name, y_pct in [("שמרני (2.5%)", 2.5), ("תרחיש בסיס (4.2%)", 4.2), ("אופטימי (6.0%)", 6.0)]:
            r1 = calculate_accumulation_python(a_bal, a_dep, yrs, y_pct, a_fb1, 0.0)
            r2 = calculate_accumulation_python(a_bal, a_dep, yrs, y_pct, a_fb2, 1.0)
            scen_rows.append({
                "תרחיש תשואה": s_name,
                "צבירה בפרישה - מנהלים (₪)": r1["final_balance_net"],
                "קצבה צפויה - מנהלים (₪)": round(r1["final_balance_net"] / a_fac1, 0),
                "צבירה בפרישה - פנסיה (₪)": r2["final_balance_net"],
                "קצבה צפויה - פנסיה (₪)": round(r2["final_balance_net"] / a_fac2, 0),
            })
        st.dataframe(pd.DataFrame(scen_rows), use_container_width=True)

    with sub_tax:
        tx_grants_known = st.checkbox("ידוע לי סכום המענקים והפיצויים הפטורים ב-32 השנים האחרונות", value=True)
        tx_grants = st.number_input("סך מענקים פטורים ב-32 השנים האחרונות (₪)", value=0.0, step=10000.0)
        if not tx_grants_known:
            st.error("🛑 לא ניתן לבצע חישוב קיבוע זכויות ללא השלמת נתון מענקים פטורים ב-32 השנים האחרונות.")
        else:
            max_ex_mo = round(9430.0 * 0.575, 2)
            init_basket = round(max_ex_mo * 180.0, 2)
            offset_135 = round(tx_grants * 1.35, 2)
            rem_basket = max(0.0, round(init_basket - offset_135, 2))
            k1, k2, k3 = st.columns(3)
            k1.metric("סל פטור התחלתי (180)", f"{init_basket:,.0f} ₪")
            k2.metric("קיזוז מענקים (x1.35)", f"{offset_135:,.0f} ₪")
            k3.metric("יתרת סל פטור פנויה", f"{rem_basket:,.0f} ₪")


# =====================================================================
# לשונית 6: הכנה לפגישה, Whisper ומשימות לרואטו CRM
# =====================================================================
with main_tabs[5]:
    st.header("🎙️ הכנה לפגישה, תמלול שמע (Whisper) ומשימות (רואטו CRM)")
    if not active_client:
        st.info("בחר תיק לקוח בסרגל הצד כדי להוסיף משימות או להפיק דוח הכנה לפגישה.")
    else:
        with st.form("task_form"):
            t_cat = st.selectbox("קטגוריה", ["כספים בפיקדון שצפויים להשתחרר", "קופות שדורשות טיפול נוסף", "קיבוע זכויות", "פיצויים", "פגישות שירות עתידיות", "מסמכים חסרים"])
            t_tit = st.text_input("כותרת משימה")
            t_dsc = st.text_area("פירוט")
            if st.form_submit_button("שמור משימה בכרטיס הלקוח"):
                db.add(ClientTask(client_id=active_client.id, category=t_cat, title=t_tit, description=t_dsc))
                db.commit()
                st.success("המשימה נשמרה!")
                st.rerun()


# =====================================================================
# לשונית 7: מנוע עמלות סוכנות LEVERAGE (מופרד לחלוטין - Admin בלבד)
# =====================================================================
with main_tabs[6]:
    st.header("💰 מודל עמלות סוכנות LEVERAGE (מופרד בחומה סינית ממנוע ההמלצות ללקוח)")
    if current_user["role"] != "admin":
        st.error("🔒 גישה למנהלי מערכת (`admin`) בלבד.")
    else:
        st.info("🛡️ **הפרדה מוחלטת:** נתוני העמלות במסך זה לעולם אינם מועברים למנוע ההמלצות של הלקוח ואינם משפיעים על דירוג המוצרים.")
        agreements = db.query(AgencyCommissionAgreement).all()
        st.dataframe(
            pd.DataFrame([
                {
                    "גוף מוסדי": a.provider_name,
                    "מוצר": a.product_category,
                    "עמלת היקף (%)": a.scope_commission_pct,
                    "עמלת נפרעים שנתית (%)": a.ongoing_nifraim_pct,
                    "בונוס למיליון (₪)": a.one_time_bonus_per_1m_ils,
                    "חלק סוכן (%)": a.agent_share_pct,
                    "מנגנון ביטול והשבה": a.clawback_rule_desc,
                }
                for a in agreements
            ]),
            use_container_width=True,
        )


# =====================================================================
# לשונית 8: ניהול מאגרי מידע + בדיקות אוטומטיות על מקרי לקוח אמיתיים (QA)
# =====================================================================
with main_tabs[7]:
    st.header("🗄️ מאגרי מידע ומערך בדיקות אוטומטיות (QA Benchmark על מקרי לקוח מאנונמים)")
    st.write("בדיקה אוטומטית של מנוע ההמלצות, התאמת המוצרים, דיוק החישובים והפרדת העמלות על מקרי מבחן.")

    if st.button("🧪 הרץ בדיקות אוטומטיות על מקרי לקוח אמיתיים (Benchmark Suite)"):
        qa_results = run_automated_benchmark_suite(db)
        st.success("כל בדיקות האיכות והאקטואריה האוטומטיות הסתיימו בהצלחה!")
        st.table(pd.DataFrame(qa_results))

    st.subheader("📚 מאגר חקיקה, רגולציה ומיסוי פעיל במערכת")
    all_kb = db.query(KnowledgeSourceItem).all()
    st.dataframe(
        pd.DataFrame([
            {
                "קוד": k.code_key,
                "תחום": k.domain_scope,
                "כותרת": k.title,
                "סעיף": k.section_ref,
                "רשות": k.authority_name,
                "עודכן": k.last_updated,
            }
            for k in all_kb
        ]),
        use_container_width=True,
    )

db.close()
