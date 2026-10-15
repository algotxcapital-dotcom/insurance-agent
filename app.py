import streamlit as st
import os
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

st.set_page_config(page_title="סוכן AI לביטוח ופנסיה", page_icon="🤖")
st.title("🤖 סוכן AI מומחה לביטוח, פנסיה והשקעות בישראל")
st.write("שאל כל שאלה בנושאי ביטוח, חקיקה, מיסוי פנסיוני ותיקים מורכבים.")

openai_api_key = st.text_input("הכנס את ה-OpenAI API Key שלך כאן:", type="password")

if not openai_api_key:
    st.warning("אנא הכנס מפתח API כדי להתחיל להשתמש בסוכן.")
else:
    KNOWLEDGE_BASE = [
        Document(
            page_content="סעיף 43 לחוק חוזה הביטוח, התשמ\"א-1981: מבטח אינו רשאי להסתמך על אי-גילוי או על תשובה שאינה מלאה או כנה לאחר שעברו שלוש שנים מכריתת החוזה, זולת אם הוכח שהמבוטח פעל בכוונת מרמה ממשית. שינוי במצב בריאותי שאירע לאחר כריתת החוזה אינו מחייב הודעה למבטח.",
            metadata={"source": "חוק חוזה הביטוח", "section": "43"}
        ),
        Document(
            page_content="סעיף 33 לחוק חוזה הביטוח: לעניין המשא ומתן לקראת כריתת חוזה הביטוח ולעניין כריתת החוזה, יראו את סוכן הביטוח כשלוחו של המבטח, זולת אם פעל כשלוחו של המבוטח לפי דרישתו בכתב. מצג שהציג סוכן הביטוח למבוטח בעת הרכישה מחייב את חברת הביטוח מכוח שליחות בדין.",
            metadata={"source": "חוק חוזה הביטוח", "section": "33"}
        ),
        Document(
            page_content="פקודת מס הכנסה - תיקון 190 (קופת גמל לתגמולים במעמד עצמאי): מאפשר הפקדת כספים פרטיים לקופת גמל. תנאי זכאות למשיכה כסכום הוני חד-פעמי: הגעה לגיל 60 ומעלה וקבלת קצבה חודשית מזכה בפועל (מעל סכום המינימום לפי חוק, כ-5,012 ש\"ח). שיעור המס במשיכה הונית הוא 15% נומינלי בלבד על הרווחים (במקום 25% ריאלי).",
            metadata={"source": "פקודת מס הכנסה", "section": "תיקון 190"}
        )
    ]

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=openai_api_key)
    vectorstore = Chroma.from_documents(documents=KNOWLEDGE_BASE, embedding=embeddings)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 2})

    SYSTEM_PROMPT = """אתה סוכן AI מומחה בכיר לביטוח, פנסיה והשקעות בישראל בלבד.
    ענה בהסתמך על החוק והמקורות המצורפים, וציין את סעיפי החוק. אם אינך יודע, אמור זאת.
    
    מידע חוקי:
    {context}
    
    שאלה:
    {question}"""

    prompt = ChatPromptTemplate.from_template(SYSTEM_PROMPT)
    llm = ChatOpenAI(model="gpt-4o", temperature=0, openai_api_key=openai_api_key)

    def format_docs(docs):
        return "\n\n".join(f"[{doc.metadata.get('source')} | סעיף {doc.metadata.get('section')}]:\n{doc.page_content}" for doc in docs)

    rag_chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if user_query := st.chat_input("הקלד כאן את השאלה או מקרה הבוחן שלך..."):
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)

        with st.chat_message("assistant"):
            with st.spinner("מנתח את התיק מול חוקי הביטוח..."):
                response = rag_chain.invoke(user_query)
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
