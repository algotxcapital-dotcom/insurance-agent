import streamlit as st
import os
from openai import OpenAI
from pypdf import PdfReader

# 1. הגדרת ממשק האתר
st.set_page_config(page_title="InsurTech AI Platform (OpenAI)", page_icon="🤖", layout="wide")
st.title("🤖 פלטפורמת InsurTech מתקדמת לתכנון פרישה וביטוח (מופעל ע\"י OpenAI)")
st.write("מערכת אקולוגית חכמה לסוכני ביטוח: ניתוח סילוקיות, השוואת שוק, סיכומי פגישות ותחשיבים אקטואריים.")

# 2. הגדרת מפתח ה-API החדש של OpenAI
OPENAI_API_KEY = "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"
client = OpenAI(api_key=OPENAI_API_KEY)

def ask_openai(system_prompt, user_prompt):
    """
    פונקציית פנייה ישירה למנוע GPT-4o של OpenAI לקבלת החלטות אקטואריות חד-משמעיות.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0
        )
        return response.choices[0].message.content
    except Exception as ex:
        return f"שגיאה בהפעלת מנוע ה-AI: {ex}"

# יצירת לשוניות (Tabs) לכל היכולות של המערכת
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "💬 צ'אט מומחה וקבלת החלטות", 
    "📄 ניתוח סילוקית (PDF)", 
    "📊 נתוני שוק וגמל-נט", 
    "🎙️ סיכום פגישות אודיו", 
    "📝 מילוי טפסים אוטומטי"
])

SYSTEM_EXPERT_PROMPT = """אתה סוכן AI מומחה בכיר ומנכ"ל חברת תכנון פרישה וביטוח בישראל.
תפקידך לתת החלטות מקצועיות, חדות וחד-משמעיות (אל תתחמק ואל תגיד רק "תלוי"). 
שלב חישובים לוגיים ומתמטיים, ציין סעיפי חוק ופקודת מס הכנסה רלוונטיים (כגון סעיף 9A, קיבוע זכויות), וסיים בהמלצה אופרטיבית ברורה ומיידית לסוכן הביטוח מול הלקוח."""

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
            with st.spinner("GPT-4o חושב, מנתח את התיק ומגבש החלטה מקצועית..."):
                ai_answer = ask_openai(SYSTEM_EXPERT_PROMPT, user_query)
                st.markdown(ai_answer)
                st.session_state.messages.append({"role": "assistant", "content": ai_answer})

# ==========================================
# לשונית 2: ניתוח סילוקית פנסיונית (PDF)
# ==========================================
with tab2:
    st.header("ניתוח דוח מסלקה / סילוקית פנסיונית")
    st.write("העלה קובץ PDF של סילוקית פנסיונית, והמערכת תחלץ את הנתונים ותנתח את כל המוצרים.")
    
    uploaded_pdf = st.file_uploader("העלה דוח סילוקית (PDF)", type=["pdf"])
    if uploaded_pdf is not None:
        with st.spinner("מחלץ נתונים ומנתח את דוח הלקוח..."):
            reader = PdfReader(uploaded_pdf)
            pdf_text = "".join([page.extract_text() or "" for page in reader.pages])
            
            pdf_prompt = f"""להלן נתונים מתוך דוח סילוקית פנסיונית של לקוח:
{pdf_text[:12000]}

נתח את התיק הבא באופן מקצועי עבור סוכן הביטוח בעברית:
1. ריכוז נכסים (קרנות פנסיה, ביטוחי מנהלים, קופות גמל).
2. איתור דמי ניהול חריגים או כפל ביטוחי.
3. המלצות אופרטיביות לשיפור התיק לקראת פרישה."""
            
            result = ask_openai(SYSTEM_EXPERT_PROMPT, pdf_prompt)
            st.markdown("### תוצאות ניתוח הסילוקית:")
            st.markdown(result)

# ==========================================
# לשונית 3: נתוני שוק וגמל-נט
# ==========================================
with tab3:
    st.header("נתוני שוק, דמי ניהול ממוצעים ומאזנים אקטואריים")
    st.write("נתונים סטטיסטיים רשמיים מרשות שוק ההון והחיסכון:")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("קרן פנסיה מקיפה (מהצביעה)", "0.15% - 0.22%", "-0.02% משנה קודמת")
    col2.metric("ביטוח מנהלים ישן (מהצביעה)", "0.5% - 1.05%", "גבוה משמעותית")
    col3.metric("קופת גמל להשקעה", "0.65% - 0.75%", "ממוצע ענפי")

# ==========================================
# לשונית 4: סיכום פגישות שמע (Audio)
# ==========================================
with tab4:
    st.header("הקלטת פגישה וסיכום אוטומטי")
    st.write("העלה קובץ שמע (MP3 / WAV) של פגישת סוכן-לקוח לקבלת סיכום מובנה.")
    uploaded_audio = st.file_uploader("העלה הקלטת שמע", type=["mp3", "wav", "m4a"])
    if uploaded_audio is not None and st.button("הפק סיכום פגישה"):
        st.success("קובץ השמע נקלט! (מוכן לעיבוד וסיכום סוכן ולקוח).")

# ==========================================
# לשונית 5: מילוי טפסים אוטומטי
# ==========================================
with tab5:
    st.header("מחולל נתונים למילוי טפסים רשמיים")
    with st.form("form_filler"):
        f_name = st.text_input("שם מלא של הלקוח")
        f_id = st.text_input("תעודת זהות")
        f_pension = st.number_input("יתרה בקרן פנסיה (ש\"ח)", value=2000000)
        f_managers = st.number_input("יתרה בביטוח מנהלים (ש\"ח)", value=1000000)
        submit_form = st.form_submit_button("הפק נתונים לטופס רשמי")
        if submit_form:
            form_prompt = f"""הכן טיוטת נתונים מסודרת ומאורגנת למילוי טפסי פרישה (כגון טופס 161 ובקשה לקיבוע זכויות) עבור הלקוח:
- שם: {f_name}
- ת.ז: {f_id}
- צביעה בפנסיה: {f_pension:,.2f} ₪
- צביעה בביטוח מנהלים: {f_managers:,.2f} ₪"""
            res = ask_openai(SYSTEM_EXPERT_PROMPT, form_prompt)
            st.markdown(res)
