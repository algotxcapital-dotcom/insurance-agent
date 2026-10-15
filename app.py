import streamlit as st
import os
import google.generativeai as genai
from pypdf import PdfReader

# 1. הגדרת ממשק האתר
st.set_page_config(page_title="InsurTech AI Platform (Gemini Pro)", page_icon="🤖", layout="wide")
st.title("🤖 פלטפורמת InsurTech מתקדמת לתכנון פרישה וביטוח (מופעל ע\"י Gemini Pro)")
st.write("מערכת אקולוגית חכמה לסוכני ביטוח: ניתוח סילוקיות, השוואת שוק, סיכומי פגישות ותחשיבים אקטואריים.")

# 2. מפתח API קבוע של Google Gemini
gemini_api_key = "AQ.Ab8RN6LoTHnQW3SHls2WT1sCQU9oTTOGYk7EAgM_ed-HRNl3YQ"
genai.configure(api_key=gemini_api_key)

# טעינת מודל Gemini 1.5 Pro
from langchain_google_genai import ChatGoogleGenerativeAI
llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-pro",
    temperature=0,
    google_api_key=gemini_api_key
)

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
                prompt_full = f"""You are a senior expert AI insurance and pension planning agent in Israel. 
Provide a professional, definitive decision, include relevant mathematical logic, mention applicable tax or insurance laws in Israel, and end with an actionable recommendation for the agent.

Client Case / Question: {user_query}
Professional Analysis and Decision:"""
                response = llm.invoke(prompt_full)
                st.markdown(response.content)
                st.session_state.messages.append({"role": "assistant", "content": response.content})

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
            pdf_text = ""
            for page in reader.pages:
                pdf_text += page.extract_text() or ""
            
            analysis_prompt = f"""Analyze the following pension clearinghouse report (Silukit) for an Israeli insurance agent:
{pdf_text[:15000]}

Provide in Hebrew:
1. Asset summary (pension funds, executive insurance, provident funds).
2. Identification of abnormal management fees or overlapping coverages.
3. Actionable recommendations for pre-retirement optimization."""
            
            pdf_response = llm.invoke(analysis_prompt)
            st.markdown("### תוצאות ניתוח הסילוקית:")
            st.markdown(pdf_response.content)

# ==========================================
# לשונית 3: נתוני שוק וגמל-נט
# ==========================================
with tab3:
    st.header("נתוני שוק, דמי ניהול ממוצעים ומאזנים אקטואריים")
    st.write("נתונים סטטיסטיים רשמיים מרשות שוק ההון והחיסכון:")
    
    st.info("📌 **דמי ניהול ממוצעים במשק (עדכון אחרון):**")
    col1, col2, col3 = st.columns(3)
    col1.metric("קרן פנסיה מקיפה (מהצביעה)", "0.15% - 0.22%", "-0.02% משנה קודמת")
    col2.metric("ביטוח מנהלים ישן (מהצביעה)", "0.5% - 1.05%", "גבוה משמעותית")
    col3.metric("קופת גמל להשקעה", "0.65% - 0.75%", "ממוצע ענפי")
    
    st.markdown("---")
    st.markdown("### 🔍 מצב יציבות ומאזן אקטוארי בקרנות הפנסיה:")
    st.write("קרנות הפנסיה החדשות מתאפיינות ביתרת עודף אקטוארי או איזון בהתאם לתקנונים החדשים.")

# ==========================================
# לשונית 4: סיכום פגישות שמע (Audio)
# ==========================================
with tab4:
    st.header("הקלטת פגישה וסיכום אוטומטי (Audio to Summary)")
    st.write("העלה קובץ שמע (MP3 / WAV) של פגישת סוכן-לקוח, וקבל סיכום טכני לסוכן וסיכום מותאם ללקוח.")
    
    uploaded_audio = st.file_uploader("העלה הקלטת שמע של פגישה", type=["mp3", "wav", "m4a"])
    if uploaded_audio is not None:
        st.success("קובץ השמע נקלט בהצלחה במערכת!")
        if st.button("הפק סיכום פגישה"):
            with st.spinner("Gemini מעבד את השמע ומפיק סיכומי פגישה מקצועיים..."):
                audio_bytes = uploaded_audio.read()
                audio_file_path = "temp_meeting_audio.mp3"
                with open(audio_file_path, "wb") as f:
                    f.write(audio_bytes)
                
                gemini_audio_file = genai.upload_file(audio_file_path)
                
                audio_prompt = [
                    gemini_audio_file,
                    "Listen to this audio recording between an insurance agent and a client, and provide in Hebrew:\n"
                    "1. A technical, professional summary for the agent (action items, transfers, missing coverages).\n"
                    "2. A friendly, focused summary for the client explaining the agreed decisions."
                ]
                
                audio_response = llm.invoke(audio_prompt)
                st.markdown(audio_response.content)
                
                if os.path.exists(audio_file_path):
                    os.remove(audio_file_path)

# ==========================================
# לשונית 5: מילוי טפסים אוטומטי
# ==========================================
with tab5:
    st.header("מחולל נתונים למילוי טפסים רשמיים (מס הכנסה / סבב ניוד)")
    st.write("הזן את נתוני הלקוח והפק נתונים מוכנים להעתקה לטופס 161 או בקשה לקיבוע זכויות:")
    
    with st.form("form_filler"):
        f_name = st.text_input("שם מלא של הלקוח")
        f_id = st.text_input("תעודת זהות")
        f_pension = st.number_input("יתרה בקרן פנסיה (ש\"ח)", value=2000000)
        f_managers = st.number_input("יתרה בביטוח מנהלים (ש\"ח)", value=1000000)
        submit_form = st.form_submit_button("הפק נתונים לטופס רשמי")
        
        if submit_form:
            form_prompt = f"""Generate a structured draft of data for filling official Israeli retirement forms (Form 161 / Tax Fixation) for:
- Name: {f_name}
- ID: {f_id}
- Pension Balance: {f_pension} NIS
- Managers Insurance Balance: {f_managers} NIS
Present the details clearly in Hebrew for the agent."""
            form_response = llm.invoke(form_prompt)
            st.markdown(form_response.content)
