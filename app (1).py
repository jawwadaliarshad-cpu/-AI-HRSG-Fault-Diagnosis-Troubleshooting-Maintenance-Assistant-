import streamlit as st
import os
import re
import math

# ============================================================
# HRSG Fault Diagnosis, Troubleshooting & Maintenance Assistant
# API-FREE VERSION
# Uses local PDF retrieval + rule-based engineering guidance.
# ============================================================

st.set_page_config(
    page_title="HRSG Fault Diagnosis Assistant",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------
# Colorful UI
# -----------------------------
st.markdown("""
<style>
.stApp {
    background: linear-gradient(135deg, #eef7ff 0%, #f8fbff 45%, #eefaf5 100%);
}
.main-title {
    background: linear-gradient(90deg, #0b5ed7, #087f5b);
    color: white;
    padding: 18px 22px;
    border-radius: 16px;
    margin-bottom: 18px;
}
.card {
    background: white;
    border-radius: 14px;
    padding: 18px;
    margin: 10px 0;
    box-shadow: 0 3px 14px rgba(0,0,0,.08);
    border-left: 5px solid #0b5ed7;
}
.warning-card {
    background: #fff8e6;
    border-left: 5px solid #f59f00;
    padding: 15px;
    border-radius: 12px;
}
.danger-card {
    background: #fff0f0;
    border-left: 5px solid #dc3545;
    padding: 15px;
    border-radius: 12px;
}
.success-card {
    background: #effaf3;
    border-left: 5px solid #198754;
    padding: 15px;
    border-radius: 12px;
}
.small-muted {
    color: #666;
    font-size: 0.9rem;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# PDF ENGINE
# ============================================================
def extract_pdf_pages(pdf_bytes):
    """
    Imports pypdf only when actually needed.
    This avoids an application-level import crash and gives
    a useful message if Streamlit has not installed pypdf.
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError(
            "PDF engine is unavailable. Please make sure requirements.txt "
            "contains: pypdf>=5.0,<7.0 and redeploy the Streamlit app."
        ) from exc

    try:
        import io
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []

        for page_no, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            text = re.sub(r"\s+", " ", text).strip()

            if text:
                pages.append({
                    "page": page_no,
                    "text": text
                })

        return pages

    except Exception as exc:
        raise RuntimeError(f"Could not read the PDF file: {exc}") from exc


def chunk_pages(pages, chunk_words=260, overlap_words=50):
    chunks = []

    for item in pages:
        words = item["text"].split()

        if not words:
            continue

        start = 0

        while start < len(words):
            end = min(start + chunk_words, len(words))
            text = " ".join(words[start:end])

            chunks.append({
                "page": item["page"],
                "text": text
            })

            if end >= len(words):
                break

            start = max(0, end - overlap_words)

    return chunks


# ============================================================
# LOCAL RETRIEVAL
# ============================================================
@st.cache_resource(show_spinner=False)
def build_retriever(chunks):
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except Exception as exc:
        raise RuntimeError(
            "scikit-learn is unavailable. Please redeploy after installing "
            "the supplied requirements.txt."
        ) from exc

    texts = [c["text"] for c in chunks]

    if not texts:
        return None

    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        max_features=25000
    )

    matrix = vectorizer.fit_transform(texts)

    return {
        "vectorizer": vectorizer,
        "matrix": matrix,
        "chunks": chunks,
        "cosine_similarity": cosine_similarity
    }


def retrieve(query, retriever, top_k=5):
    if retriever is None:
        return []

    vectorizer = retriever["vectorizer"]
    matrix = retriever["matrix"]
    chunks = retriever["chunks"]
    cosine_similarity = retriever["cosine_similarity"]

    q_vec = vectorizer.transform([query])
    scores = cosine_similarity(q_vec, matrix).flatten()

    ranked = scores.argsort()[::-1][:top_k]

    results = []

    for idx in ranked:
        score = float(scores[idx])

        if score <= 0:
            continue

        results.append({
            "page": chunks[idx]["page"],
            "text": chunks[idx]["text"],
            "score": score
        })

    return results


# ============================================================
# RULE-BASED ENGINEERING GUIDANCE
# ============================================================
TOPIC_RULES = {
    "temperature": [
        "Check the relevant temperature indication and compare with other available indications.",
        "Check temperature transmitters/sensors, signal integrity and associated instrumentation.",
        "Check steam/water flow conditions and heat-transfer conditions around the affected section.",
    ],
    "pressure": [
        "Check pressure indication/transmitter and compare with upstream/downstream conditions.",
        "Inspect possible restrictions, abnormal flow conditions, valve position and pressure-control behavior.",
        "Confirm whether the pressure trend is increasing, decreasing or fluctuating.",
    ],
    "level": [
        "Check drum level indication and compare independent/available level measurements.",
        "Check feedwater flow, steam flow and level-control response.",
        "Investigate transmitter, impulse-line and signal problems before assuming an actual process-level fault.",
    ],
    "flow": [
        "Check the flow indication/transmitter and compare with related pressure and valve conditions.",
        "Inspect pump operation, valve position and possible restrictions.",
        "Check whether the flow change is gradual, sudden or intermittent.",
    ],
    "pump": [
        "Check pump operating condition, suction/discharge pressure and associated flow.",
        "Check for abnormal vibration, noise, leakage or temperature.",
        "Review pump, motor and associated valve/instrument conditions according to the site maintenance procedure.",
    ],
    "valve": [
        "Verify commanded valve position versus actual position.",
        "Check actuator/control signal and associated instrumentation.",
        "Inspect for abnormal leakage, sticking or failure to reach the commanded position.",
    ],
    "vibration": [
        "Check vibration trend and compare with normal operating behavior.",
        "Inspect rotating equipment condition, bearings, alignment and mechanical abnormalities as applicable.",
        "If vibration is severe or rapidly increasing, follow site protection and shutdown procedures.",
    ],
    "leakage": [
        "Identify the exact location and medium involved before intervention.",
        "Check nearby piping, valves, drains, vents, flanges and equipment connections.",
        "Apply plant isolation, depressurization and safety procedures before physical inspection.",
    ],
    "instrument": [
        "Verify the instrument reading against an independent or redundant indication where available.",
        "Check signal wiring, transmitter status and associated control-system indications.",
        "Do not alter calibration or protection settings unless authorized procedures and qualified personnel are involved.",
    ],
    "steam": [
        "Check steam pressure, temperature and flow together rather than treating one indication in isolation.",
        "Review the relevant superheater/reheater/steam-line section and associated drains or controls.",
        "Check for abnormal trends and operating-condition changes.",
    ],
    "feedwater": [
        "Check feedwater flow, pressure, pump operation and control-valve condition.",
        "Review feedwater-related instrumentation and associated economizer/drum conditions.",
        "Check for restrictions, leakage or abnormal pump behavior.",
    ],
    "attemperator": [
        "Check steam temperature and attemperating-water conditions.",
        "Verify the relevant control-valve command/position and instrumentation.",
        "Investigate water supply, valve response and temperature-control behavior.",
    ],
    "economizer": [
        "Check feedwater flow, temperature and pressure around the economizer.",
        "Review associated gas-side/heat-transfer conditions and instrumentation.",
        "Check for abnormal temperature approach or flow behavior.",
    ],
    "evaporator": [
        "Check drum level, circulation/flow indications and steam generation conditions.",
        "Review relevant pressure and temperature trends.",
        "Inspect instrumentation and associated water/steam paths according to approved procedures.",
    ],
    "superheater": [
        "Check steam temperature and pressure trends across the relevant section.",
        "Review attemperating-water/control behavior where applicable.",
        "Check instrumentation and heat-transfer/flow conditions.",
    ],
    "drum": [
        "Check drum level, pressure, temperature and related feedwater/steam conditions.",
        "Compare the indication with available independent measurements.",
        "Investigate level-control, feedwater and instrumentation conditions before physical intervention.",
    ],
}

def detect_topics(text):
    t = text.lower()
    found = []

    keyword_groups = {
        "temperature": ["temperature", "temp", "hot", "overheat", "high temp", "low temp"],
        "pressure": ["pressure", "press", "high pressure", "low pressure", "pressure drop"],
        "level": ["level", "low level", "high level", "drum level"],
        "flow": ["flow", "low flow", "high flow", "flow drop"],
        "pump": ["pump", "pumping", "feedwater pump"],
        "valve": ["valve", "actuator", "control valve"],
        "vibration": ["vibration", "vibrating", "bearing", "noise"],
        "leakage": ["leak", "leakage", "leaking"],
        "instrument": ["transmitter", "sensor", "instrument", "signal", "indication"],
        "steam": ["steam", "superheated", "reheater", "superheater"],
        "feedwater": ["feedwater", "feed water", "boiler feed"],
        "attemperator": ["attemperator", "spray water", "desuperheater"],
        "economizer": ["economizer"],
        "evaporator": ["evaporator"],
        "superheater": ["superheater"],
        "drum": ["drum"],
    }

    for topic, keywords in keyword_groups.items():
        if any(k in t for k in keywords):
            found.append(topic)

    return found or ["instrument", "process"]


def combined_guidance(fault_text, equipment):
    topics = detect_topics(f"{equipment} {fault_text}")

    checks = []
    for topic in topics:
        if topic in TOPIC_RULES:
            checks.extend(TOPIC_RULES[topic])

    # Remove duplicates while preserving order
    unique = []
    for item in checks:
        if item not in unique:
            unique.append(item)

    return topics, unique[:10]


# ============================================================
# SESSION STATE
# ============================================================
if "history" not in st.session_state:
    st.session_state.history = []

if "manual_name" not in st.session_state:
    st.session_state.manual_name = ""

# ============================================================
# HEADER
# ============================================================
st.markdown("""
<div class="main-title">
<h1>🏭 HRSG Fault Diagnosis, Troubleshooting & Maintenance Assistant</h1>
<p>API-Free • Local Manual Retrieval • Rule-Based Engineering Decision Support</p>
</div>
""", unsafe_allow_html=True)

# ============================================================
# SIDEBAR
# ============================================================
st.sidebar.title("⚙️ System Setup")

uploaded = st.sidebar.file_uploader(
    "Upload HRSG Operation & Maintenance Manual",
    type=["pdf"],
    help="Upload the HRSG manual PDF used as the knowledge source."
)

default_pdf = "HRSG_Manual.pdf"

pdf_bytes = None
manual_source = ""

if uploaded is not None:
    pdf_bytes = uploaded.getvalue()
    manual_source = uploaded.name
elif os.path.exists(default_pdf):
    try:
        with open(default_pdf, "rb") as f:
            pdf_bytes = f.read()
        manual_source = default_pdf
    except Exception:
        pdf_bytes = None

if pdf_bytes:
    try:
        pages = extract_pdf_pages(pdf_bytes)
        chunks = chunk_pages(pages)
        retriever = build_retriever(chunks)

        st.sidebar.success(
            f"Manual loaded: {manual_source}\n\n"
            f"Pages with text: {len(pages)}\n"
            f"Search chunks: {len(chunks)}"
        )

        st.session_state.manual_name = manual_source

    except Exception as e:
        st.sidebar.error(str(e))
        pages = []
        chunks = []
        retriever = None
else:
    pages = []
    chunks = []
    retriever = None

st.sidebar.markdown("---")
st.sidebar.info(
    "This version does not require OpenAI, Groq, Gemini or Hugging Face API keys."
)

# ============================================================
# NAVIGATION
# ============================================================
module = st.sidebar.radio(
    "Select Module",
    [
        "🔎 Fault Diagnosis",
        "🛠️ Maintenance Assistant",
        "📚 Manual Search",
        "🏭 Equipment Explorer",
        "🛡️ Safety Assistant",
        "📊 Diagnostic History",
        "ℹ️ About",
    ]
)

# ============================================================
# FAULT DIAGNOSIS
# ============================================================
if module == "🔎 Fault Diagnosis":

    st.subheader("🔎 HRSG Fault Diagnosis")

    col1, col2 = st.columns(2)

    with col1:
        equipment = st.text_input(
            "Equipment / System",
            placeholder="Example: HP Drum, Feedwater Pump, Superheater"
        )

        severity = st.selectbox(
            "Observed Severity",
            ["Normal / Investigation", "Warning", "High", "Critical"]
        )

    with col2:
        operating_condition = st.text_area(
            "Operating Condition",
            placeholder="Example: Unit at 80% load, steam temperature increasing..."
        )

        fault = st.text_area(
            "Describe the Fault / Symptom",
            placeholder="Example: HP drum level is falling below normal..."
        )

    if st.button("🚀 Diagnose Fault", type="primary", use_container_width=True):

        if not fault.strip():
            st.warning("Please enter the observed fault or symptom.")
        else:
            query = f"{equipment} {operating_condition} {fault}"
            topics, checks = combined_guidance(fault, equipment)
            evidence = retrieve(query, retriever, top_k=6)

            st.session_state.history.insert(0, {
                "equipment": equipment,
                "fault": fault,
                "severity": severity,
                "topics": ", ".join(topics)
            })

            st.markdown("### 1. Diagnostic Interpretation")
            st.markdown(
                f'<div class="card"><b>Assessment:</b> '
                f"The symptom is associated with the following investigation areas: "
                f"<b>{', '.join(topics)}</b>. "
                f"This is a preliminary decision-support assessment, not a confirmed equipment failure."
                f"</div>",
                unsafe_allow_html=True
            )

            if severity == "Critical":
                st.markdown(
                    '<div class="danger-card"><b>⚠️ CRITICAL:</b> '
                    'Follow approved plant protection, trip, isolation and emergency procedures. '
                    'Do not bypass protection systems or perform unsafe intervention.</div>',
                    unsafe_allow_html=True
                )
            elif severity == "High":
                st.markdown(
                    '<div class="warning-card"><b>⚠️ HIGH SEVERITY:</b> '
                    'Escalate to qualified operations/maintenance personnel and follow site procedures.</div>',
                    unsafe_allow_html=True
                )

            st.markdown("### 2. Possible Investigation Areas")

            for i, item in enumerate(checks, 1):
                st.write(f"**{i}.** {item}")

            st.markdown("### 3. Manual Evidence")

            if evidence:
                for result in evidence:
                    with st.expander(
                        f"📄 Manual Page {result['page']} | Retrieval score: {result['score']:.2f}"
                    ):
                        st.write(result["text"])
            else:
                st.info(
                    "No sufficiently relevant text was retrieved from the uploaded manual. "
                    "Check the wording or search the manual directly."
                )

            st.markdown("### 4. Verification")

            st.markdown(
                '<div class="success-card">'
                '<b>Recommended verification approach:</b><br>'
                '1. Confirm the symptom using available independent indications.<br>'
                '2. Check related process variables and trends.<br>'
                '3. Inspect instrumentation before assuming a physical equipment failure.<br>'
                '4. Compare observations with the approved operating/maintenance procedure.<br>'
                '5. Record the finding and escalate when intervention is required.'
                '</div>',
                unsafe_allow_html=True
            )

            st.markdown("### 5. Safety Boundary")

            st.markdown(
                '<div class="danger-card">'
                '<b>Important:</b> This application provides engineering decision support only. '
                'It does not authorize maintenance work, isolation, startup/shutdown, '
                'protection-setting changes, calibration changes, or bypassing safety systems. '
                'Use qualified personnel and approved plant/OEM procedures.'
                '</div>',
                unsafe_allow_html=True
            )

# ============================================================
# MAINTENANCE
# ============================================================
elif module == "🛠️ Maintenance Assistant":

    st.subheader("🛠️ Maintenance Assistant")

    equipment = st.text_input(
        "Equipment / Component",
        placeholder="Example: Feedwater Pump / HP Drum / Attemperator"
    )

    maintenance_query = st.text_area(
        "Maintenance Question",
        placeholder="Example: What should I inspect if feedwater pump pressure is dropping?"
    )

    if st.button("🔧 Generate Maintenance Guidance", type="primary"):
        if not maintenance_query.strip():
            st.warning("Please enter a maintenance question.")
        else:
            query = f"{equipment} {maintenance_query}"
            topics, checks = combined_guidance(maintenance_query, equipment)
            evidence = retrieve(query, retriever, top_k=6)

            st.markdown("### Recommended Investigation / Maintenance Areas")

            for i, item in enumerate(checks, 1):
                st.write(f"**{i}.** {item}")

            st.markdown("### Relevant Manual Sections")

            if evidence:
                for result in evidence:
                    with st.expander(
                        f"📄 Page {result['page']} | Score {result['score']:.2f}"
                    ):
                        st.write(result["text"])
            else:
                st.info("No sufficiently relevant manual passage was retrieved.")

            st.markdown(
                '<div class="warning-card">'
                '<b>Maintenance control:</b> Do not invent or assume torque values, '
                'clearances, calibration settings, protection settings, spare-part numbers, '
                'or maintenance intervals. Use the approved manual/site procedure when such '
                'values are required.'
                '</div>',
                unsafe_allow_html=True
            )

# ============================================================
# MANUAL SEARCH
# ============================================================
elif module == "📚 Manual Search":

    st.subheader("📚 Search the HRSG Manual")

    query = st.text_input(
        "Search Query",
        placeholder="Example: HP drum level monitoring"
    )

    top_k = st.slider("Number of results", 1, 10, 5)

    if st.button("🔍 Search Manual", type="primary"):
        if not query.strip():
            st.warning("Enter a search query.")
        elif retriever is None:
            st.error("Please upload the HRSG PDF first.")
        else:
            results = retrieve(query, retriever, top_k=top_k)

            if not results:
                st.info("No matching passage found.")
            else:
                for result in results:
                    st.markdown(
                        f"### 📄 Page {result['page']} "
                        f"(score {result['score']:.2f})"
                    )
                    st.write(result["text"])
                    st.markdown("---")

# ============================================================
# EQUIPMENT EXPLORER
# ============================================================
elif module == "🏭 Equipment Explorer":

    st.subheader("🏭 HRSG Equipment Explorer")

    equipment = st.selectbox(
        "Select Area",
        [
            "HP System",
            "IP System",
            "LP System",
            "Economizer",
            "Evaporator",
            "Superheater",
            "Reheater",
            "Steam Drum",
            "Feedwater System",
            "Feedwater Pump",
            "Attemperating Water System",
            "Safety Valves",
            "Duct / Stack",
            "Expansion System",
        ]
    )

    if retriever:
        results = retrieve(equipment, retriever, top_k=6)

        if results:
            st.success(f"Relevant manual information for: {equipment}")

            for result in results:
                with st.expander(
                    f"📄 Page {result['page']} | Score {result['score']:.2f}"
                ):
                    st.write(result["text"])
        else:
            st.info("No relevant passage found in the current manual.")

# ============================================================
# SAFETY
# ============================================================
elif module == "🛡️ Safety Assistant":

    st.subheader("🛡️ HRSG Safety Assistant")

    st.markdown(
        '<div class="danger-card">'
        '<b>⚠️ Safety First</b><br><br>'
        'HRSG systems contain high-temperature, high-pressure steam and water. '
        'Always follow approved plant safety procedures, isolation/LOTO requirements, '
        'PPE requirements, depressurization procedures and authorized work practices.'
        '</div>',
        unsafe_allow_html=True
    )

    safety_query = st.text_area(
        "Safety Question",
        placeholder="Example: What should I consider before inspecting a suspected steam leak?"
    )

    if st.button("🛡️ Search Safety Guidance", type="primary"):
        if not safety_query.strip():
            st.warning("Enter a safety question.")
        else:
            results = retrieve(safety_query, retriever, top_k=6)

            if results:
                for result in results:
                    with st.expander(
                        f"📄 Manual Page {result['page']} | Score {result['score']:.2f}"
                    ):
                        st.write(result["text"])
            else:
                st.info(
                    "No sufficiently relevant safety passage was retrieved. "
                    "Use the approved site safety procedure."
                )

    st.markdown("### General Safety Boundaries")

    safety_items = [
        "Never bypass or defeat safety/protection systems.",
        "Do not open or dismantle pressurized equipment.",
        "Use approved isolation and lockout/tagout procedures.",
        "Do not make unapproved control, calibration or protection-setting changes.",
        "Use qualified operations and maintenance personnel for intervention.",
        "Treat abnormal temperature, pressure, level, flow or leakage as potentially hazardous until verified."
    ]

    for item in safety_items:
        st.write("• " + item)

# ============================================================
# HISTORY
# ============================================================
elif module == "📊 Diagnostic History":

    st.subheader("📊 Diagnostic History")

    if not st.session_state.history:
        st.info("No diagnostic records in this session.")
    else:
        for i, item in enumerate(st.session_state.history, 1):
            with st.expander(
                f"{i}. {item['equipment'] or 'Equipment not specified'} — {item['severity']}"
            ):
                st.write("**Fault:**", item["fault"])
                st.write("**Topics:**", item["topics"])

    if st.session_state.history:
        if st.button("🗑️ Clear Session History"):
            st.session_state.history = []
            st.rerun()

# ============================================================
# ABOUT
# ============================================================
else:

    st.subheader("ℹ️ About This Application")

    st.markdown("""
    ### Purpose

    This application is an **API-free HRSG Fault Diagnosis, Troubleshooting &
    Maintenance Assistant**.

    It uses:

    - Local HRSG PDF manual
    - PDF text extraction
    - TF-IDF retrieval
    - Cosine similarity
    - Rule-based engineering diagnostic guidance
    - Manual page references

    ### No API Key Required

    This application does **not** require:

    - OpenAI API
    - Groq API
    - Gemini API
    - Hugging Face API

    ### Important Limitation

    Because this is an API-free version, it is not a full GPT-style autonomous
    reasoning system. Its diagnostic guidance is based on predefined engineering
    rules plus retrieval of relevant passages from the uploaded manual.

    For operational decisions, always use qualified personnel and approved
    plant/OEM procedures.
    """)

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.caption(
    "HRSG Fault Diagnosis Assistant | API-Free Local Retrieval Version | "
    "Engineering decision support only"
)
