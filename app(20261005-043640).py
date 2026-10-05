import re
from pathlib import Path
import streamlit as st
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

st.set_page_config(
    page_title="AI HRSG Fault & Maintenance Assistant",
    page_icon="⚙️",
    layout="wide"
)

# -------------------- STYLING --------------------
st.markdown("""
<style>
.stApp {
    background: linear-gradient(135deg,#071a35 0%,#12385a 50%,#123f4f 100%);
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg,#0b2340,#163b5b);
}
h1,h2,h3,h4,p,label,.stMarkdown {color:#f5f9ff;}
.hero {
    padding: 25px 30px;
    border-radius: 20px;
    background: linear-gradient(110deg,#008b8b,#2466b3 60%,#7044a8);
    box-shadow: 0 10px 30px #0005;
    margin-bottom: 20px;
}
.hero h1 {color:white;margin:0;font-size:2rem;}
.hero p {color:#e9f7ff;margin:7px 0 0;}
.card {
    padding:18px;
    border-radius:15px;
    background:#173b59;
    border:1px solid #2e6985;
    margin-bottom:12px;
}
.warning {
    padding:15px;
    border-radius:10px;
    background:#5a3618;
    border-left:5px solid #ffbd45;
    color:#fff3d5;
}
.successbox {
    padding:15px;
    border-radius:10px;
    background:#104d4b;
    border-left:5px solid #35dfc4;
}
.info-box {
    padding:15px;
    border-radius:10px;
    background:#173b68;
    border-left:5px solid #54a8ff;
}
div.stButton>button {
    background:linear-gradient(90deg,#00a6a6,#3479d3);
    color:white;
    border:0;
    border-radius:10px;
    font-weight:700;
}
div.stButton>button:hover {
    background:linear-gradient(90deg,#17c9b5,#5898ef);
    color:white;
}
.metric {
    background:#173b59;
    border:1px solid #34718c;
    border-radius:14px;
    padding:14px;
    text-align:center;
}
.metric .value {font-size:1.15rem;font-weight:bold;color:#72e3d2;}
.metric .label {font-size:.78rem;color:#d5e7f8;}
.small {font-size:.82rem;color:#bcd4ea;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
<h1>⚙️ AI HRSG Fault Diagnosis & Maintenance Assistant</h1>
<p>Offline • API-Free • Manual-Grounded Engineering Decision Support</p>
</div>
""", unsafe_allow_html=True)

# -------------------- DATA --------------------
EQUIPMENT = [
    "Not specified","HP System","IP System","LP System",
    "HP Drum","IP Drum","LP Drum",
    "HP Economizer","IP Economizer","LP Economizer",
    "HP Evaporator","IP Evaporator","LP Evaporator",
    "HP Superheater","IP Superheater","Reheater",
    "Attemperator System","Feedwater Pump","Recirculation Pump",
    "Blowdown System","Drain System","Vent System","Safety Valves",
    "Temperature Monitoring","Drum Level Monitoring",
    "HRSG Inlet Duct","HRSG Outlet Duct",
    "Expansion System","Stack","Insulation","Auxiliary Equipment"
]

FAULT_KEYWORDS = {
    "temperature": ["temperature","hot","overheat","overheating","thermal"],
    "pressure": ["pressure","high pressure","low pressure","pressurized"],
    "level": ["level","drum level","water level"],
    "flow": ["flow","low flow","high flow","circulation"],
    "pump": ["pump","feedwater pump","recirculation pump"],
    "valve": ["valve","control valve","safety valve"],
    "vibration": ["vibration","shaking","oscillation"],
    "leakage": ["leak","leakage","dripping"],
    "instrument": ["sensor","transmitter","instrument","indication","reading"],
    "steam": ["steam","superheated steam","reheater"],
    "feedwater": ["feedwater","feed water"],
    "attemperator": ["attemperator","spray water","spray valve"],
    "economizer": ["economizer"],
    "evaporator": ["evaporator"],
    "superheater": ["superheater"],
    "drum": ["drum"],
}

def extract_pdf(file_bytes):
    import io
    reader = PdfReader(io.BytesIO(file_bytes))
    pages=[]
    for page_no,page in enumerate(reader.pages,1):
        try:
            text=page.extract_text() or ""
        except Exception:
            text=""
        text=re.sub(r"\s+"," ",text).strip()
        if text:
            pages.append({"page":page_no,"text":text})
    return pages

def make_chunks(pages, size=1300, overlap=220):
    chunks=[]
    for item in pages:
        text=item["text"]
        start=0
        while start < len(text):
            part=text[start:start+size].strip()
            if part:
                chunks.append({"page":item["page"],"text":part})
            start += max(1,size-overlap)
    return chunks

@st.cache_data(show_spinner=False)
def build_index(pdf_bytes):
    pages=extract_pdf(pdf_bytes)
    chunks=make_chunks(pages)
    if not chunks:
        return [], None
    texts=[x["text"] for x in chunks]
    vectorizer=TfidfVectorizer(
        stop_words="english",
        ngram_range=(1,2),
        max_features=30000
    )
    matrix=vectorizer.fit_transform(texts)
    return chunks, (vectorizer,matrix)

def retrieve(query,chunks,index,k=6):
    if not chunks or index is None:
        return []
    vectorizer,matrix=index
    try:
        q=vectorizer.transform([query])
        scores=cosine_similarity(q,matrix).ravel()
        ids=scores.argsort()[::-1][:k]
        return [(chunks[i],float(scores[i])) for i in ids if scores[i] > 0.01]
    except Exception:
        return []

def detect_topics(question):
    q=question.lower()
    found=[]
    for topic,words in FAULT_KEYWORDS.items():
        if any(w in q for w in words):
            found.append(topic)
    return found

def local_diagnosis(question, equipment, condition, severity, hits):
    q=question.lower()
    topics=detect_topics(question)

    if "critical" in q or severity=="Critical":
        sev="CRITICAL"
    elif "high" in q or severity=="High":
        sev="HIGH"
    elif severity=="Medium":
        sev="MEDIUM"
    else:
        sev="REQUIRES FIELD ASSESSMENT"

    # Evidence-driven response: no invented setpoints or repair values.
    cause_map={
        "temperature":"temperature measurement, heat-transfer condition, steam-side condition, or temperature-control/attemperation condition should be investigated using the applicable manual section.",
        "pressure":"pressure indication, upstream/downstream operating condition, valve/control condition, and the relevant pressure-protection information should be checked.",
        "level":"level indication/transmitter condition, feedwater/steam-water balance, and the relevant drum level system should be investigated.",
        "flow":"pump condition, valve position, flow indication, and upstream/downstream restrictions should be checked against the manual.",
        "pump":"pump operating condition, suction/discharge condition, valve status, instrumentation and related system information should be checked.",
        "valve":"valve indication/position, control signal, associated process conditions and applicable maintenance information should be checked.",
        "vibration":"equipment condition, rotating equipment status, mechanical support and applicable inspection requirements should be checked by qualified personnel.",
        "leakage":"identify the source and boundary, maintain safe distance, isolate/depressurize only under approved procedures, and inspect the relevant component.",
        "instrument":"compare the indication with available independent/approved indications and inspect the relevant instrument/transmitter system.",
        "attemperator":"check the attemperating-water system, spray/control-valve indication and relevant temperature indications using the manual.",
        "feedwater":"review feedwater flow, pump condition, valves and associated economizer/drum flow path.",
        "drum":"review drum level/pressure/temperature indications and associated feedwater/steam-water flow path.",
        "economizer":"review economizer inlet/outlet conditions, flow path and relevant temperature/heat-transfer information.",
        "evaporator":"review evaporator circulation, drum relationship and operating conditions described in the manual.",
        "superheater":"review steam temperature, flow path and associated temperature-control arrangements.",
        "steam":"review the applicable steam system, temperature/pressure indications and associated control equipment."
    }

    causes=[]
    for t in topics:
        if t in cause_map:
            causes.append(cause_map[t])

    if not causes:
        causes=["The symptom is not specific enough to establish a probable root cause. Use the retrieved manual evidence and provide equipment, alarm, trend, and measured parameter information."]

    safety = (
        "STOP/ESCALATE if the condition involves uncontrolled pressure/steam release, fire, severe overheating, "
        "electrical danger, rotating equipment danger, or another immediate hazard. Follow approved plant emergency, "
        "permit-to-work, isolation/LOTO and OEM procedures. Never bypass protection or change safety settings."
    )

    checks=[
        "Confirm the equipment tag/system and operating condition.",
        "Record the actual alarm/symptom and available measured value or trend.",
        "Compare the indication with the approved plant/OEM operating information; this application does not invent alarm limits or setpoints.",
        "Inspect/review the relevant component and connected upstream/downstream system using the manual.",
        "If an instrument reading is suspected, verify it using an approved independent indication or maintenance procedure.",
        "Escalate to qualified maintenance/control-room personnel when physical intervention is required."
    ]

    maintenance=[
        "Use the retrieved manual sections as the technical reference for the affected equipment.",
        "Perform isolation, depressurization and electrical/mechanical safety steps only according to approved site/OEM procedures.",
        "Do not perform a component repair or replacement unless the applicable detailed maintenance procedure authorizes it.",
        "After authorized work, perform the approved functional/return-to-service verification and document the result."
    ]

    return {
        "severity":sev,
        "topics":topics,
        "causes":causes,
        "checks":checks,
        "maintenance":maintenance,
        "safety":safety,
        "verification":"Verify the original symptom has cleared using approved plant instrumentation, alarms/trends and the applicable commissioning/return-to-service procedure.",
    }

# -------------------- SIDEBAR --------------------
with st.sidebar:
    st.markdown("## 🧭 Workspaces")
    page=st.radio(
        "Select module",
        [
            "🔎 Fault Diagnosis",
            "🛠️ Maintenance Assistant",
            "📚 Manual Search",
            "🏭 Equipment Explorer",
            "🛡️ Safety Assistant",
            "📊 Diagnostic History",
            "ℹ️ About"
        ]
    )
    st.markdown("---")
    st.markdown("### 📄 HRSG O&M Manual")

    uploaded=st.file_uploader(
        "Upload PDF Manual",
        type=["pdf"],
        help="The manual is processed locally during this Streamlit session."
    )

    default_pdf=Path(__file__).parent/"HRSG_Manual.pdf"

    if uploaded:
        pdf_bytes=uploaded.getvalue()
        pdf_name=uploaded.name
    elif default_pdf.exists():
        pdf_bytes=default_pdf.read_bytes()
        pdf_name=default_pdf.name
    else:
        pdf_bytes=None
        pdf_name=""

    if pdf_bytes:
        try:
            chunks,index=build_index(pdf_bytes)
            st.success(f"Manual ready\n{len(chunks)} searchable chunks")
            st.caption(pdf_name)
        except Exception as e:
            chunks=[]
            index=None
            st.error(f"PDF processing error: {e}")
    else:
        chunks=[]
        index=None
        st.warning("Upload HRSG_Manual.pdf to activate manual-grounded analysis.")

    st.markdown("---")
    st.caption("🔒 No API key • No external AI service • Local retrieval")

# -------------------- RESULT FUNCTION --------------------
def show_local_result(question,equipment,condition,severity):
    if not question.strip():
        st.warning("Please enter a fault, symptom, alarm, or maintenance question.")
        return

    hits=retrieve(question,chunks,index,6)

    if not hits:
        st.error("No sufficiently relevant information was found in the uploaded manual. Try using the equipment name, symptom, alarm, pressure/temperature/flow/level term, or consult the applicable OEM/site procedure.")
        return

    result=local_diagnosis(question,equipment,condition,severity,hits)

    st.markdown("### 📋 Diagnostic Summary")
    c1,c2,c3,c4=st.columns(4)
    with c1:
        st.markdown(f'<div class="metric"><div class="value">{result["severity"]}</div><div class="label">SEVERITY</div></div>',unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="metric"><div class="value">{equipment}</div><div class="label">EQUIPMENT</div></div>',unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="metric"><div class="value">{condition}</div><div class="label">CONDITION</div></div>',unsafe_allow_html=True)
    with c4:
        confidence="Medium" if len(hits)>=3 else "Low"
        st.markdown(f'<div class="metric"><div class="value">{confidence}</div><div class="label">RETRIEVAL CONFIDENCE</div></div>',unsafe_allow_html=True)

    st.markdown("#### 🔍 Fault / Affected System")
    st.write(question)
    st.write("Detected technical topics:", ", ".join(result["topics"]) if result["topics"] else "General fault")

    st.markdown("#### 🧠 Possible Causes / Investigation Areas")
    for x in result["causes"]:
        st.markdown(f"- {x}")

    st.markdown("#### 🔧 Troubleshooting Checks")
    for i,x in enumerate(result["checks"],1):
        st.markdown(f"**{i}.** {x}")

    st.markdown("#### 🛠️ Maintenance Guidance")
    for x in result["maintenance"]:
        st.markdown(f"- {x}")

    st.markdown("#### 🛡️ Safety Warning")
    st.markdown(f'<div class="warning">⚠️ {result["safety"]}</div>',unsafe_allow_html=True)

    st.markdown("#### ✅ Verification")
    st.markdown(result["verification"])

    st.markdown("### 📖 Manual Evidence")
    for item,score in hits:
        with st.expander(f"Manual Page {item['page']}  |  Retrieval Score {score:.2f}",expanded=False):
            st.write(item["text"])

    st.session_state.setdefault("history",[]).insert(
        0,{"question":question,"equipment":equipment,"severity":result["severity"]}
    )

# -------------------- PAGES --------------------
if page=="🔎 Fault Diagnosis":
    st.subheader("🔎 Autonomous Fault Diagnosis")
    a,b,c=st.columns(3)
    with a:
        equipment=st.selectbox("Equipment / Subsystem",EQUIPMENT)
    with b:
        condition=st.selectbox("Operating Condition",["Unknown","Startup","Normal Operation","Shutdown"])
    with c:
        severity=st.selectbox("Reported Severity",["Unknown","Low","Medium","High","Critical"])

    question=st.text_area(
        "Describe the machine fault / abnormal condition",
        height=150,
        placeholder="Example: HP steam temperature is increasing and the attemperator does not appear to control it..."
    )

    if st.button("⚡ ANALYZE FAULT",use_container_width=True):
        show_local_result(question,equipment,condition,severity)

    st.markdown("### 💡 Example Faults")
    examples=[
        "HP drum level is abnormally low",
        "HP steam temperature is too high",
        "Feedwater flow is low",
        "Feedwater pump pressure is dropping",
        "Attemperator is not controlling steam temperature",
        "Drum level transmitter is giving abnormal readings"
    ]
    cols=st.columns(2)
    for i,q in enumerate(examples):
        with cols[i%2]:
            if st.button(q,key=f"fault_{i}",use_container_width=True):
                show_local_result(q,"Not specified","Unknown","Unknown")

elif page=="🛠️ Maintenance Assistant":
    st.subheader("🛠️ Maintenance Assistant")
    mode=st.selectbox(
        "Maintenance Activity",
        ["Corrective Maintenance","Preventive Maintenance","Fault Troubleshooting",
         "Inspection","Safety Check","Component Replacement","Operational Abnormality","Emergency Condition"]
    )
    equipment=st.selectbox("Equipment",EQUIPMENT,key="maintenance_equipment")
    condition=st.selectbox("Operating Condition",["Unknown","Startup","Normal Operation","Shutdown"],key="maintenance_condition")
    severity=st.selectbox("Severity",["Unknown","Low","Medium","High","Critical"],key="maintenance_severity")
    question=st.text_area(
        "Describe the maintenance requirement",
        height=130,
        placeholder="Example: Need troubleshooting guidance for abnormal HP feedwater flow."
    )
    if st.button("🧰 GENERATE MAINTENANCE GUIDANCE",use_container_width=True):
        show_local_result(f"{mode}: {question}",equipment,condition,severity)

    with st.expander("📋 General Work Checklist"):
        checklist=[
            "Confirm equipment identification and approved work scope.",
            "Obtain required permit and authorized isolation.",
            "Identify pressure, temperature, electrical, mechanical and other hazards.",
            "Apply approved LOTO/isolation and verify safe condition.",
            "Use the applicable OEM/site maintenance procedure.",
            "Record as-found readings and physical condition.",
            "Perform only authorized maintenance.",
            "Complete inspection and functional verification.",
            "Follow approved return-to-service procedure.",
            "Document the maintenance result."
        ]
        for item in checklist:
            st.checkbox(item,key="mc_"+str(abs(hash(item))))

elif page=="📚 Manual Search":
    st.subheader("📚 HRSG Manual Search")
    query=st.text_input(
        "Search the manual",
        placeholder="Example: HP drum, attemperator, feedwater system, safety precautions"
    )
    k=st.slider("Number of results",3,10,6)
    if st.button("🔎 SEARCH",use_container_width=True):
        hits=retrieve(query,chunks,index,k)
        if not hits:
            st.warning("No relevant text found.")
        for item,score in hits:
            with st.expander(f"📄 Page {item['page']} | Score {score:.2f}",expanded=True):
                st.write(item["text"])

elif page=="🏭 Equipment Explorer":
    st.subheader("🏭 Equipment Explorer")
    equipment=st.selectbox("Select equipment/system",EQUIPMENT[1:],key="equipment_explorer")
    if st.button("🔍 EXPLORE MANUAL",use_container_width=True):
        query=f"{equipment} operation system flow maintenance inspection troubleshooting"
        hits=retrieve(query,chunks,index,7)
        if not hits:
            st.warning("No relevant manual content found.")
        for item,score in hits:
            with st.expander(f"Page {item['page']} | Score {score:.2f}",expanded=True):
                st.write(item["text"])

elif page=="🛡️ Safety Assistant":
    st.subheader("🛡️ Safety-First Assistant")
    st.markdown("""
    <div class="warning">
    <b>IMPORTANT:</b> This application must never be used as authorization to operate, isolate,
    open, repair, or return HRSG equipment to service. Follow approved plant/OEM procedures.
    </div>
    """,unsafe_allow_html=True)

    q=st.text_area(
        "What safety topic do you want to check?",
        height=120,
        placeholder="Example: Safety precautions before HRSG maintenance"
    )
    if st.button("🛡️ FIND SAFETY INFORMATION",use_container_width=True):
        show_local_result(q,"Not specified","Unknown","Unknown")

    st.markdown("### Core Safety Reminders")
    for x in [
        "Follow permit-to-work and plant isolation procedures.",
        "Confirm required energy sources are isolated by authorized personnel.",
        "Treat pressure, steam and hot surfaces as hazardous.",
        "Use required PPE and approved access/fall protection.",
        "Do not bypass alarms, interlocks or protection systems.",
        "Do not change safety-valve/protection settings without authorized OEM/plant approval.",
        "For an emergency, follow the plant emergency response procedure."
    ]:
        st.markdown(f"- {x}")

elif page=="📊 Diagnostic History":
    st.subheader("📊 Current Session Diagnostic History")
    history=st.session_state.get("history",[])
    if not history:
        st.info("No diagnostic activity in this session.")
    else:
        for i,item in enumerate(history,1):
            st.markdown(
                f"**{i}. {item['equipment']} — {item['severity']}**  \n"
                f"{item['question']}"
            )
            st.divider()

elif page=="ℹ️ About":
    st.subheader("ℹ️ About the Application")
    st.markdown("""
    ### ⚙️ What this version does

    This is an **API-free** Streamlit application.

    **Workflow:**

    PDF Manual
    → Text Extraction
    → Page-Based Chunking
    → TF-IDF Index
    → Similarity Retrieval
    → Relevant Manual Evidence
    → Rule-Based Diagnostic Guidance
    → Maintenance & Safety Guidance

    ### 🔒 No API Required

    This version does **not** require:
    - OpenAI API
    - Groq API
    - Gemini API
    - Hugging Face API
    - Any secret key

    ### ⚠️ Engineering Limitation

    Without an external LLM, this version cannot perform unrestricted natural-language reasoning like ChatGPT. It instead combines manual retrieval with transparent, rule-based engineering guidance. This is intentional so the application can run on Streamlit without API credentials.

    For actual plant work, all recommendations must be checked against the approved O&M/OEM/site procedures and qualified personnel.
    """)
