import streamlit as st
import os
import google.generativeai as genai
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

# 1. הגדרת ממשק האתר
st.set_page_config(page_title="Pension & Insurance AI Agent", page_icon="🤖", layout="wide")
st.title("🤖 AI Insurance & Pension Expert Agent (Gemini)")
st.write("מערכת חכמה לניתוח תיקים מורכבים, קבלת החלטות אקטואריות והשוואות פיננסיות בישראל.")

# 2. מפתח API קבוע של Google Gemini
gemini_api_key = "AQ.Ab8RN6LoTHnQW3SHls2WT1sCQU9oTTOGYk7EAgM_ed-HRNl3YQ"
genai.configure(api_key=gemini_api_key)

# 3. מנוע חישוב אקטוארי-מתמטי פנימי לקבלת החלטות
def calculate_pension_vs_managers(age, pension_balance, managers_balance, managers_guaranteed_coeff, current_fees_managers):
    avg_pension_coeff = 200.0
    est_managers_pension = managers_balance / managers_guaranteed_coeff
    est_pension_pension = pension_balance / avg_pension_coeff
    
    diff = est_managers_pension - est_pension_pension
    
    if managers_guaranteed_coeff < 180 and current_fees_managers > 1.0:
        recommendation = (
            "ניתוח מתמטי-אקטוארי: לביטוח המנהלים יש מקדם מובטח עדיף משמעותית, "
            "אך דמי הניהול הגבוהים מהצביעה שוחקים את היתרון לאורך זמן. "
            "מומלץ לבחון הון מול קצבה או לבצע בחינת נקודת איזון (Break-even)."
        )
    else:
        recommendation = (
            "ניתוח מתמטי-אקטוארי: קרן הפנסיה מציגה יתרון בדמי הניהול ובכיסויים המובנים, "
            "אולם יש לבחון את השפעת המקדם המובטח בביטוח המנהלים טרם ביצוע ניוד."
        )
    
    return {
        "est_managers_pension": est_managers_pension,
        "est_pension_pension": est_pension_pension,
        "difference": diff,
        "math_recommendation": recommendation
    }

# 4. מאגר ידע מורחב (חקיקה, מיסוי, נתוני שוק ודמי ניהול ממוצעים)
KNOWLEDGE_BASE = [
    Document(
        page_content=(
            "דמי ניהול ממוצעים במשק (נתוני רשות שוק ההון): בקרנות הפנסיה החדשות המקיפות דמי הניהול הממוצעים "
            "נעים סביב 0.1% - 0.22% מהצביעה ועד 1.5% מהפקדה. בביטוחי המנהלים הישנים דמי הניהול מהצביעה מגיעים לעיתים ל-0.5% - 1.0% ואף מעבר לכך."
        ),
        metadata={"source": "רשות שוק ההון", "topic": "דמי ניהול ממוצעים"}
    ),
    Document(
        page_content=(
            "קיבוע זכויות (Tax Fixation) לפי פקודת מס הכנסה: תהליך המחייב פרישה בגיל זכאות שבו הפורש מחליט "
            "כיצד לנצל את פטור ממס על קצבה מזכה או היוון קצבה בפטור ממס. ההחלטה היא בלתי הפיכה."
        ),
        metadata={"source": "פקודת מס הכנסה", "section": "קיבוע זכויות", "topic": "מיסוי פרישה"}
    )
]

# שימוש במודל של Gemini
from langchain_google_genai import ChatGoogleGenerativeAI

llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-pro",
    temperature=0,
    google_api_key=gemini_api_key
)

SYSTEM_PROMPT = """אתה סוכן AI מומחה בכיר ומנכ"ל מקצועי של חברת תכנון פרישה, ביטוח והשקעות בישראל.
תפקידך אינו רק להציג מידע חוקי, אלא לקבל החלטות מקצועיות וחד-משמעיות עבור הלקוח והסוכן, המבוססות על החוק, המיסוי ונתוני השוק, ולפרט אותן באופן מתמטי והגיוני מנומק היטב.

הנחיות חובה:
1. אל תתחמק מתשובות ואל תגיד רק "תלוי". תן עמדה מקצועית ברורה.
2. שלב חישובים לוגיים ומתמטיים המראים את כדאיות הפעולה.
3. ציין את סעיפי החוק והתקנות הרלוונטיים.
4. סיים עם המלצה אופרטיבית מיידית לפעולה עבור סוכן הביטוח מול הלקוח.

שאלה / תיק הלקוח:
{question}

ניתוח מקצועי, החלטה מנומקת ותחשיב לוגי:"""

# 5. ממשק צ'אט אינטראקטיבי ב-Streamlit
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if user_query := st.chat_input("הקלד מקרה מורכב, שאלה במיסוי פנסיוני או דילמת ניוד..."):
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        with st.spinner("Gemini מנתח את הנתונים ומגבש החלטה מקצועית..."):
            prompt_full = SYSTEM_PROMPT.format(question=user_query)
            response = llm.invoke(prompt_full)
            st.markdown(response.content)
            st.session_state.messages.append({"role": "assistant", "content": response.content})
