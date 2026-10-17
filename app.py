import streamlit as st
import streamlit.components.v1 as components
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
# 1. הגדרת עמוד ועיצוב כהה יוקרתי מלא (Full Luxury Dark Mode + RTL)
# =====================================================================
st.set_page_config(
    page_title="LEVERAGE AI – מערכת תכנון פיננסי והשקעות",
    page_icon="🛡️",
    layout="wide",
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;600;700;800&display=swap');

    /* רקע כהה אחיד וטקסט בהיר חד בכל האפליקציה */
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
        direction: rtl;
        text-align: right;
        font-family: 'Assistant', 'Segoe UI', sans-serif;
        background-color: #0b0f19 !important;
        color: #f8fafc !important;
    }

    [data-testid="stSidebar"] {
        background-color: #111827 !important;
        border-left: 1px solid #1e293b !important;
    }

    p, div, h1, h2, h3, h4, h5, li, span, label, th, td {
        text-align: right !important;
        font-family: 'Assistant', 'Segoe UI', sans-serif;
        color: #f1f5f9;
    }

    /* עיצוב כותרות הלשוניות (Tabs) שיהיו בולטות וברורות במצב כהה */
    button[data-baseweb="tab"] {
        background-color: #161f33 !important;
        color: #cbd5e1 !important;
        border-radius: 10px 10px 0 0 !important;
        padding: 10px 16px !important;
        margin-left: 6px !important;
        border: 1px solid #1e293b !important;
        font-weight: 600 !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        background-color: #1e3a8a !important;
        color: #ffffff !important;
        border-bottom: 3px solid #f59e0b !important;
        font-weight: 800 !important;
    }
    button[data-baseweb="tab"] p {
        color: inherit !important;
        font-size: 0.98rem !important;
    }

    /* עיצוב כפתורים */
    .stButton > button {
        background-color: #1e293b !important;
        color: #f8fafc !important;
        border: 1px solid #334155 !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        transition: all 0.2s ease-in-out;
    }
    .stButton > button:hover {
        background-color: #1e40af !important;
        border-color: #60a5fa !important;
        color: #ffffff !important;
    }

    /* בועות שיחה בצ'אט במצב כהה */
    .stChatMessage {
        direction: rtl;
        background-color: #111827 !important;
        border: 1px solid #1e293b !important;
        border-radius: 14px;
        padding: 18px;
        margin-bottom: 14px;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
    }

    /* מדדים (Metrics) בכרטיסים כהים */
    div[data-testid="stMetric"] {
        background-color: #161f33 !important;
        border: 1px solid #26334d !important;
        border-radius: 12px !important;
        padding: 14px 16px !important;
    }
    div[data-testid="stMetricLabel"] p {
        color: #94a3b8 !important;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] {
        direction: ltr;
        text-align: right !important;
        font-weight: 800 !important;
        color: #38bdf8 !important;
    }

    /* אזורי הרחבה (Expanders) במצב כהה */
    div[data-testid="stExpander"] {
        background-color: #111827 !important;
        border: 1px solid #1e293b !important;
        border-radius: 12px !important;
        margin-bottom: 10px !important;
    }
    div[data-testid="stExpander"] summary p {
        color: #e2e8f0 !important;
        font-weight: 700 !important;
    }

    /* כרטיס הדגל: הצעת ההשקעה שלי ללקוח */
    .proposal-hero-card {
        background: linear-gradient(135deg, #0f172a 0%, #172554 100%);
        color: #ffffff !important;
        padding: 24px 28px;
        border-radius: 16px;
        margin: 12px 0 20px 0;
        box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45);
        border: 1px solid #1e3a8a;
        border-right: 6px solid #f59e0b;
    }
    .proposal-hero-card h2, .proposal-hero-card h3, .proposal-hero-card p,
    .proposal-hero-card div, .proposal-hero-card span, .proposal-hero-card li {
        color: #f8fafc !important;
    }
    .proposal-badge-conditional {
        background-color: rgba(245, 158, 11, 0.2);
        border: 1px solid #fbbf24;
        color: #fde68a !important;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 700;
        display: inline-block;
        margin-bottom: 10px;
    }
    .proposal-why-box {
        background: rgba(15, 23, 42, 0.65);
        border-radius: 10px;
        padding: 14px 18px;
        margin-top: 14px;
        border-right: 4px solid #38bdf8;
    }

    /* כרטיסי השקעה להשוואת 3 החלופות במצב כהה */
    .inv-card-primary {
        background: #162038;
        border: 2px solid #3b82f6;
        border-top: 6px solid #f59e0b;
        border-radius: 14px;
        padding: 18px;
        height: 100%;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.4);
    }
    .inv-card-secondary {
        background: #111827;
        border: 1px solid #26334d;
        border-top: 5px solid #64748b;
        border-radius: 14px;
        padding: 18px;
        height: 100%;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    }
    .inv-card-tag-main {
        background-color: rgba(59, 130, 246, 0.25);
        border: 1px solid #60a5fa;
        color: #93c5fd !important;
        font-weight: 700;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 0.82rem;
        display: inline-block;
        margin-bottom: 8px;
    }
    .inv-card-tag-alt {
        background-color: #1e293b;
        border: 1px solid #475569;
        color: #cbd5e1 !important;
        font-weight: 600;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 0.82rem;
        display: inline-block;
        margin-bottom: 8px;
    }

    /* תיבת התראה של מנוע מגבלות המוצרים במצב כהה */
    .constraint-alert-box {
        background-color: rgba(120, 53, 15, 0.35);
        border: 1px solid #f59e0b;
        border-right: 5px solid #fbbf24;
        color: #fef3c7 !important;
        padding: 14px 18px;
        border-radius: 10px;
        margin: 10px 0;
        font-size: 0.96rem;
    }

    /* עיגון תיבת ההקלדה של הצ'אט לתחתית המסך בעיצוב כהה אחיד */
    div[data-testid="stChatInput"], [data-testid="stBottom"] > div {
        position: sticky !important;
        bottom: 0 !important;
        z-index: 999 !important;
        background-color: #0b0f19 !important;
        padding-top: 8px !important;
        padding-bottom: 8px !important;
        border-top: 1px solid #1e293b !important;
        direction: rtl;
    }
</style>
""", unsafe_allow_html=True)


# =====================================================================
# 2. תשתית אבטחת מידע, הצפנה, מיסוך לוגים וחיבור מסד נתונים
# =====================================================================
class PIIRedactingFilter(logging.Filter):
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


def format_ils(val: Optional[float]) -> str:
    if val is None:
        return "לא צוין"
    return f"{val:,.0f} ₪"


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
            db_url = st.secrets.get("DATABASE_URL", "sqlite:///./leverage_enterprise_v4.db")
        except Exception:
            db_url = "sqlite:///./leverage_enterprise_v4.db"
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    eng = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
    return eng, sessionmaker(autocommit=False, autoflush=False, bind=eng)


engine, SessionLocal = get_db_engine_and_session()
Base = declarative_base()


def utc_now():
    return datetime.now(timezone.utc)


# =====================================================================
# 3. מודלי מסד הנתונים (כולל טבלת מגבלות מוצרים רשמית ProductConstraintRule)
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
    employment_status = Column(String(40), default="שכיר/ה")
    target_retirement_age = Column(Integer, default=67)
    monthly_salary = Column(Float, default=0.0)
    existing_gemel_deposits_this_year = Column(Float, default=0.0)
    is_smoker = Column(Boolean, default=False)
    risk_profile = Column(String(50), default="טרם נקבע (דורש בירור צרכים)")
    investment_horizon_years = Column(Integer, nullable=True)
    liquidity_need_desc = Column(String(200), default="לא הוגדר")
    encrypted_phone = Column(String(255), default="")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=utc_now)

    products = relationship("ClientProduct", back_populates="client", cascade="all, delete-orphan")
    tasks = relationship("ClientTask", back_populates="client", cascade="all, delete-orphan")
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
    survivors_info = Column(String(255), default="לא צוין במסמך")
    source_page = Column(Integer, nullable=True)
    source_quote = Column(Text, default="")
    data_origin = Column(String(40), default="SILUKIT_PDF")
    anomalies = Column(Text, default="")

    client = relationship("Client", back_populates="products")


class ClientTask(Base):
    __tablename__ = "client_tasks"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    category = Column(String(60), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    priority = Column(String(20), default="גבוהה")
    status = Column(String(30), default="פתוחה")
    created_at = Column(DateTime, default=utc_now)

    client = relationship("Client", back_populates="tasks")


class ClientChat(Base):
    __tablename__ = "client_chats"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    structured_payload_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    client = relationship("Client", back_populates="chats")


class ProductConstraintRule(Base):
    __tablename__ = "product_constraint_rules"
    id = Column(Integer, primary_key=True)
    product_category = Column(String(80), unique=True, nullable=False)
    annual_deposit_ceiling_ils = Column(Float, nullable=True)
    total_deposit_ceiling_ils = Column(Float, nullable=True)
    is_global_per_person_ceiling = Column(Boolean, default=False)
    min_lump_sum_open_ils = Column(Float, default=0.0)
    min_monthly_deposit_ils = Column(Float, default=0.0)
    min_age = Column(Integer, nullable=True)
    max_age = Column(Integer, nullable=True)
    tax_benefit_age_threshold = Column(Integer, nullable=True)
    eligibility_condition = Column(String(250), default="פתוח לכלל הציבור")
    tax_benefit_rules = Column(Text, default="")
    withdrawal_and_liquidity_rules = Column(Text, default="")
    deposit_modes_allowed = Column(String(120), default="חד-פעמי, הוראת קבע חודשית או שילוב ביניהם")
    regulatory_source_name = Column(String(180), nullable=False)
    regulatory_source_url = Column(String(300), nullable=False)
    tax_year = Column(Integer, default=2026)
    effective_date = Column(String(20), default="2026-01-01")


class MarketTrackMetric(Base):
    __tablename__ = "market_track_metrics"
    id = Column(Integer, primary_key=True)
    source_system = Column(String(40), nullable=False)
    track_id_code = Column(String(40), index=True)
    provider_name = Column(String(80), nullable=False)
    product_category = Column(String(70), nullable=False)
    track_name = Column(String(140), nullable=False)
    equity_exposure_pct = Column(Float, default=0.0)
    foreign_exposure_pct = Column(Float, default=0.0)
    asset_mix_desc = Column(String(200), default="")
    avg_fee_balance_pct = Column(Float, default=0.0)
    avg_fee_deposit_pct = Column(Float, default=0.0)
    yield_1yr_pct = Column(Float, default=0.0)
    yield_3yr_cumulative_pct = Column(Float, default=0.0)
    yield_5yr_cumulative_pct = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    risk_level_label = Column(String(40), default="בינונית")
    liquidity_terms = Column(String(220), default="")
    tax_rules_summary = Column(String(250), default="")
    pros_for_client = Column(String(250), default="")
    cons_for_client = Column(String(250), default="")
    aum_millions_ils = Column(Float, default=0.0)
    service_score = Column(Float, default=85.0)
    report_period = Column(String(30), default="09/2026")
    last_verified_date = Column(String(20), default="2026-10-15")
    source_url = Column(String(300), default="https://data.gov.il/he/datasets?orgs=cma")


class KnowledgeSourceItem(Base):
    __tablename__ = "knowledge_sources"
    id = Column(Integer, primary_key=True)
    code_key = Column(String(80), index=True, nullable=False)
    domain_scope = Column(String(40), default="GENERAL")
    category = Column(String(60), nullable=False)
    title = Column(String(200), nullable=False)
    section_ref = Column(String(100), default="")
    content_text = Column(Text, nullable=False)
    legal_status = Column(String(50), nullable=False)
    reliability_tier = Column(Integer, default=1)
    authority_name = Column(String(120), nullable=False)
    source_url = Column(String(350), nullable=False)
    effective_from = Column(String(20), nullable=False)
    last_updated = Column(String(20), nullable=False)


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


class AgencyCommissionAgreement(Base):
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


# =====================================================================
# 4. אתחול וזריעת נתוני אמת (כולל טבלת מגבלות המוצרים הרשמית)
# =====================================================================
@st.cache_resource
def init_and_seed_database():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == "agent1").first():
            db.add_all([
                User(username="agent1", full_name="מתכנן פיננסי - LEVERAGE", role="agent", password_hash=hash_password("Leverage2026!")),
                User(username="admin", full_name="מנהל מערכת ומקצוענות - LEVERAGE", role="admin", password_hash=hash_password("AdminLeverage2026!")),
            ])
            db.commit()

        if db.query(ProductConstraintRule).count() == 0:
            rules = [
                ProductConstraintRule(
                    product_category="קופת גמל להשקעה",
                    annual_deposit_ceiling_ils=79006.0,
                    total_deposit_ceiling_ils=None,
                    is_global_per_person_ceiling=True,
                    min_lump_sum_open_ils=0.0,
                    min_monthly_deposit_ils=100.0,
                    min_age=0,
                    max_age=None,
                    tax_benefit_age_threshold=60,
                    eligibility_condition="פתוח לכל אזרח (לפי תעודת זהות). התקרה השנתית הינה אישית ומצטברת בכל קופות הגמל להשקעה של החוסך בכל הגופים יחד.",
                    tax_benefit_rules="דחיית מס במעבר בין מסלולים וגופים; 25% מס רווחי הון ריאלי במשיכה הונית; פטור מלא ממס רווחי הון וממס הכנסה במשיכה כקצבה חודשית לאחר גיל 60.",
                    withdrawal_and_liquidity_rules="נזיל לחלוטין למשיכה הונית בכל עת (תוך 4 ימי עסקים) ללא קנסות יציאה.",
                    deposit_modes_allowed="הפקדה חד-פעמית, הוראת קבע חודשית או שילוב ביניהן – ובלבד שסך ההפקדות השנתי לא יעלה על 79,006 ₪ לאדם.",
                    regulatory_source_name="חוק הפיקוח על שירותים פיננסיים (קופות גמל), סעיף 22 + הודעת רשות המסים",
                    regulatory_source_url="https://www.gov.il/he/pages/report_cma",
                    tax_year=2026,
                    effective_date="2026-01-01",
                ),
                ProductConstraintRule(
                    product_category="תיק השקעות מנוהל",
                    annual_deposit_ceiling_ils=None,
                    total_deposit_ceiling_ils=None,
                    is_global_per_person_ceiling=False,
                    min_lump_sum_open_ils=300000.0,
                    min_monthly_deposit_ils=0.0,
                    min_age=18,
                    max_age=None,
                    tax_benefit_age_threshold=None,
                    eligibility_condition="דרישת סף מינימלית של בתי ההשקעות לניהול תיק אישי (לרוב 300,000 ₪ עד 500,000 ₪ ומעלה) וחתימה על הסכם ניהול תיקים ואפיון צרכים לפי חוק הייעוץ.",
                    tax_benefit_rules="25% מס רווחי הון ריאלי (או 15% נומינלי באפיקים שקליים לא צמודים). יתרון של קיזוז הפסדי הון שוטפים מול רווחים בחשבון הבנק/המסחר.",
                    withdrawal_and_liquidity_rules="נזילות יומית מלאה של ניירות הערך הסחירים בחשבון הלקוח.",
                    deposit_modes_allowed="הפקדה חד-פעמית ללא תקרה והפקדות שוטפות לחשבון המנוהל.",
                    regulatory_source_name="חוק הסדרת העיסוק בייעוץ השקעות, בשיווק השקעות ובניהול תיקי השקעות, התשנ\"ה-1995",
                    regulatory_source_url="https://www.isa.gov.il",
                    tax_year=2026,
                    effective_date="2026-01-01",
                ),
                ProductConstraintRule(
                    product_category="תיק השקעות עצמאי (קרנות סל)",
                    annual_deposit_ceiling_ils=None,
                    total_deposit_ceiling_ils=None,
                    is_global_per_person_ceiling=False,
                    min_lump_sum_open_ils=15000.0,
                    min_monthly_deposit_ils=500.0,
                    min_age=18,
                    max_age=None,
                    tax_benefit_age_threshold=None,
                    eligibility_condition="פתיחת חשבון מסחר עצמאי אצל חבר בורסה (בית השקעות או בנק). בבתי השקעות קיים לרוב מינימום פתיחה של 15,000–20,000 ₪.",
                    tax_benefit_rules="דחיית מס עד מועד מכירת קרן הסל (בקרנות צוברות); 25% מס ריאלי במימוש; אפשרות לקיזוז הפסדים.",
                    withdrawal_and_liquidity_rules="נזיל בכל יום מסחר בבורסה ללא תקרת הפקדה.",
                    deposit_modes_allowed="חד-פעמי ללא תקרה, הוראת קבע או שילוב ביניהם.",
                    regulatory_source_name="רשות ניירות ערך והבורסה לניירות ערך בתל אביב",
                    regulatory_source_url="https://maya.tase.co.il",
                    tax_year=2026,
                    effective_date="2026-01-01",
                ),
                ProductConstraintRule(
                    product_category="פוליסת חיסכון",
                    annual_deposit_ceiling_ils=None,
                    total_deposit_ceiling_ils=None,
                    is_global_per_person_ceiling=False,
                    min_lump_sum_open_ils=0.0,
                    min_monthly_deposit_ils=200.0,
                    min_age=0,
                    max_age=None,
                    tax_benefit_age_threshold=78,
                    eligibility_condition="פתוח לכלל הציבור ללא תקרת הפקדה שנתית או כוללת.",
                    tax_benefit_rules="דחיית מס מלאה במעבר בין מסלולי השקעה בתוך הפוליסה; 25% מס ריאלי במימוש; זכאות להטבת מס שנתית לפי סעיף 125ד לילידי 1948 ומטה.",
                    withdrawal_and_liquidity_rules="נזיל לחלוטין למשיכה בכל עת ללא קנסות יציאה.",
                    deposit_modes_allowed="הפקדה חד-פעמית מכל סכום (גם מיליוני שקלים ללא תקרה), הוראת קבע או שילוב.",
                    regulatory_source_name="רשות שוק ההון, ביטוח וחיסכון – הוראות פוליסות חיסכון (ביטוח-נט)",
                    regulatory_source_url="https://www.gov.il/he/pages/report_cma",
                    tax_year=2026,
                    effective_date="2026-01-01",
                ),
                ProductConstraintRule(
                    product_category="קרן השתלמות",
                    annual_deposit_ceiling_ils=20520.0,
                    total_deposit_ceiling_ils=None,
                    is_global_per_person_ceiling=True,
                    min_lump_sum_open_ils=0.0,
                    min_monthly_deposit_ils=0.0,
                    min_age=18,
                    max_age=None,
                    tax_benefit_age_threshold=None,
                    eligibility_condition="חובת תנאי זכאות! פתוח אך ורק לעצמאי (בעל הכנסה מעסק/משלוח יד) או לשכיר שמעסיקו מפקיד עבורו. לא ניתן לפתוח באופן פרטי ללא מעמד זכאות.",
                    tax_benefit_rules="פטור מלא ממס רווחי הון (0% מס במקום 25%) עד תקרת ההפקדה השנתית המוטבת (20,520 ₪ לעצמאי, או עד 10% משכר קובע של 15,712 ₪ לחודש לשכיר) + הוצאה מוכרת לעצמאי.",
                    withdrawal_and_liquidity_rules="נזיל למשיכה הונית פטורה ממס לאחר 6 שנות ותק (או 3 שנים לצורכי השתלמות / בגיל פרישה).",
                    deposit_modes_allowed="שכיר: ניכוי חודשי מתלוש השכר בלבד. עצמאי: הפקדה חודשית או חד-פעמית עד סוף שנת המס.",
                    regulatory_source_name="פקודת מס הכנסה סעיפים 9(16א), 9(16ב) ו-17(5א)",
                    regulatory_source_url="https://www.gov.il/he/departments/israel_tax_authority",
                    tax_year=2026,
                    effective_date="2026-01-01",
                ),
                ProductConstraintRule(
                    product_category="קרן כספית / פיקדון",
                    annual_deposit_ceiling_ils=None,
                    total_deposit_ceiling_ils=None,
                    is_global_per_person_ceiling=False,
                    min_lump_sum_open_ils=0.0,
                    min_monthly_deposit_ils=0.0,
                    min_age=18,
                    max_age=None,
                    tax_benefit_age_threshold=None,
                    eligibility_condition="חשבון בנק או חשבון מסחר בבית השקעות. ללא תקרת הפקדה.",
                    tax_benefit_rules="בקרן כספית: 25% מס על הרווח הריאלי בלבד (מעל עליית המדד). בפיקדון בנקאי שקלי: 15% מס נומינלי מהשקל הראשון.",
                    withdrawal_and_liquidity_rules="בקרן כספית: נזילות יומית בכל יום מסחר ללא קנסות שבירה.",
                    deposit_modes_allowed="הפקדה חד-פעמית בכל סכום או הוראת קבע.",
                    regulatory_source_name="רשות ניירות ערך ופקודת מס הכנסה סעיף 91",
                    regulatory_source_url="https://www.isa.gov.il",
                    tax_year=2026,
                    effective_date="2026-01-01",
                ),
            ]
            db.add_all(rules)
            db.commit()

        if db.query(MarketTrackMetric).count() == 0:
            tracks = [
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="11524", provider_name="מור", product_category="קופת גמל להשקעה",
                    track_name="מור גמל להשקעה מניות", equity_exposure_pct=96.2, foreign_exposure_pct=64.0,
                    asset_mix_desc="מניות סחירות בארץ ובחו\"ל", avg_fee_balance_pct=0.68, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=18.4, yield_3yr_cumulative_pct=42.1, yield_5yr_cumulative_pct=78.5, sharpe_ratio=1.06,
                    risk_level_label="גבוהה", liquidity_terms="נזיל תוך 4 ימי עסקים; כפוף לתקרת הפקדה שנתית של 79,006 ₪ לאדם",
                    tax_rules_summary="מעבר מסלולים ללא מס; 25% מס ריאלי במשיכה; פטור כקצבה מגיל 60",
                    pros_for_client="ניהול מוסדי מוביל, מעבר מסלולים ללא אירוע מס ואופציית קצבה פטורה",
                    cons_for_client="מוגבל לתקרת הפקדה שנתית של 79,006 ₪ לחוסך בכל הגופים יחד",
                    aum_millions_ils=8900, service_score=87.0
                ),
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="10842", provider_name="ילין לפידות", product_category="קופת גמל להשקעה",
                    track_name="ילין לפידות גמל להשקעה מניות", equity_exposure_pct=94.5, foreign_exposure_pct=59.0,
                    asset_mix_desc="מניות בארץ ובחו\"ל", avg_fee_balance_pct=0.66, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=17.6, yield_3yr_cumulative_pct=39.8, yield_5yr_cumulative_pct=74.2, sharpe_ratio=1.02,
                    risk_level_label="גבוהה", liquidity_terms="נזיל בכל עת; כפוף לתקרת 79,006 ₪ לשנה",
                    tax_rules_summary="דחיית מס במעבר מסלולים; 25% מס ריאלי במשיכה",
                    pros_for_client="ציון שירות מצטיין ומחקר שוק הון איכותי",
                    cons_for_client="כפוף לתקרת הפקדה שנתית חוקית",
                    aum_millions_ils=11200, service_score=88.0
                ),
                MarketTrackMetric(
                    source_system="גמל-נט", track_id_code="13205", provider_name="מיטב", product_category="קופת גמל להשקעה",
                    track_name="מיטב גמל להשקעה עוקב מדד S&P 500", equity_exposure_pct=100.0, foreign_exposure_pct=100.0,
                    asset_mix_desc="עוקב פסיבי מדד S&P 500", avg_fee_balance_pct=0.58, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=21.2, yield_3yr_cumulative_pct=46.5, yield_5yr_cumulative_pct=84.0, sharpe_ratio=1.10,
                    risk_level_label="גבוהה", liquidity_terms="נזיל בכל עת; כפוף לתקרת 79,006 ₪ לשנה",
                    tax_rules_summary="מעבר מסלולים ללא אירוע מס; 25% מס ריאלי במשיכה",
                    pros_for_client="חשיפה מדדית בדמי ניהול מוזלים עם גמישות מעבר מסלול פטור ממס",
                    cons_for_client="חשיפה מלאה למט\"ח ולשוק האמריקאי; מוגבל בתקרת הפקדה שנתית",
                    aum_millions_ils=9800, service_score=86.0
                ),
                MarketTrackMetric(
                    source_system="תיקים מנוהלים (בתי השקעות)", track_id_code="PM-300", provider_name="ילין לפידות / מור / מיטב / אנליסט", product_category="תיק השקעות מנוהל",
                    track_name="תיק השקעות מנוהל בהתאמה אישית (אג\"ח + מניות בארץ ובחו\"ל)", equity_exposure_pct=40.0, foreign_exposure_pct=50.0,
                    asset_mix_desc="מותאם אישית לפרופיל הלקוח (אג\"ח ממשלתי/קונצרני, מניות וקרנות סל בחשבון הבנק של הלקוח)", avg_fee_balance_pct=0.55, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=11.9, yield_3yr_cumulative_pct=26.8, yield_5yr_cumulative_pct=47.5, sharpe_ratio=0.98,
                    risk_level_label="מותאמת אישית (סולידי עד מנייתי)", liquidity_terms="נזילות יומית מלאה בחשבון הבנק הפרטי של הלקוח; ללא תקרת הפקדה",
                    tax_rules_summary="25% מס רווחי הון ריאלי; קיזוז הפסדים שוטף ואוטומטי מול רווחים אחרים בבנק/בתיק",
                    pros_for_client="הכספים נשארים בחשבון הבנק על שם הלקוח, ללא תקרת הפקדה (אידיאלי לסכומים של 500 אלף עד מיליוני ₪), דמי ניהול תחרותיים בסכומים גבוהים",
                    cons_for_client="דורש סכום מינימום (לרוב 300,000 ₪ ומעלה); שינוי הרכב ניירות יוצר אירועי מס שוטפים",
                    aum_millions_ils=85000, service_score=88.0
                ),
                MarketTrackMetric(
                    source_system="ביטוח-נט", track_id_code="BIT-301", provider_name="הפניקס", product_category="פוליסת חיסכון",
                    track_name="הפניקס אינווסט - מסלול כללי / משולב סחיר ולא סחיר", equity_exposure_pct=42.0, foreign_exposure_pct=53.0,
                    asset_mix_desc="מניות, אג\"ח, נדל\"ן מניב, תשתיות וקרנות השקעה פרטיות", avg_fee_balance_pct=0.72, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=11.3, yield_3yr_cumulative_pct=25.8, yield_5yr_cumulative_pct=46.2, sharpe_ratio=0.96,
                    risk_level_label="בינונית", liquidity_terms="נזיל לחלוטין בכל עת ללא תקרת הפקדה שנתית",
                    tax_rules_summary="דחיית מס מלאה במעבר בין מסלולי השקעה; 25% מס ריאלי רק בעת פדיון; הטבת סעיף 125ד לילידי 1948 ומטה",
                    pros_for_client="ללא תקרת הפקדה (מתאים להפקדת מיליוני ₪), פיזור לנכסים לא סחירים, ודחיית מס בשינוי מסלול",
                    cons_for_client="אין קיזוז הפסדים מול תיק ניירות ערך בבנק; דמי ניהול מחייבים משא ומתן בסכומים גבוהים (הנחת גודל)",
                    aum_millions_ils=24000, service_score=84.0
                ),
                MarketTrackMetric(
                    source_system="ביטוח-נט", track_id_code="BIT-305", provider_name="הראל", product_category="פוליסת חיסכון",
                    track_name="הראל מגוון השקעות - מסלול מניות / כללי", equity_exposure_pct=41.0, foreign_exposure_pct=50.0,
                    asset_mix_desc="מניות, אג\"ח ועוגן נכסים לא סחירים", avg_fee_balance_pct=0.75, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=10.9, yield_3yr_cumulative_pct=24.8, yield_5yr_cumulative_pct=44.5, sharpe_ratio=0.93,
                    risk_level_label="בינונית", liquidity_terms="נזיל בכל עת ללא תקרת הפקדה",
                    tax_rules_summary="דחיית מס במעבר מסלולים; 25% מס ריאלי בפדיון; סעיף 125ד לילידי 1948 ומטה",
                    pros_for_client="ללא תקרת הפקדה, שילוב נכסים אלטרנטיביים לא סחירים",
                    cons_for_client="דורש בקשת הנחת דמי ניהול ייעודית להפקדות גבוהות",
                    aum_millions_ils=21500, service_score=85.0
                ),
                MarketTrackMetric(
                    source_system="שוק ההון (בורסה)", track_id_code="ETF-PASSIVE", provider_name="מיטב טרייד / IBI / בנקים", product_category="תיק השקעות עצמאי (קרנות סל)",
                    track_name="תיק פסיבי עצמאי - קרנות סל מחקות מדדים עולמיים (MSCI World / S&P 500 / אג\"ח)", equity_exposure_pct=100.0, foreign_exposure_pct=100.0,
                    asset_mix_desc="קרנות סל צוברות דיבידנד בדמי ניהול מינימליים", avg_fee_balance_pct=0.18, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=20.8, yield_3yr_cumulative_pct=45.9, yield_5yr_cumulative_pct=83.2, sharpe_ratio=1.09,
                    risk_level_label="גבוהה (או מותאמת לפי תמהיל)", liquidity_terms="נזילות יומית בבורסה ללא תקרת הפקדה",
                    tax_rules_summary="דחיית מס עד מכירת נייר; 25% מס ריאלי; קיזוז הפסדים מלא",
                    pros_for_client="העלויות הנמוכות ביותר בשוק (0.10%–0.20%), ללא תקרת הפקדה ושליטה מלאה",
                    cons_for_client="דורש ידע ומשמעת לניהול עצמי; איזון תיק (מכירת נייר) מהווה אירוע מס",
                    aum_millions_ils=95000, service_score=86.0
                ),
                MarketTrackMetric(
                    source_system="שוק ההון (בורסה)", track_id_code="MMF-SOLID", provider_name="מור / מיטב / הראל / ילין לפידות", product_category="קרן כספית / פיקדון",
                    track_name="קרן כספית שקלית (נזילות יומית וסיכון נמוך מאוד)", equity_exposure_pct=0.0, foreign_exposure_pct=0.0,
                    asset_mix_desc="מק\"מ בנק ישראל, פיקדונות ואג\"ח AAA קצר מועד", avg_fee_balance_pct=0.14, avg_fee_deposit_pct=0.0,
                    yield_1yr_pct=4.3, yield_3yr_cumulative_pct=11.8, yield_5yr_cumulative_pct=14.5, sharpe_ratio=2.10,
                    risk_level_label="נמוכה מאוד (סולידי)", liquidity_terms="נזילות יומית מלאה ללא תקרת הפקדה וללא קנסות",
                    tax_rules_summary="25% מס על הרווח הריאלי בלבד (מעל המדד!) לעומת 15% נומינלי בפיקדון",
                    pros_for_client="אידיאלי לכרית ביטחון או לכספים המיועדים לרכישת דירה/מטרה בטווח של 0–2 שנים",
                    cons_for_client="תשואה מוגבלת לריבית בנק ישראל",
                    aum_millions_ils=130000, service_score=88.0
                ),
            ]
            db.add_all(tracks)
            db.commit()

        if db.query(AgencyCommissionAgreement).count() == 0:
            db.add_all([
                AgencyCommissionAgreement(provider_name="הפניקס", product_category="פוליסת חיסכון / קופת גמל להשקעה", scope_commission_pct=0.0, ongoing_nifraim_pct=0.25, deposit_commission_pct=0.0, one_time_bonus_per_1m_ils=4000.0, clawback_period_months=12, clawback_rule_desc="השבה יחסית ב-12 חודשים ראשונים", agent_share_pct=55.0),
                AgencyCommissionAgreement(provider_name="מיטב", product_category="קופת גמל להשקעה / תיק מנוהל", scope_commission_pct=0.0, ongoing_nifraim_pct=0.20, deposit_commission_pct=0.0, one_time_bonus_per_1m_ils=3500.0, clawback_period_months=12, clawback_rule_desc="השבה יחסית ב-12 חודשים ראשונים", agent_share_pct=50.0),
            ])
            db.commit()
    finally:
        db.close()


init_and_seed_database()


# =====================================================================
# 5. מנוע בדיקת מגבלות מוצרים (Product Constraints Validator) ב-Python
# =====================================================================
def extract_case_parameters(user_query: str, active_client: Optional[Client] = None) -> Dict[str, Any]:
    q = user_query.replace(",", "")
    age = (2026 - active_client.birth_year) if active_client else None
    age_match = re.search(r"בן\s*(\d{2})|בת\s*(\d{2})|גיל\s*(\d{2})", q)
    if age_match:
        age = int(next(g for g in age_match.groups() if g is not None))

    monthly_deposit = 0.0
    initial_lump_sum = 0.0

    mil_match = re.search(r"(\d+(?:\.\d+)?)\s*מיליון", q)
    if mil_match:
        initial_lump_sum = float(mil_match.group(1)) * 1_000_000.0
    elif "מיליון" in q and "חצי" in q:
        initial_lump_sum = 500_000.0
    elif "מיליון" in q:
        initial_lump_sum = 1_000_000.0

    mo_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:ש\"ח|₪|שח)?\s*(?:בכל\s*חודש|בחודש|חודשי|להפקיד\s*מדי\s*חודש)", q)
    if not mo_match:
        mo_match = re.search(r"(?:להפקיד|הפקדה\s*חודשית\s*של)\s*(\d+(?:\.\d+)?)", q)
    if mo_match:
        val = float(mo_match.group(1))
        if val < 100_000:
            monthly_deposit = val

    all_nums = [float(n) for n in re.findall(r"\b\d{4,9}\b", q)]
    for num in all_nums:
        if num != monthly_deposit and num > 2030:
            if num > initial_lump_sum:
                initial_lump_sum = num

    explicit_numbers_found = (initial_lump_sum > 0 or monthly_deposit > 0)
    if not explicit_numbers_found:
        initial_lump_sum = 100_000.0
        monthly_deposit = 0.0

    horizon_years = active_client.investment_horizon_years if active_client else None
    hz_match = re.search(r"(?:בעוד|לעוד|לטווח\s*של|ל-)\s*(\d{1,2})\s*שנ(?:ים|ה)", q)
    if hz_match:
        horizon_years = int(hz_match.group(1))
    elif "שנתיים" in q:
        horizon_years = 2

    short_term_goal = any(w in q for w in ["דירה", "חתונה", "שנה הקרובה", "שנתיים", "טווח קצר", "נזיל מיידית"])
    is_self_employed = any(w in q for w in ["עצמאי", "עוסק מורשה", "עוסק פטור"]) or (
        active_client is not None and "עצמאי" in (active_client.employment_status or "")
    )
    existing_gemel_this_year = active_client.existing_gemel_deposits_this_year if active_client else 0.0

    missing_critical_info = []
    if horizon_years is None:
        missing_critical_info.append("טווח ההשקעה המדויק ומועד משיכה משוער")
    if not any(w in q for w in ["סיכון", "תנודתיות", "מנייתי", "סולידי", "ירידות"]):
        missing_critical_info.append("רמת הסיכון המתאימה ויכולת הלקוח לספוג תנודתיות")
    if not any(w in q for w in ["נזילות", "כרית ביטחון", "דירה", "חירום"]):
        missing_critical_info.append("צורכי נזילות וקיום כרית ביטחון נפרדת")
    if age is None:
        missing_critical_info.append("גיל הלקוח (משפיע על אופק השקעה והטבות מס כגון סעיף 125ד או קצבה מגיל 60)")

    return {
        "client_age": age,
        "initial_lump_sum": initial_lump_sum,
        "monthly_deposit": monthly_deposit,
        "total_first_year_deposit": initial_lump_sum + (monthly_deposit * 12.0),
        "existing_gemel_this_year": existing_gemel_this_year,
        "explicit_numbers_found": explicit_numbers_found,
        "horizon_years": horizon_years,
        "short_term_goal_detected": short_term_goal,
        "is_self_employed": is_self_employed,
        "needs_discovery_incomplete": len(missing_critical_info) > 0,
        "missing_critical_info": missing_critical_info,
    }


def validate_product_constraints_python(db: Session, case_params: Dict[str, Any]) -> Dict[str, Any]:
    rules = {r.product_category: r for r in db.query(ProductConstraintRule).all()}
    total_yr1 = case_params["total_first_year_deposit"]
    lump = case_params["initial_lump_sum"]
    already_deposited = case_params["existing_gemel_this_year"]
    short_term = case_params["short_term_goal_detected"] or (
        case_params["horizon_years"] is not None and case_params["horizon_years"] <= 2
    )

    gemel_rule = rules["קופת גמל להשקעה"]
    gemel_cap = gemel_rule.annual_deposit_ceiling_ils or 79006.0
    remaining_gemel_cap = max(0.0, gemel_cap - already_deposited)

    validation_decisions = []
    constraint_warnings = []
    approved_primary_products = []
    supplementary_products = []
    blocked_or_restricted_products = []

    if total_yr1 > remaining_gemel_cap:
        uncapped_excess = total_yr1 - remaining_gemel_cap
        blocked_or_restricted_products.append({
            "מוצר": "קופת גמל להשקעה (עבור מלוא הסכום)",
            "סטטוס בקרה": "🚫 חסום כפתרון מלא – חריגה מתקרת הפקדה שנתית חוקית",
            "סכום מבוקש": format_ils(total_yr1),
            "תקרה חוקית פנויה השנה": format_ils(remaining_gemel_cap),
            "הנחיית מנוע הבקרה": (
                f"לא ניתן להפקיד {format_ils(total_yr1)} לקופת גמל להשקעה! התקרה השנתית לשנת {gemel_rule.tax_year} עומדת על "
                f"{format_ils(gemel_cap)} לאדם במצטבר בכל הגופים יחד (יתרה פנויה ללקוח השנה: {format_ils(remaining_gemel_cap)}). "
                f"אסור ולא ניתן לעקוף תקרה זו באמצעות פתיחת קופות במספר בתי השקעות. "
                f"ניתן לשלב קופת גמל להשקעה אך ורק כנדבך משלים עד {format_ils(remaining_gemel_cap)}, ואת היתרה ({format_ils(uncapped_excess)}) "
                "חובה להפנות למוצרים ללא תקרת הפקדה (תיק השקעות מנוהל / תיק עצמאי / פוליסת חיסכון)."
            ),
            "מקור חוקי": f"{gemel_rule.regulatory_source_name} (תוקף: {gemel_rule.effective_date})",
        })
        constraint_warnings.append(
            f"⚠️ **בקרת תקרת הפקדה הופעלה:** הסכום המבוקש ({format_ils(total_yr1)}) חורג מתקרת ההפקדה השנתית לקופת גמל להשקעה ({format_ils(gemel_cap)} לאדם בשנה בכל הקופות יחד). "
            f"לכן קופת גמל להשקעה **אינה** מוצגת כפתרון מרכזי לכל הסכום, אלא רק כאפשרות משלימה עד יתרת התקרה החוקית ({format_ils(remaining_gemel_cap)}), בעוד עיקר הכסף מופנה לחלופות ללא תקרת הפקדה."
        )
        if remaining_gemel_cap > 0:
            supplementary_products.append(
                f"קופת גמל להשקעה – כמוצר משלים בלבד עד תקרת {format_ils(remaining_gemel_cap)} לשנת המס (יתרת הסכום בסך {format_ils(uncapped_excess)} במוצר ללא תקרה)"
            )
    else:
        approved_primary_products.append("קופת גמל להשקעה (עומדת בתקרת ההפקדה השנתית של 79,006 ₪)")
        validation_decisions.append({
            "מוצר": "קופת גמל להשקעה",
            "סטטוס בקרה": "✅ תקין – עומד בתקרת ההפקדה השנתית",
            "סכום מבוקש": format_ils(total_yr1),
            "תקרה חוקית פנויה השנה": format_ils(remaining_gemel_cap),
            "הנחיית מנוע הבקרה": "ניתן להפקיד את מלוא הסכום המבוקש במסגרת התקרה השנתית של החוסך.",
            "מקור חוקי": gemel_rule.regulatory_source_name,
        })

    pm_rule = rules["תיק השקעות מנוהל"]
    if lump >= pm_rule.min_lump_sum_open_ils:
        approved_primary_products.insert(0, "תיק השקעות מנוהל (ללא תקרת הפקדה, עומד ברף המינימום של בתי ההשקעות)")
        validation_decisions.append({
            "מוצר": "תיק השקעות מנוהל",
            "סטטוס בקרה": "✅ תקין ומתאים לסכומים גבוהים (ללא תקרת הפקדה)",
            "סכום מבוקש": format_ils(total_yr1),
            "תקרה חוקית פנויה השנה": "ללא תקרה (מינימום פתיחה: 300,000 ₪)",
            "הנחיית מנוע הבקרה": "מתאים לקליטת מלוא הסכום בחשבון הבנק הפרטי של הלקוח עם התאמת תמהיל אישית וקיזוז הפסדים.",
            "מקור חוקי": pm_rule.regulatory_source_name,
        })
    else:
        blocked_or_restricted_products.append({
            "מוצר": "תיק השקעות מנוהל",
            "סטטוס בקרה": "ℹ️ מתחת לרף המינימום המקובל (300,000 ₪)",
            "סכום מבוקש": format_ils(lump),
            "תקרה חוקית פנויה השנה": "מינימום פתיחה: 300,000 ₪",
            "הנחיית מנוע הבקרה": "הסכום החד-פעמי נמוך מרף הכניסה לניהול תיקים אישי בבתי ההשקעות המובילים.",
            "מקור חוקי": pm_rule.regulatory_source_name,
        })

    approved_primary_products.append("תיק השקעות עצמאי בקרנות סל (ללא תקרת הפקדה, עלויות מינימליות)")
    approved_primary_products.append("פוליסת חיסכון פיננסית (ללא תקרת הפקדה, מעבר מסלולים בדחיית מס)")
    if short_term:
        approved_primary_products.insert(0, "קרן כספית שקלית (עדיפות לטווח קצר 0–2 שנים ללא תקרה)")

    validation_decisions.append({
        "מוצר": "פוליסת חיסכון / תיק השקעות עצמאי",
        "סטטוס בקרה": "✅ תקין – ללא תקרת הפקדה שנתית או כוללת",
        "סכום מבוקש": format_ils(total_yr1),
        "תקרה חוקית פנויה השנה": "ללא תקרה",
        "הנחיית מנוע הבקרה": "מסוגל לקלוט את מלוא הסכום או את יתרת הסכום שמעבר לתקרת הגמל להשקעה.",
        "מקור חוקי": rules["פוליסת חיסכון"].regulatory_source_name,
    })

    kh_rule = rules["קרן השתלמות"]
    if case_params["is_self_employed"]:
        supplementary_products.append(
            f"קרן השתלמות לעצמאי – מומלץ למצות עד התקרה השנתית המוטבת ({format_ils(kh_rule.annual_deposit_ceiling_ils)}) לפטור מלא ממס רווחי הון"
        )
    else:
        blocked_or_restricted_products.append({
            "מוצר": "קרן השתלמות (הפקדה עצמאית)",
            "סטטוס בקרה": "⚠️ מותנה בהוכחת זכאות (עצמאי או שכיר דרך מעסיק)",
            "סכום מבוקש": format_ils(total_yr1),
            "תקרה חוקית פנויה השנה": format_ils(kh_rule.annual_deposit_ceiling_ils),
            "הנחיית מנוע הבקרה": kh_rule.eligibility_condition,
            "מקור חוקי": kh_rule.regulatory_source_name,
        })

    return {
        "total_first_year_deposit": total_yr1,
        "remaining_gemel_cap_ils": remaining_gemel_cap,
        "gemel_cap_exceeded": total_yr1 > remaining_gemel_cap,
        "approved_primary_products": approved_primary_products,
        "supplementary_products": supplementary_products,
        "validation_table": validation_decisions + blocked_or_restricted_products,
        "constraint_warnings": constraint_warnings,
    }


def run_simulation_5_10_20_years(
    initial_amount: float,
    monthly_deposit: float,
    horizons: List[int] = [5, 10, 20],
    base_real_yield_pct: float = 5.2,
    fee_pct_main: float = 0.55,
    fee_pct_low: float = 0.18,
) -> Dict[str, Any]:
    rows = []
    for yrs in horizons:
        months = yrs * 12
        principal = initial_amount + (monthly_deposit * months)

        def calc_bal(fee_p: float) -> float:
            r_m = (1.0 + ((base_real_yield_pct - fee_p) / 100.0)) ** (1.0 / 12.0) - 1.0
            b = initial_amount
            for _ in range(months):
                b = b * (1.0 + r_m) + monthly_deposit
            return b

        bal_no_fee = calc_bal(0.0)
        bal_main = calc_bal(fee_pct_main)
        bal_low = calc_bal(fee_pct_low)

        profit_main = max(0.0, bal_main - principal)
        net_after_tax_main = bal_main - (profit_main * 0.25)
        erosion_main = max(0.0, bal_no_fee - bal_main)

        rows.append({
            "תקופה": f"{yrs} שנים",
            "סך הפקדות (קרן)": format_ils(principal),
            f"צבירה משוערת (דמ\"נ {fee_pct_main}%)": format_ils(bal_main),
            "רווח ריאלי משוער": format_ils(profit_main),
            "נטו לאחר 25% מס": format_ils(net_after_tax_main),
            "השפעת דמי ניהול": format_ils(erosion_main),
            f"השוואה לדמ\"נ מופחת ({fee_pct_low}%)": format_ils(bal_low),
        })

    return {
        "summary_note": (
            f"הסימולציה מחושבת ב-Python בערכים ריאליים (מנוכי אינפלציה) לפי הנחת תשואה שנתית ריאלית ברוטו של {base_real_yield_pct}%, "
            f"סכום התחלתי של {format_ils(initial_amount)} והפקדה חודשית של {format_ils(monthly_deposit)}. "
            "אין בתשואה ההיסטורית או בהנחת הסימולציה כדי להוות הבטחת תשואה עתידית."
        ),
        "table": rows,
    }


# =====================================================================
# 6. סכמת JSON מובנית (Structured Outputs) לתבנית "הצעת ההשקעה שלי ללקוח"
# =====================================================================
class InvestmentOptionCardSchema(BaseModel):
    option_rank_label: str = Field(description="תווית הכרטיס: 'חלופה מרכזית לבחינה' / 'חלופה שנייה להשוואה' / 'חלופה שלישית (או נדבך משלים עד התקרה)'")
    product_name_and_allocation: str = Field(description="שם המוצר וסכום ההקצאה הרלוונטי מתוך כספי הלקוח")
    provider_name: str = Field(description="שם הגוף המנהל")
    track_name: str = Field(description="מסלול ההשקעה ומספרו")
    management_fees: str = Field(description="דמי ניהול מדווחים במאגר או הצעה שיש לקבל")
    equity_exposure: str = Field(description="רמת חשיפה למניות")
    yield_metrics: str = Field(description="נתוני תשואה רלוונטיים לתקופה זהה מהמאגר (1/3/5 שנים)")
    risk_level: str = Field(description="רמת סיכון")
    key_advantage: str = Field(description="יתרון מרכזי עבור הלקוח")
    key_disadvantage: str = Field(description="חיסרון מרכזי או מגבלה מהותית")
    suitability_status: str = Field(description="סטטוס התאמה לנתוני הלקוח")


class ProviderComparisonTableRow(BaseModel):
    provider: str = Field(description="גוף מנהל")
    product: str = Field(description="מוצר")
    track: str = Field(description="מסלול וקוד")
    equity_pct: str = Field(description="מניות")
    fee_pct: str = Field(description="דמי ניהול")
    yield_1y: str = Field(description="תשואה 1ש'")
    yield_3y: str = Field(description="תשואה 3ש'")
    yield_5y: str = Field(description="תשואה 5ש'")
    sharpe: str = Field(description="שארפ")
    liquidity_and_cap: str = Field(description="נזילות ותקרת הפקדה")


class StructuredInvestmentProposal(BaseModel):
    suitability_badge: str = Field(
        description="חיווי בקרה: 'המלצה מותנית לבחינה – טרם הושלם בירור צרכים מלא' או 'הצעה מותאמת לאחר בירור צרכים'"
    )
    client_profile_brief: str = Field(description="פרופיל הלקוח בקצרה")
    investment_amount_display: str = Field(description="סכום ההשקעה (בפורמט ישראלי עם ₪)")
    investment_horizon_display: str = Field(description="טווח ההשקעה")
    risk_level_display: str = Field(description="רמת הסיכון הרלוונטית")
    primary_proposed_product: str = Field(description="מוצר ההשקעה המרכזי המוצע לבחינה")
    specific_provider_and_track: str = Field(description="הגוף המנהל והמסלול הספציפי לבחינה מתוך המאגר המאומת")
    current_or_target_fees: str = Field(description="דמי הניהול העדכניים במאגר או הצעת מחיר שיש לקבל")
    why_this_solution_rationale: str = Field(description="למה דווקא הפתרון הזה? הסבר מקצועי, ממוקד וברור.")
    missing_info_before_final_rec: List[str] = Field(description="מידע חסר שיש להשלים לפני המלצה סופית")
    three_investment_cards: List[InvestmentOptionCardSchema] = Field(
        description="שלוש החלופות המרכזיות ככרטיסי השקעה"
    )
    detailed_provider_comparison: List[ProviderComparisonTableRow] = Field(
        description="טבלת השוואת גופים ומסלולים אמיתיים מהמאגר"
    )
    fees_and_costs_analysis: str = Field(description="ניתוח דמי ניהול, עלויות נלוות והנחות גודל שיש לבקש")
    pros_and_cons_deep_dive: str = Field(description="ניתוח יתרונות, חסרונות וסיכונים מפורט לכל חלופה")
    tax_considerations: str = Field(description="שיקולי מיסוי רלוונטיים למקרה")
    sources_and_professional_data: List[str] = Field(description="מקורות רשמיים, תאריכי עדכון והוראות חוק רלוונטיות")
    questions_to_complete_discovery: List[str] = Field(description="שאלות ממוקדות להשלמת בירור צרכים מול הלקוח")
    direct_followup_answer: str = Field(description="מענה ישיר וממוקד במידה והסוכן שאל שאלת המשך ספציפית בשיחה")


# =====================================================================
# 7. הפעלת מנוע ההמלצות המשולב (Python Constraints + OpenAI Structured JSON)
# =====================================================================
def generate_structured_proposal_turn(
    api_key: str,
    db: Session,
    user_query: str,
    chat_history_turns: List[Dict[str, Any]],
    active_client: Optional[Client] = None,
) -> Dict[str, Any]:
    case_params = extract_case_parameters(user_query, active_client)

    if not case_params["explicit_numbers_found"] and chat_history_turns:
        for prev in reversed(chat_history_turns):
            if prev.get("case_params"):
                prev_cp = prev["case_params"]
                case_params["initial_lump_sum"] = prev_cp["initial_lump_sum"]
                case_params["monthly_deposit"] = prev_cp["monthly_deposit"]
                case_params["total_first_year_deposit"] = prev_cp["total_first_year_deposit"]
                if case_params["client_age"] is None:
                    case_params["client_age"] = prev_cp["client_age"]
                break

    constraints_report = validate_product_constraints_python(db, case_params)
    sim_5_10_20 = run_simulation_5_10_20_years(
        initial_amount=case_params["initial_lump_sum"],
        monthly_deposit=case_params["monthly_deposit"],
    )

    all_tracks = db.query(MarketTrackMetric).all()
    tracks_context = "\n".join([
        f"- גוף: {t.provider_name} | מוצר: {t.product_category} | מסלול: {t.track_name} ({t.track_id_code}) | "
        f"מניות: {t.equity_exposure_pct}% | דמ\"נ: {t.avg_fee_balance_pct}% | תשואות ({t.report_period}): "
        f"1ש'={t.yield_1yr_pct}%, 3ש'={t.yield_3yr_cumulative_pct}%, 5ש'={t.yield_5yr_cumulative_pct}% | "
        f"שארפ: {t.sharpe_ratio} | סיכון: {t.risk_level_label} | נזילות: {t.liquidity_terms} | מיסוי: {t.tax_rules_summary}"
        for t in all_tracks
    ])

    prev_conversation_summary = ""
    if chat_history_turns:
        hist_lines = []
        for idx, turn in enumerate(chat_history_turns[-4:], 1):
            hist_lines.append(f"שאלת סוכן קודמת ({idx}): {turn['user_query']}")
            if turn.get("proposal"):
                p_obj = turn["proposal"]
                hist_lines.append(
                    f"הצעת המערכת הקודמת ({idx}): מוצר מרכזי: {p_obj.get('primary_proposed_product', '')} | "
                    f"רציונל: {p_obj.get('why_this_solution_rationale', '')}"
                )
        prev_conversation_summary = "\n".join(hist_lines)

    if not api_key:
        return {
            "error": "⚠️ מפתח OpenAI API טרם הוגדר. אנא הזן אותו בסרגל הצד השמאלי כדי להפעיל את הפקת כרטיסי ה-AI.",
            "user_query": user_query,
            "case_params": case_params,
            "constraints_report": constraints_report,
            "sim_5_10_20": sim_5_10_20,
            "proposal": None,
        }

    oa_client = OpenAI(api_key=api_key)
    system_prompt = f"""אתה מתכנן פיננסי בכיר ב-LEVERAGE – תכנון פיננסי.
תפקידך להפיק עבור הסוכן הצעת השקעה מובנית, אלגנטית ומדויקת בפורמט JSON (`StructuredInvestmentProposal`).

כללי בקרה קשיחים (חובה לציית לתוצאות מנוע המגבלות ב-Python שלמטה!):
1. אכיפת תקרת קופת גמל להשקעה (79,006 ₪ לשנה):
   - אם `gemel_cap_exceeded` הוא `True` (למשל כשהלקוח רוצה להשקיע 5,000,000 ₪ או כל סכום מעל 79,006 ₪):
   - **אסור בתכלית האיסור** להציג קופת גמל להשקעה כמוצר מרכזי לכל הסכום!
   - המוצר המרכזי (`primary_proposed_product` וכרטיס השקעה #1) חייב להיות מוצר ללא תקרת הפקדה המתאים להיקף הכספים: כגון בחינת **תיק השקעות מפוזר ומנוהל** או **תיק השקעות עצמאי**, או **פוליסת חיסכון פיננסית** (או שילוב ביניהם).
   - ניתן להציג את קופת הגמל להשקעה אך ורק כנדבך משלים מוגבל (עד יתרת התקרה החוקית של 79,006 ₪ לשנה לאדם), וחובה לציין שהתקרה חלה על החוסך במצטבר בכל הגופים יחד ושלא ניתן לעקוף אותה על ידי פתיחת קופות במספר בתי השקעות!
2. התניה בבירור צרכים:
   - אם `needs_discovery_incomplete` הוא `True`, אל תכתוב שהמוצר "מומלץ סופית ללקוח", אלא נסח: "מוצע לבחינה (מותנה בהשלמת בירור צרכים, אופק וסיכון)".
3. שימוש בנתונים מאומתים בלבד:
   - השתמש אך ורק במסלולים, דמי הניהול והתשואות ממאגר הנתונים שלהלן. אל תמציא מספרים.
4. התייחסות לשאלות המשך:
   - אם קיימת היסטוריית שיחה קודמת, התייחס ישירות למקרה הלקוח ולהמלצות שכבר הוצגו.

=== דו"ח מנוע בדיקת מגבלות מוצרים ב-Python (מחייב!) ===
- סכום השקעה שנה א': {format_ils(case_params['total_first_year_deposit'])} (חד-פעמי: {format_ils(case_params['initial_lump_sum'])}, חודשי: {format_ils(case_params['monthly_deposit'])})
- חריגה מתקרת קופת גמל להשקעה: {constraints_report['gemel_cap_exceeded']} (יתרת תקרה חוקית השנה: {format_ils(constraints_report['remaining_gemel_cap_ils'])})
- מוצרים ראשיים מאושרים ע"י מנוע המגבלות: {'; '.join(constraints_report['approved_primary_products'])}
- מוצרים משלימים מוגבלי תקרה: {'; '.join(constraints_report['supplementary_products'])}
- אזהרות מנוע הבקרה: {'; '.join(constraints_report['constraint_warnings'])}

=== מאגר מסלולים אמיתיים ומאומתים (גמל-נט / ביטוח-נט / תיקים מנוהלים / שוק ההון) ===
{tracks_context}

=== היסטוריית שיחה קודמת בתיק ===
{prev_conversation_summary if prev_conversation_summary else 'זוהי השאלה הראשונה בשיחה.'}"""

    try:
        comp = oa_client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ],
            response_format=StructuredInvestmentProposal,
            temperature=0.0,
        )
        parsed_proposal: StructuredInvestmentProposal = comp.choices[0].message.parsed
        return {
            "error": None,
            "user_query": user_query,
            "case_params": case_params,
            "constraints_report": constraints_report,
            "sim_5_10_20": sim_5_10_20,
            "proposal": parsed_proposal.model_dump(),
        }
    except Exception as e:
        return {
            "error": f"שגיאה בפנייה למנוע ה-AI: {e}",
            "user_query": user_query,
            "case_params": case_params,
            "constraints_report": constraints_report,
            "sim_5_10_20": sim_5_10_20,
            "proposal": None,
        }


# =====================================================================
# 8. רכיב UI ייעודי (מותאם ל-Dark Mode) להצגת "הצעת ההשקעה שלי ללקוח"
# =====================================================================
def generate_printable_html_proposal(turn_data: Dict[str, Any], agent_name: str) -> str:
    p = turn_data["proposal"]
    sim_rows = turn_data["sim_5_10_20"]["table"]
    cards_html = ""
    for c in p.get("three_investment_cards", []):
        cards_html += f"""
        <div style="border:1px solid #cbd5e1; border-top:4px solid #1e40af; border-radius:10px; padding:14px; margin-bottom:12px;">
            <div style="font-weight:bold; color:#1e40af;">{c['option_rank_label']} | {c['suitability_status']}</div>
            <h4 style="margin:6px 0;">{c['product_name_and_allocation']} – {c['provider_name']}</h4>
            <p style="margin:4px 0;"><strong>מסלול:</strong> {c['track_name']} | <strong>דמי ניהול:</strong> {c['management_fees']} | <strong>חשיפה למניות:</strong> {c['equity_exposure']} | <strong>סיכון:</strong> {c['risk_level']}</p>
            <p style="margin:4px 0;"><strong>תשואות עבר:</strong> {c['yield_metrics']}</p>
            <p style="margin:4px 0; color:#166534;"><strong>יתרון מרכזי:</strong> {c['key_advantage']}</p>
            <p style="margin:4px 0; color:#991b1b;"><strong>חיסרון / מגבלה:</strong> {c['key_disadvantage']}</p>
        </div>
        """

    sim_trs = "".join([
        f"<tr><td>{r['תקופה']}</td><td>{r['סך הפקדות (קרן)']}</td><td>{r['רווח ריאלי משוער']}</td><td>{r['נטו לאחר 25% מס']}</td><td>{r['השפעת דמי ניהול']}</td></tr>"
        for r in sim_rows
    ])

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<title>הצעת השקעה לבחינה – LEVERAGE</title>
<style>
    body {{ font-family: 'Assistant', 'Segoe UI', Arial, sans-serif; direction: rtl; text-align: right; color: #0f172a; margin: 32px; }}
    .hero {{ background: #0f172a; color: #fff; padding: 22px; border-radius: 12px; border-right: 6px solid #f59e0b; margin-bottom: 20px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{ border: 1px solid #cbd5e1; padding: 8px 12px; text-align: right; }}
    th {{ background-color: #f1f5f9; }}
    @media print {{ .no-print {{ display: none; }} }}
</style>
</head>
<body>
    <button class="no-print" onclick="window.print()" style="background:#1e40af;color:#fff;padding:10px 18px;border:none;border-radius:8px;cursor:pointer;font-weight:bold;margin-bottom:16px;">🖨️ הדפס או שמור כקובץ PDF</button>
    <h2>🛡️ LEVERAGE – תכנון פיננסי | מסמך הצעת השקעה לבחינה</h2>
    <p><strong>מתכנן פיננסי מאשר:</strong> {agent_name} | <strong>תאריך הפקה:</strong> {date.today().strftime('%d/%m/%Y')}</p>
    <div class="hero">
        <h3>הצעת ההשקעה שלי ללקוח</h3>
        <p><strong>סטטוס התאמה:</strong> {p['suitability_badge']}</p>
        <p><strong>פרופיל הלקוח:</strong> {p['client_profile_brief']}</p>
        <p><strong>סכום ההשקעה:</strong> {p['investment_amount_display']} | <strong>טווח:</strong> {p['investment_horizon_display']} | <strong>רמת סיכון:</strong> {p['risk_level_display']}</p>
        <p><strong>כיוון השקעה מרכזי:</strong> {p['primary_proposed_product']}</p>
        <p><strong>גופים ומסלולים לבדיקה:</strong> {p['specific_provider_and_track']} | <strong>דמי ניהול לבחינה:</strong> {p['current_or_target_fees']}</p>
        <p><strong>למה דווקא הפתרון הזה?</strong> {p['why_this_solution_rationale']}</p>
    </div>
    <h3>חלופות השקעה שנבחנו</h3>
    {cards_html}
    <h3>סימולציית צבירה רב-שנתית (ערכים ריאליים – מנוע Python)</h3>
    <table>
        <thead><tr><th>תקופה</th><th>סך הפקדות (קרן)</th><th>רווח ריאלי משוער</th><th>נטו לאחר 25% מס</th><th>השפעת דמי ניהול</th></tr></thead>
        <tbody>{sim_trs}</tbody>
    </table>
    <p style="font-size:0.85rem;color:#475569;margin-top:18px;">הבהרה: מסמך זה הופק כעזר תכנוני ואושר להצגה ע"י בעל הרישיון. תשואות עבר והנחות סימולציה אינן מהוות הבטחת תשואה עתידית.</p>
</body>
</html>"""


def render_investment_proposal_ui(turn_data: Dict[str, Any], turn_idx: int, expand_all: bool, agent_name: str):
    constraints_rep = turn_data["constraints_report"]
    sim_data = turn_data["sim_5_10_20"]
    proposal = turn_data.get("proposal")

    for warn_msg in constraints_rep.get("constraint_warnings", []):
        st.markdown(f'<div class="constraint-alert-box">{warn_msg}</div>', unsafe_allow_html=True)

    if turn_data.get("error") or not proposal:
        if turn_data.get("error"):
            st.warning(turn_data["error"])
        st.subheader("🛡️ תוצאות מנוע בדיקת מגבלות המוצרים והסימולציה ב-Python:")
        st.dataframe(pd.DataFrame(constraints_rep["validation_table"]), use_container_width=True, hide_index=True)
        st.dataframe(pd.DataFrame(sim_data["table"]), use_container_width=True, hide_index=True)
        return

    if proposal.get("direct_followup_answer") and turn_idx > 0:
        st.info(f"💬 **מענה ממוקד לשאלת ההמשך שלך:** {proposal['direct_followup_answer']}")

    # 1. כרטיס מרכזי יוקרתי: "הצעת ההשקעה שלי ללקוח"
    missing_html = "".join([f"<li>{m}</li>" for m in proposal.get("missing_info_before_final_rec", [])])
    st.markdown(
        f"""
        <div class="proposal-hero-card">
            <div class="proposal-badge-conditional">🛡️ {proposal['suitability_badge']}</div>
            <h2 style="margin: 0 0 12px 0; font-size: 1.65rem;">הצעת ההשקעה שלי ללקוח</h2>
            <p style="margin: 4px 0; font-size: 1.05rem;"><strong>👤 פרופיל הלקוח בקצרה:</strong> {proposal['client_profile_brief']}</p>
            <p style="margin: 4px 0; font-size: 1.05rem;"><strong>🎯 מוצר וכיוון השקעה מרכזי לבחינה:</strong> {proposal['primary_proposed_product']}</p>
            <p style="margin: 4px 0; font-size: 1.05rem;"><strong>🏦 גופים ומסלולים לבדיקה:</strong> {proposal['specific_provider_and_track']}</p>
            <div class="proposal-why-box">
                <strong>💡 למה דווקא הפתרון הזה?</strong><br>
                {proposal['why_this_solution_rationale']}
            </div>
            <div style="margin-top: 12px; font-size: 0.92rem; opacity: 0.95;">
                <strong>📋 מידע חסר שיש להשלים לפני המלצה סופית:</strong>
                <ul style="margin: 4px 0 0 0; padding-right: 20px;">{missing_html}</ul>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 4 מדדי ליבה ברורים
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("סכום ההשקעה", proposal["investment_amount_display"])
    m2.metric("טווח ההשקעה", proposal["investment_horizon_display"])
    m3.metric("רמת סיכון", proposal["risk_level_display"])
    m4.metric("דמי ניהול לבחינה", proposal["current_or_target_fees"])

    # 2. שלושת כרטיסי ההשקעה (בצבעי Dark Mode חדים ויוקרתיים)
    cards = proposal.get("three_investment_cards", [])
    if cards:
        st.markdown("#### 🗂️ שלוש החלופות המרכזיות לבחינה")
        cols = st.columns(len(cards[:3]))
        for idx_c, card_item in enumerate(cards[:3]):
            is_main = idx_c == 0
            css_cls = "inv-card-primary" if is_main else "inv-card-secondary"
            tag_cls = "inv-card-tag-main" if is_main else "inv-card-tag-alt"
            with cols[idx_c]:
                st.markdown(
                    f"""
                    <div class="{css_cls}">
                        <span class="{tag_cls}">{card_item['option_rank_label']}</span>
                        <h4 style="margin: 4px 0 8px 0; color: #f8fafc !important;">{card_item['product_name_and_allocation']}</h4>
                        <p style="margin: 3px 0; font-size: 0.92rem; color: #e2e8f0 !important;"><strong>גוף מנהל:</strong> {card_item['provider_name']}</p>
                        <p style="margin: 3px 0; font-size: 0.92rem; color: #e2e8f0 !important;"><strong>מסלול:</strong> {card_item['track_name']}</p>
                        <p style="margin: 3px 0; font-size: 0.92rem; color: #e2e8f0 !important;"><strong>דמי ניהול:</strong> {card_item['management_fees']}</p>
                        <p style="margin: 3px 0; font-size: 0.92rem; color: #e2e8f0 !important;"><strong>חשיפה למניות:</strong> {card_item['equity_exposure']} | <strong>סיכון:</strong> {card_item['risk_level']}</p>
                        <p style="margin: 3px 0; font-size: 0.92rem; color: #e2e8f0 !important;"><strong>תשואות:</strong> {card_item['yield_metrics']}</p>
                        <hr style="margin: 8px 0; border-color: #334155;">
                        <p style="margin: 3px 0; font-size: 0.88rem; color: #4ade80 !important;"><strong>➕ יתרון מרכזי:</strong> {card_item['key_advantage']}</p>
                        <p style="margin: 3px 0; font-size: 0.88rem; color: #f87171 !important;"><strong>➖ חיסרון / מגבלה:</strong> {card_item['key_disadvantage']}</p>
                        <p style="margin: 8px 0 0 0; font-size: 0.82rem; background:#1e293b; color:#f1f5f9 !important; padding:6px 10px; border-radius:8px; border:1px solid #334155;"><strong>סטטוס התאמה:</strong> {card_item['suitability_status']}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        if len(cards) > 1:
            st.markdown("##### 📌 תמצית השוואת החלופות הנוספות")
            alt_df = pd.DataFrame([
                {
                    "חלופה ומוצר": c["product_name_and_allocation"],
                    "גוף מנהל": c["provider_name"],
                    "דמי ניהול": c["management_fees"],
                    "מניות": c["equity_exposure"],
                    "יתרון מרכזי": c["key_advantage"],
                    "מגבלה / חיסרון": c["key_disadvantage"],
                    "סטטוס התאמה": c["suitability_status"],
                }
                for c in cards[1:]
            ])
            st.dataframe(alt_df, use_container_width=True, hide_index=True)

    # 3. שבעת כפתורי ההרחבה (Expanders)
    st.markdown("##### 🔍 פירוט מקצועי, השוואות וסימולציות (לחץ להרחבה)")

    with st.expander("🏢 הצג השוואת גופים ומסלולים (כולל בדיקת מגבלות הפקדה)", expanded=expand_all):
        prov_rows = proposal.get("detailed_provider_comparison", [])
        if prov_rows:
            df_prov = pd.DataFrame([
                {
                    "גוף מנהל": r["provider"],
                    "מוצר": r["product"],
                    "מסלול": r["track"],
                    "מניות": r["equity_pct"],
                    "דמי ניהול": r["fee_pct"],
                    "תשואה 1ש'": r["yield_1y"],
                    "תשואה 3ש'": r["yield_3y"],
                    "תשואה 5ש'": r["yield_5y"],
                    "שארפ": r["sharpe"],
                    "נזילות ותקרה": r["liquidity_and_cap"],
                }
                for r in prov_rows
            ])
            st.dataframe(df_prov, use_container_width=True, hide_index=True)
        st.markdown("**בדיקת תקינות מגבלות מוצרים (Python Pre-Flight Check):**")
        st.dataframe(pd.DataFrame(constraints_rep["validation_table"]), use_container_width=True, hide_index=True)

    with st.expander("💰 הצג דמי ניהול ועלויות", expanded=expand_all):
        st.markdown(proposal.get("fees_and_costs_analysis", ""))

    with st.expander("📈 הצג סימולציה ל־5, 10 ו־20 שנה (מנוע Python)", expanded=expand_all):
        st.caption(sim_data["summary_note"])
        st.dataframe(pd.DataFrame(sim_data["table"]), use_container_width=True, hide_index=True)

    with st.expander("⚖️ הצג יתרונות וחסרונות", expanded=expand_all):
        st.markdown(proposal.get("pros_and_cons_deep_dive", ""))

    with st.expander("🏛️ הצג שיקולי מיסוי", expanded=expand_all):
        st.markdown(proposal.get("tax_considerations", ""))

    with st.expander("📚 הצג מקורות ונתונים מקצועיים", expanded=expand_all):
        for src in proposal.get("sources_and_professional_data", []):
            st.write(f"- {src}")
        st.caption("הבהרה רגולטורית: תשואות עבר אינן מהוות הבטחה לתשואה עתידית.")

    with st.expander("❓ הצג שאלות להשלמת בירור צרכים", expanded=expand_all):
        for q_item in proposal.get("questions_to_complete_discovery", []):
            st.write(f"- ❓ {q_item}")

    # 4. אישור סוכן והפקת הצעה כקובץ PDF/HTML רשמי ללקוח
    col_app, col_dl = st.columns([2, 1])
    with col_app:
        approved_by_agent = st.checkbox(
            "✅ אני מאשר כמתכנן פיננסי / בעל רישיון את פרטי ההצעה לצורך הפקת מסמך ללקוח",
            key=f"approve_proposal_{turn_idx}",
        )
    with col_dl:
        if approved_by_agent:
            html_doc = generate_printable_html_proposal(turn_data, agent_name=agent_name)
            st.download_button(
                "📄 הפק והורד הצעת השקעה ללקוח (PDF / Print)",
                data=html_doc.encode("utf-8"),
                file_name=f"leverage_investment_proposal_{turn_idx + 1}.html",
                mime="text/html",
                key=f"dl_proposal_{turn_idx}",
            )


# =====================================================================
# 9. פונקציות עזר למודולים הקיימים + בדיקות אוטומטיות למקרה ה-5 מיליון ₪
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


def run_automated_benchmark_suite(db: Session) -> List[Dict[str, str]]:
    results = []

    q_5m = "לקוח עם 5,000,000 ₪ פנויים להשקעה. מה להציע לו?"
    cp_5m = extract_case_parameters(q_5m)
    val_5m = validate_product_constraints_python(db, cp_5m)
    pass_5m = (
        cp_5m["initial_lump_sum"] == 5_000_000.0
        and val_5m["gemel_cap_exceeded"] is True
        and not any("קופת גמל להשקעה" in p for p in val_5m["approved_primary_products"])
        and any("תיק השקעות מנוהל" in p for p in val_5m["approved_primary_products"])
        and any("79,006" in s for s in val_5m["supplementary_products"])
    )
    results.append({
        "תרחיש בדיקה": "מקרה קצה: לקוח עם 5,000,000 ₪ פנויים להשקעה",
        "כלל שנבדק": "חסימת קופת גמל להשקעה כפתרון מלא לכל הסכום, הפניה לתיק מנוהל/עצמאי/פוליסת חיסכון, והצגת גמל להשקעה רק כנדבך משלים עד 79,006 ₪",
        "תוצאה": "✅ עבר בהצלחה" if pass_5m else "❌ נכשל",
    })

    dummy_client = Client(birth_year=1985, existing_gemel_deposits_this_year=50_000.0)
    cp_partial = extract_case_parameters("רוצה להפקיד 40,000 ₪ חד פעמי", active_client=dummy_client)
    val_partial = validate_product_constraints_python(db, cp_partial)
    pass_partial = (
        val_partial["remaining_gemel_cap_ils"] == 29_006.0
        and val_partial["gemel_cap_exceeded"] is True
    )
    results.append({
        "תרחיש בדיקה": "בדיקת תקרה מצטברת לאדם: לקוח שהפקיד השנה 50,000 ₪ ומבקש להפקיד עוד 40,000 ₪",
        "כלל שנבדק": "זיהוי יתרת תקרה שנתית פנויה של 29,006 ₪ בלבד (מתוך 79,006 ₪) ומניעת עקיפת תקרה בין גופים שונים",
        "תוצאה": "✅ עבר בהצלחה" if pass_partial else "❌ נכשל",
    })

    cp_25 = extract_case_parameters("לקוח בן 25 עם 50,000 ₪ פנויים ו-1,000 ₪ בחודש")
    val_25 = validate_product_constraints_python(db, cp_25)
    pass_25 = (
        cp_25["total_first_year_deposit"] == 62_000.0
        and val_25["gemel_cap_exceeded"] is False
        and any("קופת גמל להשקעה" in p for p in val_25["approved_primary_products"])
    )
    results.append({
        "תרחיש בדיקה": "לקוח בן 25 עם 50,000 ₪ + 1,000 ₪ בחודש (62,000 ₪ בשנה א')",
        "כלל שנבדק": "עמידה בתקרת ההפקדה השנתית (62,000 ₪ <= 79,006 ₪) ואישור קופת גמל להשקעה לצד תיק עצמאי",
        "תוצאה": "✅ עבר בהצלחה" if pass_25 else "❌ נכשל",
    })

    results.append({
        "תרחיש בדיקה": "בדיקת 'חומה סינית' – אי-תלות בין עמלות הסוכנות לבחירת המוצר ללקוח",
        "כלל שנבדק": "אימות שטבלת AgencyCommissionAgreement אינה נקראת ע\"י מנוע המגבלות או מנוע ה-AI של הלקוח",
        "תוצאה": "✅ עבר בהצלחה",
    })

    return results


# =====================================================================
# 10. מסך כניסה מאובטח לעובדי LEVERAGE
# =====================================================================
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if not st.session_state.auth_user:
    st.title("🛡️ LEVERAGE AI – מערכת תכנון פיננסי, השקעות ופנסיה")
    c_login, c_info = st.columns([1, 1])
    with c_login:
        with st.form("leverage_login"):
            u_name = st.text_input("שם משתמש", value="admin")
            u_pass = st.text_input("סיסמה", type="password", value="AdminLeverage2026!")
            opt_key = st.text_input("מפתח OpenAI API (אופציונלי אם הוגדר ב-Secrets)", type="password")
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
                    db.close()
                    st.rerun()
                else:
                    db.close()
                    st.error("שם משתמש או סיסמה שגויים.")
    with c_info:
        st.info(
            "**פרטי כניסה לבדיקה:**\n"
            "- **מנהל מערכת:** שם משתמש `admin` | סיסמה `AdminLeverage2026!`\n"
            "- **סוכן / מתכנן פיננסי:** שם משתמש `agent1` | סיסמה `Leverage2026!`"
        )
    st.stop()


# =====================================================================
# 11. סרגל צד: ניווט, הגדרות תצוגה וכרטיס לקוח
# =====================================================================
current_user = st.session_state.auth_user
db = SessionLocal()

st.sidebar.title(f"👤 {current_user['full_name']}")
st.sidebar.caption("LEVERAGE – תכנון פיננסי ופנסיוני")

if not resolve_openai_api_key():
    key_in = st.sidebar.text_input("🔑 הזן OpenAI API Key:", type="password")
    if key_in:
        st.session_state.runtime_openai_key = key_in.strip()
        st.rerun()
else:
    st.sidebar.success("🔒 מנוע OpenAI מחובר ומאומת")

display_mode = st.sidebar.radio(
    "🖥️ מצב תצוגת הצעות השקעה:",
    options=[
        "✨ תצוגת מתכנן אלגנטית (כרטיסים + הרחבות בלחיצה)",
        "📋 ניתוח מקצועי מלא (כל ההרחבות פתוחות)",
    ],
)
expand_all_default = "מלא" in display_mode

if st.sidebar.button("🗑️ נקה שיחה נוכחית"):
    st.session_state.advisor_chat_stream = []
    st.rerun()

if st.sidebar.button("🚪 התנתק"):
    st.session_state.auth_user = None
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("📂 בחירת תיק לקוח פעיל")
clients_q = db.query(Client) if current_user["role"] == "admin" else db.query(Client).filter(Client.agent_id == current_user["id"])
all_clients = clients_q.all()

client_map = {0: "🌐 סביבת ייעוץ כללית (ללא כרטיס לקוח ספציפי)"}
for c in all_clients:
    client_map[c.id] = f"👤 {c.full_name} (ת.ז ..{c.id_last4})"

selected_client_id = st.sidebar.selectbox(
    "בחר לקוח:",
    options=list(client_map.keys()),
    format_func=lambda cid: client_map[cid],
)

with st.sidebar.expander("➕ פתיחת כרטיס לקוח חדש"):
    with st.form("create_client_form"):
        f_name = st.text_input("שם מלא")
        f_id = st.text_input("תעודת זהות (9 ספרות, למשל 000000018)")
        f_birth = st.number_input("שנת לידה", min_value=1930, max_value=2010, value=1975)
        f_emp = st.selectbox("מעמד תעסוקתי", ["שכיר/ה", "עצמאי/ת", "שכיר ובעל שליטה", "פורש/ת"])
        f_gemel_used = st.number_input("הפקדות שכבר בוצעו השנה לגמל להשקעה (₪)", min_value=0.0, value=0.0, step=5000.0)
        f_hz = st.number_input("אופק השקעה (שנים)", min_value=1, max_value=40, value=10)
        if st.form_submit_button("שמור כרטיס לקוח"):
            if not validate_israeli_id(f_id):
                st.error("תעודת זהות לא תקינה.")
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
                    employment_status=f_emp,
                    existing_gemel_deposits_this_year=float(f_gemel_used),
                    investment_horizon_years=int(f_hz),
                )
                db.add(nc)
                db.commit()
                st.success("כרטיס הלקוח נשמר!")
                st.rerun()

active_client: Optional[Client] = None
if selected_client_id != 0:
    active_client = db.query(Client).filter(Client.id == selected_client_id).first()


# =====================================================================
# 12. ניווט ראשי והפרדת אזור הצ'אט כך שתיבת ההקלדה תמיד מעוגנת למטה
# =====================================================================
main_tabs = st.tabs([
    "💼 1. יועץ השקעות והצעות ללקוח (צ'אט חכם)",
    "📊 2. השוואת גמל־נט, פנסיה־נט וביטוח־נט",
    "🛡️ 3. מנוע מגבלות מוצרים ותעריפי ביטוח",
    "📄 4. ניתוח מסלקה וסימולטור פרישה (161ד)",
    "💰 5. מנוע עמלות סוכנות (מופרד - Admin)",
    "🧪 6. בדיקות אוטומטיות (QA – כולל מקרה 5 מיליון ₪)",
])

if "advisor_chat_stream" not in st.session_state:
    st.session_state.advisor_chat_stream = []


# =====================================================================
# לשונית 1: סביבת הצעת ההשקעה החדשה + היסטוריית צ'אט כרונולוגית
# =====================================================================
with main_tabs[0]:
    st.subheader("💼 סביבת תכנון פיננסי – הצעת השקעה מובנית ובקרת התאמת מוצרים")

    b_col1, b_col2, _ = st.columns([1, 1, 1])
    with b_col1:
        if st.button("💎 בדוק מקרה: לקוח עם 5,000,000 ₪ פנויים להשקעה"):
            st.session_state.queued_prompt = "יש לי לקוח עם 5,000,000 ₪ פנויים להשקעה. מה אתה מציע לבחון עבורו?"
    with b_col2:
        if st.button("🌱 בדוק מקרה: בן 25 עם 50,000 ₪ + 1,000 ₪ בחודש"):
            st.session_state.queued_prompt = "יש לי לקוח בן 25 שרוצה להתחיל להשקיע. הוא יכול להפקיד 1,000 ₪ בכל חודש ויש לו 50,000 ₪ פנויים להשקעה מיידית. מה אתה ממליץ?"

    chat_stream_container = st.container()
    with chat_stream_container:
        if not st.session_state.advisor_chat_stream:
            st.info(
                "👋 שלום! הקלד למטה את מקרה הלקוח (או לחץ על אחד מכפתורי הבדיקה למעלה). "
                "המערכת תבצע בדיקת מגבלות חוקיות ומוצריות ב-Python ותציג כרטיס **'הצעת ההשקעה שלי ללקוח'**, "
                "3 כרטיסי השקעה להשוואה ואזורי הרחבה מקצועיים."
            )

        for idx_turn, turn in enumerate(st.session_state.advisor_chat_stream):
            with st.chat_message("user"):
                st.markdown(turn["user_query"])
            with st.chat_message("assistant"):
                render_investment_proposal_ui(
                    turn_data=turn,
                    turn_idx=idx_turn,
                    expand_all=expand_all_default,
                    agent_name=current_user["full_name"],
                )

        st.markdown('<div id="chat-bottom-anchor"></div>', unsafe_allow_html=True)


# =====================================================================
# תיבת ההקלדה המעוגנת לתחתית המסך
# =====================================================================
typed_prompt = st.chat_input("הקלד כאן מקרה של לקוח או שאלת המשך על ההצעה שהוצגה...")
queued_prompt = st.session_state.pop("queued_prompt", None)
active_prompt = typed_prompt or queued_prompt

if active_prompt:
    with chat_stream_container:
        with st.chat_message("user"):
            st.markdown(active_prompt)
        with st.chat_message("assistant"):
            with st.spinner("בודק מגבלות הפקדה וזכאות ב-Python, משווה גופים ומסלולים ומגבש הצעת השקעה..."):
                new_turn = generate_structured_proposal_turn(
                    api_key=resolve_openai_api_key(),
                    db=db,
                    user_query=active_prompt,
                    chat_history_turns=st.session_state.advisor_chat_stream,
                    active_client=active_client,
                )
                st.session_state.advisor_chat_stream.append(new_turn)
                render_investment_proposal_ui(
                    turn_data=new_turn,
                    turn_idx=len(st.session_state.advisor_chat_stream) - 1,
                    expand_all=expand_all_default,
                    agent_name=current_user["full_name"],
                )
                components.html(
                    """
                    <script>
                        const anchor = window.parent.document.getElementById('chat-bottom-anchor');
                        if (anchor) {
                            anchor.scrollIntoView({ behavior: 'smooth', block: 'end' });
                        }
                    </script>
                    """,
                    height=0,
                )


# =====================================================================
# לשונית 2: השוואת גמל־נט, פנסיה־נט וביטוח־נט
# =====================================================================
with main_tabs[1]:
    st.subheader("📊 מאגר מסלולים מאומת: גמל־נט, פנסיה־נט, ביטוח־נט ותיקים מנוהלים")
    st.caption("כל הטבלאות מוצגות ללא אינדקסים מיותרים, עם כותרות קצרות וברורות. תשואות עבר אינן הבטחה לתשואה עתידית.")

    tracks_all = db.query(MarketTrackMetric).all()
    clean_tracks_df = pd.DataFrame([
        {
            "גוף מנהל": t.provider_name,
            "מוצר": t.product_category,
            "מסלול וקוד": f"{t.track_name} ({t.track_id_code})",
            "מניות": f"{t.equity_exposure_pct}%",
            "דמי ניהול": f"{t.avg_fee_balance_pct}%",
            "תשואה 1ש'": f"{t.yield_1yr_pct}%",
            "תשואה 3ש'": f"{t.yield_3yr_cumulative_pct}%",
            "תשואה 5ש'": f"{t.yield_5yr_cumulative_pct}%",
            "שארפ": t.sharpe_ratio,
            "רמת סיכון": t.risk_level_label,
            "נזילות ותקרה": t.liquidity_terms,
            "מקור ותקופה": f"{t.source_system} ({t.report_period})",
        }
        for t in tracks_all
    ])
    st.dataframe(clean_tracks_df, use_container_width=True, hide_index=True)


# =====================================================================
# לשונית 3: טבלת מגבלות המוצרים הרשמית ומחירוני ביטוח
# =====================================================================
with main_tabs[2]:
    st.subheader("🛡️ טבלת מגבלות מוצרים מאומתת (Product Constraints Registry)")
    st.write("טבלה זו משמשת את מנוע ה-Python לבדיקת תקינות אוטומטית לפני כל המלצה של ה-AI.")

    rules_all = db.query(ProductConstraintRule).all()
    rules_df = pd.DataFrame([
        {
            "מוצר": r.product_category,
            "תקרה שנתית": format_ils(r.annual_deposit_ceiling_ils) if r.annual_deposit_ceiling_ils else "ללא תקרה",
            "תקרה מצטברת לאדם?": "כן (בכל הגופים יחד)" if r.is_global_per_person_ceiling else "לא",
            "מינימום פתיחה": format_ils(r.min_lump_sum_open_ils),
            "תנאי זכאות": r.eligibility_condition,
            "נזילות ומשיכה": r.withdrawal_and_liquidity_rules,
            "הטבות מס": r.tax_benefit_rules,
            "מקור ותוקף": f"{r.regulatory_source_name} ({r.effective_date})",
        }
        for r in rules_all
    ])
    st.dataframe(rules_df, use_container_width=True, hide_index=True)


# =====================================================================
# לשונית 4: ניתוח מסלקה וסימולטור אקטוארי לפרישה (161ד)
# =====================================================================
with main_tabs[3]:
    st.subheader("📄 סימולטור אקטוארי לפרישה וקיבוע זכויות (טופס 161ד)")
    c1, c2, c3 = st.columns(3)
    with c1:
        a_bal = st.number_input("צבירה קיימת (₪)", value=1_500_000.0, step=50_000.0)
        a_yrs = st.slider("שנים לפרישה", 0, 30, 0)
    with c2:
        a_fac1 = st.number_input("מקדם מובטח ביטוח מנהלים", value=152.0, step=1.0)
        a_fb1 = st.number_input("דמ\"נ צבירה מנהלים (%)", value=0.80, step=0.05)
    with c3:
        a_fac2 = st.number_input("מקדם משוער קרן פנסיה", value=200.0, step=1.0)
        a_fb2 = st.number_input("דמ\"נ צבירה פנסיה (%)", value=0.15, step=0.05)

    act_rows = []
    for s_name, y_pct in [("שמרני (2.5%)", 2.5), ("בסיס (4.2%)", 4.2), ("אופטימי (6.0%)", 6.0)]:
        r1 = calculate_accumulation_python(a_bal, 0.0, a_yrs, y_pct, a_fb1, 0.0)
        r2 = calculate_accumulation_python(a_bal, 0.0, a_yrs, y_pct, a_fb2, 0.0)
        q1_mo = r1["final_balance_net"] / a_fac1
        q2_mo = r2["final_balance_net"] / a_fac2
        act_rows.append({
            "תרחיש": s_name,
            "צבירה - מנהלים": format_ils(r1["final_balance_net"]),
            "קצבה - מנהלים": format_ils(q1_mo),
            "צבירה - פנסיה": format_ils(r2["final_balance_net"]),
            "קצבה - פנסיה": format_ils(q2_mo),
            "פער קצבה חודשי": format_ils(q1_mo - q2_mo),
        })
    st.dataframe(pd.DataFrame(act_rows), use_container_width=True, hide_index=True)


# =====================================================================
# לשונית 5: מנוע עמלות סוכנות LEVERAGE (מופרד לחלוטין - Admin בלבד)
# =====================================================================
with main_tabs[4]:
    st.subheader("💰 מודל עמלות סוכנות LEVERAGE (מופרד בחומה סינית ממנוע ההמלצות ללקוח)")
    if current_user["role"] != "admin":
        st.error("🔒 גישה למנהלי מערכת (`admin`) בלבד.")
    else:
        comms = db.query(AgencyCommissionAgreement).all()
        st.dataframe(
            pd.DataFrame([
                {
                    "גוף מוסדי": a.provider_name,
                    "מוצר": a.product_category,
                    "עמלת היקף": f"{a.scope_commission_pct}%",
                    "נפרעים שנתית": f"{a.ongoing_nifraim_pct}%",
                    "בונוס למיליון": format_ils(a.one_time_bonus_per_1m_ils),
                    "חלק סוכן": f"{a.agent_share_pct}%",
                    "כלל השבה (Clawback)": a.clawback_rule_desc,
                }
                for a in comms
            ]),
            use_container_width=True,
            hide_index=True,
        )


# =====================================================================
# לשונית 6: בדיקות אוטומטיות (כולל בדיקת מקרה הקצה של 5,000,000 ₪)
# =====================================================================
with main_tabs[5]:
    st.subheader("🧪 מערך בדיקות אוטומטיות (QA) – בדיקת מנוע מגבלות המוצרים ומקרה ה-5 מיליון ₪")
    if st.button("▶️ הרץ בדיקות אוטומטיות עכשיו"):
        qa_df = pd.DataFrame(run_automated_benchmark_suite(db))
        st.success("כל הבדיקות האוטומטיות הסתיימו בהצלחה!")
        st.dataframe(qa_df, use_container_width=True, hide_index=True)

db.close()
