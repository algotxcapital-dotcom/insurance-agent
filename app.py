import streamlit as st
import os
import google.generativeai as genai
from pypdf import PdfReader

# 1. הגדרת ממשק האתר
st.set_page_config(page_title="InsurTech AI Platform (Gemini Pro)", page_icon="🤖", layout="wide")
st.title("🤖 פלטפורמת InsurTech מתקדמת לתכנון פרישה וביטוח (מופעל ע\"י Gemini Pro)")
st.write("מערכת אקולוגית חכמה לסוכני ביטוח: ניתוח סילוקיות, השוואת שוק, סיכומי פגישות ותחשיבים אקטואריים.")

# 2. הגדרת מפתח ה-API (מותאם לחשבון שלך)
api_key = "AQ.Ab8RN6JbjLPSnSke2glasV63cFU_5r37zQcNffav2c5m8iH0g"
os.environ["GEMINI_API_KEY"] = api_key
genai.configure(api_key=api_key)

# שימוש במודל Gemini 1.5 Pro ישירות דרך הספרייה הרשמית
from langchain_google_genai import ChatGoogleGenerativeAI

try:
    llm = ChatGoogleGenerativeAI(
        model="gemini-1.5-pro",
        temperature=0,
        google_api_key=api_key
    )
except Exception as e:
    st.error(f"שגיאת התחברות למודל: {e}")

# יצירת לשוניות (Tabs) לכל היכולות של המערכת
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "💬 צ'אט מומחה וקבלת החלטות", 
    "📄 ניתוח סילוקית (PDF)", 
    "📊 נתוני שוק וגמל-נט", 
    "🎙️ סיכום פגישות אודיו", 
    "📝 מילוי טפסים אוטומטי"
])

# ==========================================
# לשונית 1: צ'אט מומחה וקבלת החלטות
# ==========================================
with tab1:
    st.header("ייעוץ פנסיוני ואקטוארי חכם")
    st.write("הקלד מקרה מורכב של לקוח (למשל: השוואת ביטוח מנהלים מול קרן פנסיה, קיבוע זכויות, קרן השתלמות).")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if user_query := st.chat_input("הקלד את מקרה הלקוח כאן..."):
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)

        with st.chat_message("assistant"):
            with st.spinner("Gemini מנתח את הנתונים, מריץ תחשיבים אקטואריים ומגבש החלטה מקצועית..."):
                try:
                    prompt_full = f"""You are a senior expert AI insurance and pension planning agent in Israel. 
Provide a professional, definitive decision, include relevant mathematical logic, mention applicable tax or insurance laws in Israel, and end with an actionable recommendation for the agent.

Client Case / Question: {user_query}
Professional Analysis and Decision:"""
                    response = llm.invoke(prompt_full)
                    st.markdown(response.content)
                    st.session_state.messages.append({"role": "assistant", "content": response.content})
                except Exception as ex:
                    st.error(f"שגיאה בהפעלת המודל: {ex}")

# ==========================================
# לשונית 2: ניתוח סילוקית פנסיונית (PDF)
# ==========================================
with tab2:
    st.header("ניתוח דוח מסלקה / סילוקית פנסיונית")
    uploaded_pdf = st.file_uploader("העלה דוח סילוקית (PDF)", type=["pdf"])
    if uploaded_pdf is not None:
        with st.spinner("מחלץ נתונים ומנתח את דוח הלקוח..."):
            reader = PdfReader(uploaded_pdf)
            pdf_text = "".join([page.extract_text() or "" for page in reader.pages])
            
            analysis_prompt = f"""Analyze the following pension clearinghouse report (Silukit) for an Israeli insurance agent:
{pdf_text[:15000]}

Provide in Hebrew:
1. Asset summary.
2. Abnormal management fees.
3. Actionable recommendations."""
            pdf_response = llm.invoke(analysis_prompt)
            st.markdown(pdf_response.content)

# ==========================================
# לשונית 3: נתוני שוק וגמל-נט
# ==========================================
with tab3:
    st.header("נתוני שוק, דמי ניהול ממוצעים ומאזנים אקטואריים")
    col1, col2, col3 = st.columns(3)
    col1.metric("קרן פנסיה מקיפה", "0.15% - 0.22%")
    col2.metric("ביטוח מנהלים ישן", "0.5% - 1.05%")
    col3.metric("קופת גמל להשקעה", "0.65% - 0.75%")

# ==========================================
# לשונית 4: סיכום פגישות שמע (Audio)
# ==========================================
with tab4:
    st.header("הקלטת פגישה וסיכום אוטומטי")
    uploaded_audio = st.file_uploader("העלה הקלטת שמע", type=["mp3", "wav", "m4a"])
    if uploaded_audio is not None and st.button("הפק סיכום פגישה"):
        st.info("עיבוד הקלטת השמע מתבצע...")

# ==========================================
# לשונית 5: מילוי טפסים אוטומטי
# ==========================================
with tab5:
    st.header("מחולל נתונים למילוי טפסים רשמיים")
    with st.form("form_filler"):
        f_name = st.text_input("שם מלא")
        f_id = st.text_input("תעודת זהות")
        submit_form = st.form_submit_button("הפק נתונים")
        if submit_form:
            st.success(f"הנתונים הופקו עבור הלקוח {f_name}")
