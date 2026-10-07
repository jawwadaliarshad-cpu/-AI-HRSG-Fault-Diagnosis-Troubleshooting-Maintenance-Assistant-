"""
HRSG Fault & Maintenance Assistant
BQPS-III 900MW CCPP (K-Electric) - MHPS Dongfang HRSG

100% offline / rule-based: NO API key, NO LLM, NO internet calls.
- Searches the O&M manual PDF (BM25 keyword search) and shows exact page references.
- Built-in fault library + maintenance checklists prepared from the manual's design data.

Folder layout for GitHub / Streamlit Cloud:
    app.py
    requirements.txt
    data/HRSG_Design_Instruction_Manual.pdf   <- put the manual here (keep repo PRIVATE)
"""

import glob
import html
import io
import math
import os
import re
import datetime as dt
from collections import Counter

import pandas as pd
import streamlit as st
from pypdf import PdfReader

# ----------------------------------------------------------------------------
# PAGE CONFIG + STYLE
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="HRSG Fault & Maintenance Assistant",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
.block-container {padding-top: 1.2rem; max-width: 1250px;}
.hero {
    background: linear-gradient(120deg, #0b2545 0%, #13315c 55%, #e8710a 140%);
    border-radius: 16px; padding: 22px 28px; color: #fff; margin-bottom: 18px;
    box-shadow: 0 6px 22px rgba(11,37,69,.25);
}
.hero h1 {margin: 0; font-size: 1.9rem; color: #fff; letter-spacing: .3px;}
.hero p {margin: 6px 0 0 0; color: #dbe7f5; font-size: .95rem;}
.badge {
    display: inline-block; padding: 3px 11px; border-radius: 20px; font-size: .75rem;
    font-weight: 600; margin-right: 6px; margin-top: 10px; background: rgba(255,255,255,.16);
    color: #fff; border: 1px solid rgba(255,255,255,.28);
}
.card {
    border: 1px solid rgba(128,128,128,.28); border-left: 5px solid #e8710a;
    border-radius: 10px; padding: 12px 16px; margin: 8px 0; background: rgba(128,128,128,.06);
}
.card .meta {font-size: .78rem; color: #e8710a; font-weight: 700; margin-bottom: 4px;}
.warn {
    border: 1px solid #d9534f; background: rgba(217,83,79,.10); border-radius: 10px;
    padding: 10px 14px; margin: 8px 0; font-size: .92rem;
}
.info {
    border: 1px solid #2f80ed; background: rgba(47,128,237,.09); border-radius: 10px;
    padding: 10px 14px; margin: 8px 0; font-size: .92rem;
}
.tag-manual {color:#1b8a3a; font-weight:700;}
.tag-general {color:#c77700; font-weight:700;}
mark {background: #ffd54a; color: #000; padding: 0 2px; border-radius: 3px;}
.stTabs [data-baseweb="tab"] {font-weight: 600;}
footer {visibility: hidden;}
</style>
""",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# OPTIONAL PASSWORD GATE (set APP_PASSWORD in Streamlit secrets to enable)
# ----------------------------------------------------------------------------
def password_gate():
    try:
        pw = st.secrets.get("APP_PASSWORD", None)
    except Exception:
        pw = None
    if not pw:
        return
    if st.session_state.get("auth_ok"):
        return
    st.markdown(
        '<div class="hero"><h1>🔒 HRSG Assistant</h1><p>Internal plant document - login required</p></div>',
        unsafe_allow_html=True,
    )
    entered = st.text_input("Password", type="password")
    if st.button("Login"):
        if entered == pw:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    st.stop()


password_gate()

# ----------------------------------------------------------------------------
# PDF LOADING + SEARCH (BM25, pure python)
# ----------------------------------------------------------------------------
STOP = set(
    "a an the of to in on at for and or is are was were be by with from as it this that these those "
    "do does did how what why when which who whom can could should would will shall may might "
    "i me my we our you your if then than so not no yes into out up down over under please tell "
    "kya hai hain ka ki ke ko se me mein par aur ya ho hoo hota hoti hote tha thi the kar karo karna "
    "kia kaise kyun kyon jab to tu bhi bh".split()
)

# Roman Urdu -> English helper words (so Roman Urdu queries still match the English manual)
ROMAN_MAP = {
    "kam": "low", "kum": "low", "zyada": "high", "ziada": "high", "zyadah": "high", "ziyada": "high",
    "ooncha": "high", "uncha": "high", "neecha": "low", "garam": "hot temperature high",
    "garmi": "temperature high", "thanda": "cold low temperature", "thandi": "cold low temperature",
    "leak": "leak", "leakage": "leak", "rissa": "leak", "risna": "leak", "risao": "leak",
    "paani": "water", "pani": "water", "bhaap": "steam", "bhap": "steam", "bhapp": "steam",
    "awaz": "noise", "awaaz": "noise", "shor": "noise", "hilna": "vibration", "kanpna": "vibration",
    "larzish": "vibration", "band": "trip stop", "bund": "trip stop", "trip": "trip",
    "dabao": "pressure", "dabaao": "pressure", "pressure": "pressure", "tapmaan": "temperature",
    "darja": "temperature", "zang": "corrosion", "jang": "corrosion", "phat": "rupture leak",
    "phatna": "rupture leak", "safai": "clean", "gandagi": "fouling", "dhuan": "smoke stack",
    "chimney": "stack", "pump": "pump", "motor": "motor", "valve": "valve", "wall": "valve",
    "level": "level", "drum": "drum", "alarm": "alarm", "start": "start-up", "shutdown": "shutdown",
    "kharabi": "fault", "masla": "fault problem", "fault": "fault", "kharab": "fault",
    "nahi": "not", "chalta": "running", "chal": "running", "fail": "fault", "tez": "high fast",
    "dheema": "slow", "dhima": "slow",
}


def tokenize(text: str):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1]


def expand_query(q: str) -> str:
    extra = []
    for w in re.findall(r"[a-zA-Z]+", q.lower()):
        if w in ROMAN_MAP:
            extra.append(ROMAN_MAP[w])
    return q + " " + " ".join(extra)


@st.cache_data(show_spinner=False)
def read_pdf(name: str, data: bytes):
    pages = []
    reader = PdfReader(io.BytesIO(data))
    for i, p in enumerate(reader.pages, start=1):
        try:
            txt = p.extract_text() or ""
        except Exception:
            txt = ""
        m = re.search(r"Instruction Manual\s*\n\s*(\d{1,3})\s*\n", txt)
        label = m.group(1) if m else str(i)
        pages.append({"src": name, "pdf_page": i, "label": label, "text": txt})
    return pages


def make_chunks(pages, size=750):
    chunks = []
    for pg in pages:
        lines = [l.strip() for l in pg["text"].splitlines() if l.strip()]
        buf, n = [], 0
        for ln in lines:
            buf.append(ln)
            n += len(ln) + 1
            if n >= size:
                chunks.append({**pg, "chunk": " ".join(buf)})
                buf = buf[-2:]
                n = sum(len(x) + 1 for x in buf)
        if buf:
            chunks.append({**pg, "chunk": " ".join(buf)})
    return chunks


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tok = [tokenize(d) for d in docs]
        self.N = len(self.tok)
        self.avg = (sum(len(t) for t in self.tok) / self.N) if self.N else 0
        df = Counter()
        for t in self.tok:
            for w in set(t):
                df[w] += 1
        self.idf = {w: math.log(1 + (self.N - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = [Counter(t) for t in self.tok]

    def search(self, query, k=6):
        q = tokenize(query)
        scores = []
        for i, tf in enumerate(self.tf):
            dl = len(self.tok[i]) or 1
            s = 0.0
            for w in q:
                if w in tf:
                    f = tf[w]
                    s += self.idf.get(w, 0) * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1))
                    )
            scores.append(s)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [(i, scores[i]) for i in order[:k] if scores[i] > 0]


@st.cache_resource(show_spinner="Building manual index...")
def build_index(key: str, _chunks):
    return BM25([c["chunk"] for c in _chunks])


def load_sources(uploaded):
    pages = []
    files = sorted(set(glob.glob("data/*.pdf") + glob.glob("*.pdf")))
    for f in files:
        try:
            with open(f, "rb") as fh:
                pages += read_pdf(os.path.basename(f), fh.read())
        except Exception as e:
            st.sidebar.warning(f"Could not read {f}: {e}")
    for up in uploaded or []:
        try:
            pages += read_pdf(up.name, up.getvalue())
        except Exception as e:
            st.sidebar.warning(f"Could not read {up.name}: {e}")
    return pages


def highlight(text: str, terms):
    safe = html.escape(text)
    for t in sorted(set(terms), key=len, reverse=True):
        if len(t) < 3:
            continue
        safe = re.sub(f"(?i)({re.escape(html.escape(t))})", r"<mark>\1</mark>", safe)
    return safe


def manual_search(query, k=5):
    if not CHUNKS:
        return []
    q = expand_query(query)
    hits = INDEX.search(q, k=k)
    return [(CHUNKS[i], s) for i, s in hits]


def render_hits(hits, query):
    terms = tokenize(expand_query(query))
    for c, s in hits:
        st.markdown(
            f'<div class="card"><div class="meta">📄 {html.escape(c["src"])} &nbsp;|&nbsp; '
            f'Manual p.{c["label"]} (PDF p.{c["pdf_page"]}) &nbsp;|&nbsp; relevance {s:.1f}</div>'
            f'{highlight(c["chunk"], terms)}</div>',
            unsafe_allow_html=True,
        )


# ----------------------------------------------------------------------------
# KNOWLEDGE BASE (prepared from the manual; page numbers = printed manual pages)
# ----------------------------------------------------------------------------
EQUIPMENT = [
    "Not sure / Any",
    "HP / IP / LP Drum",
    "Superheater & Reheater",
    "Attemperators (Spray)",
    "Economizers & Evaporators",
    "Feedwater & Recirculation Pumps",
    "Blowdown / Drain / Vent",
    "Safety Valves & Instruments",
    "Casing / Insulation / Expansion Joints",
    "Stack & Silencers",
    "Water / Steam Chemistry",
    "Start-up / Shutdown / Preservation",
]

FAULTS = [
    {
        "title": "Drum water level LOW (HP / IP / LP)",
        "equip": "HP / IP / LP Drum",
        "kw": ["level low", "low level", "drum level", "water level low", "level kam", "level drop", "level falling",
               "level lost", "no water", "dry drum", "level alarm", "lp drum level", "hp drum level", "ip drum level"],
        "manual": [
            "Normal operating level (from drum centre line): HP -50 mm, IP -50 mm, LP +400 mm (p21).",
            "Each drum has a local level gauge and level transmitter to keep natural circulation safe (p21).",
            "HP feedwater control valve is at the HP ECON inlet; IP ECON outlet has a control valve to the IP drum (p22-23).",
            "HP & IP feed from 2x100% shared BFPs (1 running, 1 standby) with min-flow valve; minimum flow 130 t/h (p22-23).",
            "Emergency (start-up) blowdown and continuous blowdown lines exist on every drum (p24).",
        ],
        "causes": [
            "Feedwater control valve stuck / failed closed or in wrong mode",
            "BFP tripped or low discharge pressure; standby pump did not start",
            "Level transmitter / impulse line error (compare with local gauge)",
            "Blowdown valve (continuous / emergency / intermittent) open or passing",
            "Tube / header / economizer leak",
            "Shrink effect during load change or start-up",
        ],
        "actions": [
            "Compare DCS transmitters with the local gauge glass to confirm the real level.",
            "Check feedwater control valve demand vs. position; take manual control if needed.",
            "Confirm running BFP / start the standby BFP; check min-flow valve is not stuck open.",
            "Verify all blowdown and drain valves are fully closed.",
            "Hold or reduce GT load per plant procedure if level keeps falling.",
            "If level is lost from the gauge, trip per plant emergency procedure - do NOT feed a hot empty drum blindly.",
        ],
        "safety": "Hot drum, high pressure. Do not open drains or vents while pressurised.",
        "escalate": True,
    },
    {
        "title": "Drum water level HIGH / steam carry-over",
        "equip": "HP / IP / LP Drum",
        "kw": ["level high", "high level", "carry over", "carryover", "water carry", "level zyada", "drum high",
               "swell", "moisture in steam", "wet steam"],
        "manual": [
            "Normal levels HP -50 / IP -50 / LP +400 mm; gauges and transmitters on each drum (p21).",
            "Steam-water separation: cyclone separators (1st stage), shutter + uniform distribution plate (2nd stage, HP/IP), underwater orifice plate (LP) (p19).",
            "Emergency (start-up) blowdown line with motor + manual stop valves on every drum (p24).",
            "Steam quality must meet GB/T 12145-2008 (p12).",
        ],
        "causes": [
            "Feedwater control valve passing / wide open",
            "Level transmitter error or reference leg problem",
            "Swell during start-up, pressure drop or fast load increase",
            "Drum internals damaged or fouled (carry-over)",
            "High drum-water solids / foaming",
        ],
        "actions": [
            "Verify with local gauge; reduce feedwater demand / go manual.",
            "Open emergency blowdown per procedure if level continues to rise.",
            "Watch SH outlet temperature (sudden drop) and steam conductivity.",
            "Check continuous blowdown is in service; sample drum water.",
        ],
        "safety": "Water carry-over into SH/turbine can cause severe damage - act immediately.",
        "escalate": True,
    },
    {
        "title": "Drum wall temperature difference high (thermal stress)",
        "equip": "HP / IP / LP Drum",
        "kw": ["wall temperature", "temperature difference", "thermal stress", "drum temperature", "top bottom",
               "metal temperature", "ramp rate", "start up fast", "thermocouple drum"],
        "manual": [
            "Thermocouples on drum top & bottom and steam-side inside metal; upper/lower and outside/inside differences must stay within allowable scope for drum life (p21).",
            "Starts: cold (>72h) 10/yr, warm (10-72h) 260/yr, hot (<=10h) 40/yr; lifetime 9300 starts (p12).",
        ],
        "causes": [
            "GT loading / pressure ramp too fast",
            "Low or uneven drum water level / poor circulation",
            "Faulty thermocouple",
            "Cold feedwater injected into hot drum",
        ],
        "actions": [
            "Hold GT load and pressure rise until difference reduces.",
            "Cross-check thermocouples and trend the top/bottom values.",
            "Confirm drum level is normal; review start-up curve and OEM allowable limits.",
        ],
        "safety": "",
        "escalate": True,
    },
    {
        "title": "HP superheated steam temperature abnormal (high / low)",
        "equip": "Attemperators (Spray)",
        "kw": ["sh temperature", "superheat", "main steam temperature", "hp steam temperature", "steam temp high",
               "steam temp low", "steam temperature high", "steam temperature low", "567", "spray valve", "attemperator",
               "desuperheater", "steam garam"],
        "manual": [
            "Rated HP steam outlet: 313.08 t/h, 15.72 MPa.a, 567 C (p8).",
            "Two-stage spray attemperators: between HP SEC and TER SH, and on HP SH main steam pipe (p20).",
            "Spray water is taken from HP feedwater with flow element, electric control valve, bypass, check valve and main electric globe valve (p23).",
            "Thermocouples at attemperator inlet/outlet and SH outlet (p21).",
        ],
        "causes": [
            "Spray control valve stuck, leaking or oscillating",
            "Spray nozzle (multi-hole) blocked or eroded",
            "Low HP feedwater pressure / flow",
            "High GT exhaust temperature or gas flow change",
            "Thermocouple error; heating surface fouling",
        ],
        "actions": [
            "Check spray valve position vs demand; try bypass/manual as per procedure.",
            "Confirm main spray isolation globe valve is open and feedwater pressure is adequate.",
            "Check attemperator inlet/outlet temperatures to see if spray is effective.",
            "Reduce GT load if temperature cannot be controlled (avoid overheating P91 piping).",
        ],
        "safety": "Do not exceed design temperature of the SH tubes and P91 main steam pipe.",
        "escalate": True,
    },
    {
        "title": "Reheat (RH) steam temperature abnormal / RH protection",
        "equip": "Attemperators (Spray)",
        "kw": ["reheat", "rh temperature", "rh steam", "hot reheat", "cold reheat", "rh spray", "rh attemperator",
               "rh safety valve", "rh high temperature"],
        "manual": [
            "Rated RH steam: 350.83 t/h, 3.52 MPa.a, 567 C (p8).",
            "Two spray attemperators on RH (between SEC and TER RH, and on RH main steam pipe); spray water from IP feedwater (p15, p20, p23).",
            "RH protection: 2 safety valves on RH inlet piping, 1 on RH outlet; thermocouples at attemperator and RH outlet (p21).",
            "Hot RH pipe SA-335 P91, cold RH pipe SA-335 P12 (p22).",
        ],
        "causes": [
            "RH spray valve stuck / passing, or IP feedwater supply low",
            "Turbine bypass or HP exhaust condition changed",
            "High GT exhaust temperature",
            "Insufficient steam flow through RH during start-up (overheating)",
        ],
        "actions": [
            "Check RH spray valve, flow element and IP feedwater pressure.",
            "Verify steam is flowing through RH during start-up / low load per procedure.",
            "Reduce GT load if temperature is uncontrollable; check RH safety valve status.",
        ],
        "safety": "RH tubes can overheat quickly without steam flow.",
        "escalate": True,
    },
    {
        "title": "Safety valve lifting / leaking / seat passing",
        "equip": "Safety Valves & Instruments",
        "kw": ["safety valve", "relief valve", "psv", "pop up", "lifting", "valve passing", "valve leak", "safety valve lift",
               "safety valve leaking", "vent silencer", "overpressure", "over pressure"],
        "manual": [
            "Safety valves on HP, IP and LP SH outlets (set below drum set pressure); 2 valves on each drum; 2 on RH inlet and 1 on RH outlet (p21).",
            "Safety valve settings must NOT be changed without OEM recommendation; valves and transmitters must be calibrated per regulations (p3).",
            "Safety valve vent lines are fitted with silencers (p25).",
        ],
        "causes": [
            "Genuine over-pressure (turbine trip, bypass not opening, control fault)",
            "Operating pressure too close to set point",
            "Seat damage / dirt after previous lift; simmering",
            "Pressure transmitter drift",
        ],
        "actions": [
            "Confirm real pressure on more than one transmitter.",
            "Check turbine bypass / pressure control response.",
            "After a lift, inspect for seat leakage; schedule overhaul/re-calibration.",
            "Never adjust or gag a safety valve - contact OEM / certified valve shop.",
        ],
        "safety": "Stay clear of vent outlets. Never gag or change set points without OEM approval (p3).",
        "escalate": True,
    },
    {
        "title": "LP economizer low-temperature corrosion / low inlet water temp",
        "equip": "Economizers & Evaporators",
        "kw": ["lp econ", "lp economizer", "low temperature corrosion", "acid dew", "condensate temperature",
               "recirculation line", "dew point", "econ corrosion", "econ inlet temp", "recirc pump", "outlet gas temp low"],
        "manual": [
            "LP ECON has a recirculation line and bypass to raise inlet water temperature and prevent low-temp gas corrosion (p14, p22).",
            "Design: feedwater 55.8 C, exhaust gas 85.6 C (p8-9).",
            "Recirculation pump: 2x100%, constant speed, 250 t/h, outlet 2.3 MPa, min flow 70 t/h (p23).",
            "Light diesel fuel contains 0.59% S and oil flue gas contains SO2 (p10-11) - raises acid dew point.",
        ],
        "causes": [
            "Recirculation pump not running or recirc valve stuck closed",
            "Condensate temperature too low (condenser / ejector issues)",
            "LP ECON bypass not in correct position",
            "Prolonged oil firing with cold water inlet",
        ],
        "actions": [
            "Check recirculation pump status / start standby; verify recirc valve opening.",
            "Verify LP ECON inlet temperature trend and bypass position.",
            "Avoid long periods on oil with low inlet temperature.",
            "Inspect LP ECON tubes at outage for corrosion / leaks.",
        ],
        "safety": "",
        "escalate": False,
    },
    {
        "title": "Boiler feed pump (BFP) trip / low flow / low pressure",
        "equip": "Feedwater & Recirculation Pumps",
        "kw": ["bfp", "boiler feed pump", "feed pump", "feedwater pump", "pump trip", "pump tripped", "low discharge pressure",
               "min flow", "minimum flow", "npsh", "cavitation", "pump vibration", "feed water pressure low", "pump band"],
        "manual": [
            "HP & IP share 2x100% motor-driven constant-speed BFPs (1 service, 1 spare) with min-flow valve at each outlet (p22).",
            "Data: flow 347 t/h, inlet 0.63 MPa, outlet 17.55 MPa, feedwater 155 C, min flow 130 t/h, NPSH 21.9 m (p23).",
            "HP feedwater control valve at HP ECON inlet (p23); ammonia & hydrazine dosing at BFP inlet (p24).",
        ],
        "causes": [
            "Low suction pressure / low LP drum (deaerator) level -> NPSH loss",
            "Min-flow valve stuck, motor protection, bearing temperature / vibration trip",
            "Standby pump auto-start not available",
            "Discharge check valve or strainer problem",
        ],
        "actions": [
            "Make sure the standby BFP starts; start manually if required.",
            "Watch HP/IP drum levels and reduce load if feedwater cannot be maintained.",
            "Check LP drum level/temperature and suction pressure; check min-flow valve.",
            "Inspect trip cause (motor, bearings, lube oil, electrical) before restart.",
        ],
        "safety": "Electrical work only by qualified personnel; lock out and tag before maintenance (p3).",
        "escalate": False,
    },
    {
        "title": "Suspected tube / header leak in heating surface",
        "equip": "Economizers & Evaporators",
        "kw": ["tube leak", "tube leakage", "header leak", "steam leak", "water leak", "makeup increase", "make up water",
               "wet insulation", "hissing", "white plume", "tube rupture", "leak inside", "finned tube", "tube phat"],
        "manual": [
            "Finned tubes are vertical with upper/bottom headers; 6 blocks along gas flow x 3 across width (p1, p17).",
            "Drain lines at lowest points of every heating surface (p24); sampling points on condensate, drum water, saturated and SH steam (p24).",
            "Manholes on side casing, inlet/outlet duct, stack and between blocks for repair (p1, p26).",
            "Hydrostatic test water capacity ~532 m3 total (p16).",
            "Do not clean or repair when the system is not depressurised (p3).",
        ],
        "causes": [
            "Erosion / corrosion / fatigue of tube or header weld",
            "Thermal stress from rapid start-ups or quenching",
            "Water chemistry or oxygen corrosion; low-temperature corrosion (LP ECON)",
            "Blocked / displaced supports causing tube vibration",
        ],
        "actions": [
            "Confirm: rising make-up water, falling drum level, steam plume at stack, noise at casing.",
            "Plan controlled shutdown; cool down, depressurise and drain per procedure.",
            "Enter through manholes only with confined-space permit; locate leak visually / hydro-test.",
            "Repair or plug only per OEM-approved procedure and weld qualification.",
        ],
        "safety": "Depressurise, isolate, blind and tag before entry. Welding needs a permit (p3).",
        "escalate": True,
    },
    {
        "title": "High gas-side differential pressure / GT back-pressure",
        "equip": "Economizers & Evaporators",
        "kw": ["gas resistance", "differential pressure", "dp high", "back pressure", "backpressure", "fouling", "gas side",
               "pressure drop", "inlet duct pressure", "tube fouling", "choked", "gas flow"],
        "manual": [
            "Design gas resistance <= 3.3 kPa static pressure (p9); per-section resistance table is in p9.",
            "Gas path: inlet duct -> HP TER SH -> ... -> LP ECON -> outlet duct -> stack (p17).",
            "Stack silencers are fitted inside the stack (p25).",
        ],
        "causes": [
            "Fin fouling (soot / deposits, especially after oil firing)",
            "Displaced anti-short-gas plates or collapsed internals",
            "Damaged stack / duct silencer",
            "Higher than design GT exhaust flow",
        ],
        "actions": [
            "Trend dp across HRSG and compare with the p9 design table.",
            "Inspect through manholes at next outage; clean fouled surfaces.",
            "Inspect stack silencer and duct internals.",
        ],
        "safety": "",
        "escalate": False,
    },
    {
        "title": "Expansion joint leakage / hot gas leaking at inlet or outlet duct",
        "equip": "Casing / Insulation / Expansion Joints",
        "kw": ["expansion joint", "fabric joint", "inlet duct leak", "gas leak", "hot gas leak", "outlet duct", "duct leak",
               "duct leakage", "joint leak", "non metal", "non-metal"],
        "manual": [
            "Non-metal expansion joints at HRSG inlet and outlet duct absorb thermal expansion; expansion centre is at the 5th column (p19-20, p25).",
            "HRSG operates in positive pressure - leaks push hot gas OUT (p2).",
            "Inlet duct has insulation (280 mm) and inner casing; outlet duct and stack have external insulation (p20).",
        ],
        "causes": [
            "Ageing / thermal damage of the fabric joint, loose bolts",
            "Excess displacement or restricted expansion",
            "Inner liner / insulation failure exposing the joint",
        ],
        "actions": [
            "Keep personnel away; barricade the area.",
            "Use thermal imaging to find the extent; reduce load if severe.",
            "Plan joint repair/replacement at shutdown; check for anything restricting expansion.",
        ],
        "safety": "Hot flue-gas leak - burn and exhaust-gas hazard. Do not approach while running.",
        "escalate": True,
    },
    {
        "title": "Casing hot spot / insulation or inner casing damage",
        "equip": "Casing / Insulation / Expansion Joints",
        "kw": ["hot spot", "casing hot", "casing temperature", "insulation", "inner casing", "casing paint", "red casing",
               "casing glowing", "casing garam", "seal", "penetration"],
        "manual": [
            "Inside insulation (aluminium silicate fibre blanket) + inner casing in inlet duct, HRSG body and outlet duct (p19-20).",
            "Thickness: inlet duct 280 mm, HT area 280 mm, LT area 120 mm; inner casing SUH409L scale-type (p20).",
            "Insulation fixed with spring clamps and stainless / galvanised wire (p19-20).",
            "Better seal and expansion structure for pipes penetrating the duct (p2).",
        ],
        "causes": [
            "Insulation displaced or damaged; hot gas bypassing behind inner casing",
            "Damaged inner casing / sealing at pipe penetrations",
            "Rain ingress or mechanical damage",
        ],
        "actions": [
            "Mark hot spots with an IR camera and trend them.",
            "Reduce load if casing temperature rises rapidly.",
            "Repair insulation / casing / seals at the next outage.",
        ],
        "safety": "Hot surfaces - use PPE; no insulation work while hot.",
        "escalate": False,
    },
    {
        "title": "Tube panel vibration or abnormal noise inside HRSG",
        "equip": "Economizers & Evaporators",
        "kw": ["vibration", "noise", "rumble", "banging", "knocking", "panel vibration", "tube vibration", "shaking",
               "awaz", "shor", "pulsation", "resonance", "humming"],
        "manual": [
            "Anti-vibration baffle plates (one set per panel) prevent air-cell (acoustic) vibration (p18).",
            "Honeycomb supports: 14 sets in Blocks I & II, 8 sets in Blocks III-VI (p17-18).",
            "Header anti-seismic lugs and tie bars; top hangers with 2 hoist points per upper header (p18).",
        ],
        "causes": [
            "GT combustion dynamics / exhaust pulsation",
            "Loose, missing or damaged baffle plates or honeycomb supports",
            "Flow-induced vibration at a particular load",
            "Water hammer in drains / steam lines",
        ],
        "actions": [
            "Record load/frequency when it occurs; compare with GT conditions.",
            "Check for water hammer in piping first (drains, startup).",
            "Inspect baffles, honeycomb supports, hangers and tie bars at outage.",
        ],
        "safety": "",
        "escalate": True,
    },
    {
        "title": "Blowdown / drain / vent valve problem",
        "equip": "Blowdown / Drain / Vent",
        "kw": ["blowdown", "blow down", "drain valve", "vent valve", "continuous blowdown", "intermittent blowdown",
               "emergency blowdown", "valve stuck", "valve not closing", "motor valve", "drain line", "vent line"],
        "manual": [
            "Continuous blowdown: electric control + stop valve on HP & IP; motor + manual stop valve on LP (p24).",
            "Emergency (start-up) blowdown on each drum: motor + manual stop valve; intermittent blowdown at downcomer bottom of HP/IP/LP evaporators (p24).",
            "Drain lines at every heating surface low point; secondary drain valve is motorised for DCS operation (p24).",
            "Vent lines on saturated steam line, SH/RH headers, economizers and high points; start-up vents at HP & LP SH outlets with silencers (p24-25).",
        ],
        "causes": [
            "Valve passing (loss of level and heat)",
            "Actuator / limit switch fault, no air/power",
            "Debris on seat; manual valve left open",
        ],
        "actions": [
            "Close the downstream manual valve to isolate if the motor valve passes.",
            "Check actuator power, limit switches and feedback signals.",
            "Plan valve overhaul; confirm drum level is not affected.",
        ],
        "safety": "Hot water/steam discharge - stand clear; follow isolation and tagging procedure.",
        "escalate": False,
    },
    {
        "title": "Steam / water chemistry out of specification",
        "equip": "Water / Steam Chemistry",
        "kw": ["conductivity", "silica", "sio2", "ph", "chemistry", "hydrazine", "ammonia", "dosing", "dissolved oxygen",
               "water quality", "steam quality", "sampling", "demin", "condenser leak", "hardness"],
        "manual": [
            "Steam quality must meet GB/T 12145-2008 (p12).",
            "Demineralised water: conductivity <= 0.2 uS/cm (25 C), SiO2 <= 10 ug/L, hardness ~0 (p11).",
            "Sampling points: condensate, drum water, saturated steam, SH steam; ammonia + hydrazine dosing at BFP inlet; hydrazine on HP/IP drums, ammonia on LP drum (p24-25).",
            "Continuous blowdown available on each drum (p24).",
        ],
        "causes": [
            "Condenser tube leak / poor demin water quality",
            "Dosing pump or tank problem",
            "Drum carry-over or high drum level",
            "Air in-leakage (oxygen)",
        ],
        "actions": [
            "Confirm with a re-sample; identify which sample point is out of range.",
            "Increase continuous blowdown within limits; check dosing pumps and tanks.",
            "Check condenser leakage and demin plant; correct before continuing at load.",
        ],
        "safety": "Hydrazine/ammonia are hazardous chemicals - use proper PPE.",
        "escalate": False,
    },
    {
        "title": "LP drum / deaerator problem (high oxygen, level, pressure)",
        "equip": "HP / IP / LP Drum",
        "kw": ["deaerator", "deaerating", "dearator", "oxygen", "lp drum", "lp steam", "deaerating head", "lp pressure"],
        "manual": [
            "LP drum acts as the deaerator water tank; condensate passes LP ECON then the deaerating head through a control valve (p13-14).",
            "Part of LP drum saturated steam supplies the deaerator; the rest goes to LP SH and the steam turbine (p14).",
            "LP rated steam 39.18 t/h at 0.45 MPa.a, 245.6 C (p8-9). LP drum normal level +400 mm (p21).",
        ],
        "causes": [
            "Insufficient deaerating steam / low LP pressure",
            "Deaerating head control valve issue",
            "High condensate flow or low LP drum level",
            "Air in-leakage on condensate system",
        ],
        "actions": [
            "Check deaerating head control valve and LP drum pressure.",
            "Sample dissolved oxygen; adjust chemical dosing as needed.",
            "Check condensate flow and temperature.",
        ],
        "safety": "",
        "escalate": False,
    },
    {
        "title": "Start-up / shutdown issues & long-term preservation",
        "equip": "Start-up / Shutdown / Preservation",
        "kw": ["start up", "startup", "start-up", "shutdown", "shut down", "cold start", "warm start", "hot start",
               "nitrogen", "n2", "preservation", "storage", "long shutdown", "wet layup", "dry layup", "purge", "ramp"],
        "manual": [
            "Start types: cold (>72h), warm (10-72h), hot (<=10h); stable operating range 30-100% load (p11-12).",
            "Operates in constant or sliding pressure (p11).",
            "N2 filling connections on economizer, saturated steam connecting pipe and RH inlet pipe for long-term storage protection (p24).",
            "Start-up vents at HP and LP SH outlets (p25); start-up blowdown on each drum (p24).",
        ],
        "causes": [
            "Too fast ramp -> thermal stress",
            "Drain/vent sequence not followed",
            "Poor preservation -> internal corrosion",
        ],
        "actions": [
            "Follow the start-up curve; open drains/vents per sequence.",
            "Keep metal temperature differences within limits (see drum wall item).",
            "For long shutdown use N2 blanketing via the N2 connections and keep tubes dry.",
        ],
        "safety": "Nitrogen is an asphyxiant - never enter vessels or low spots without gas testing and permit.",
        "escalate": False,
    },
    {
        "title": "Stack / silencer / platform & structure condition",
        "equip": "Stack & Silencers",
        "kw": ["stack", "silencer", "chimney", "aviation light", "platform", "manhole", "structure", "steel structure",
               "ladder", "stairway", "dhuan", "stack noise"],
        "manual": [
            "Stack: steel, 7 m diameter, outlet elevation 60 m, platform and aviation warning light at the top (p26).",
            "Stack silencers inside the stack; vent silencers at every vent pipe outlet (p25).",
            "Manholes on inlet duct, outlet duct, stack and between blocks (p26).",
            "Platforms mostly on the left side; typical width 1 m, riffled plate (p20).",
        ],
        "causes": [
            "Silencer packing / baffle damage -> noise & dp increase",
            "Corrosion or loose steelwork",
            "Aviation light failure",
        ],
        "actions": [
            "Inspect stack silencers and structure at outage using fall protection.",
            "Repair aviation light promptly (regulatory).",
            "Check platform grating, handrails and ladders.",
        ],
        "safety": "Work at height: use full-body harness and fall-arrest system (p4-5).",
        "escalate": False,
    },
]

# Maintenance checklist: (System, Frequency, Task, Manual ref)
MAINT = [
    # Drums
    ("Drums (HP/IP/LP)", "Daily", "Compare local level gauge with DCS transmitters; check normal level (HP/IP -50 mm, LP +400 mm)", "p21"),
    ("Drums (HP/IP/LP)", "Daily", "Check drum top/bottom and inside/outside metal temperature differences are within limits", "p21"),
    ("Drums (HP/IP/LP)", "Weekly", "Walk-down: sliding supports free, no abnormal noise, no leakage at nozzles / gauge connections", "p19"),
    ("Drums (HP/IP/LP)", "Monthly", "Test level alarms/trips and balance tank / electrical contact level gauge", "p19, p21"),
    ("Drums (HP/IP/LP)", "Annual / Outage", "Internal inspection through manway: cyclone separators, shutters, distribution plates, underwater plate, vortex eliminators", "p19"),
    # Safety valves
    ("Safety valves & instruments", "Monthly", "Visual check of all safety valves (2 per drum, SH outlets, RH 2 inlet + 1 outlet) and vent silencers", "p21, p25"),
    ("Safety valves & instruments", "Annual / Outage", "Calibrate/test safety valves, transmitters and instruments per regulations; do NOT alter set points without OEM", "p3"),
    ("Safety valves & instruments", "Monthly", "Verify drum, attemperator and SH/RH outlet thermocouple readings against each other", "p21"),
    # SH / RH / attemperators
    ("SH / RH & attemperators", "Daily", "Trend SH/RH outlet temperatures vs 567 C design; check spray valve positions", "p8, p20"),
    ("SH / RH & attemperators", "Monthly", "Stroke-test spray control valves, bypass, check valve and main electric globe valve (SH from HP FW, RH from IP FW)", "p23"),
    ("SH / RH & attemperators", "Annual / Outage", "Inspect attemperator spray nozzles (multi-hole) and liners; inspect SH/RH piping supports and P91/P12 pipes", "p20, p22"),
    # Economizers, evaporators
    ("Heating surfaces (ECON / EVAP / SH / RH)", "Daily", "Trend gas-side dp and exhaust gas temperature vs design (<= 3.3 kPa; 85.6 C)", "p8-9"),
    ("Heating surfaces (ECON / EVAP / SH / RH)", "Monthly", "Check for steam/water leaks, wet insulation, abnormal noise near casing", "p26"),
    ("Heating surfaces (ECON / EVAP / SH / RH)", "Annual / Outage", "Internal inspection via manholes: fin fouling, tube erosion/corrosion, honeycomb supports, anti-vibration baffles, anti-short-gas plates", "p17-18, p26"),
    ("Heating surfaces (ECON / EVAP / SH / RH)", "Annual / Outage", "Inspect top hangers (rods, rockers, frames), header lugs and tie bars; hydrostatic test if required", "p18, p16"),
    ("LP economizer", "Weekly", "Verify recirculation pump / line and bypass operate; check LP ECON inlet water temperature", "p14, p22"),
    ("LP economizer", "Annual / Outage", "Inspect for low-temperature corrosion, especially after oil firing", "p10-11, p22"),
    # Pumps
    ("Feedwater & recirculation pumps", "Daily", "Check running/standby status, discharge pressure, bearing temperature, vibration, leakage", "p22-23"),
    ("Feedwater & recirculation pumps", "Weekly", "Change over running/standby pump; confirm standby auto-start readiness", "p22"),
    ("Feedwater & recirculation pumps", "Monthly", "Test min-flow valves (BFP min 130 t/h; recirc pump min 70 t/h)", "p23"),
    # Blowdown / drains / vents
    ("Blowdown, drains & vents", "Daily", "Check continuous blowdown valves (HP/IP electric, LP motor + manual) and sampling flow", "p24"),
    ("Blowdown, drains & vents", "Monthly", "Operate motorised secondary drain valves and emergency blowdown valves from DCS; check for passing", "p24"),
    ("Blowdown, drains & vents", "Annual / Outage", "Inspect intermittent blowdown lines, blowdown tanks and vent silencers", "p24-25"),
    # Chemistry
    ("Water / steam chemistry", "Daily", "Sample condensate, drum water, saturated and SH steam; confirm limits per GB/T 12145-2008", "p12, p24"),
    ("Water / steam chemistry", "Weekly", "Check ammonia / hydrazine dosing equipment and chemical tank levels", "p24-25"),
    # Casing / duct
    ("Casing, insulation & ducts", "Weekly", "Visual / IR scan of casing for hot spots; check pipe-penetration seals", "p2, p19-20"),
    ("Casing, insulation & ducts", "Annual / Outage", "Inspect non-metal expansion joints (inlet & outlet), inner casing (SUH409L), insulation fixings", "p19-20"),
    # Stack & structure
    ("Stack, platforms & structure", "Monthly", "Check aviation warning light, platforms, ladders, handrails, manholes", "p20, p26"),
    ("Stack, platforms & structure", "Annual / Outage", "Inspect stack silencers and steel structure; use fall protection", "p4-5, p25"),
    # Preservation
    ("Shutdown preservation", "As required", "For long storage: N2 fill via economizer, saturated steam connecting pipe and RH inlet pipe connections; keep positive N2 pressure", "p24"),
]

MAINT_DF = pd.DataFrame(MAINT, columns=["System", "Frequency", "Task", "Manual ref"])

SAFETY_RULES = [
    ("Training", "Do not operate the equipment unless you are familiar with and trained on this manual (p2)."),
    ("Depressurise first", "Do not clean or repair the equipment when the system is not depressurised (p3)."),
    ("Isolation", "Cut off power, gas and water during maintenance; install isolation blind flanges and hang warning signs (p3)."),
    ("Hot work", "Permission must be obtained before welding near the equipment (p3)."),
    ("Electrical", "Only qualified personnel maintain electrical equipment. Never open an energised electrical panel door (p3)."),
    ("Safety valves", "Safety valve and protection set points must not be changed without OEM recommendation; calibrate per regulations (p3)."),
    ("Fire", "Keep a fire extinguisher nearby with an unobstructed path; keep roads/site channels clear (p2-3)."),
    ("Visibility", "Do not operate the equipment in poor visibility (p2)."),
    ("Environment", "Handle drained coolant/lubricant properly to prevent pollution (p3)."),
    ("PPE", "Gloves, safety glasses/shoes, ear protection, hard hat, respirator, coveralls as required (p4)."),
    ("Fall protection", "Use full-body harness with fall-arrest/lanyards, rails or nets as appropriate for work at height (p4-5)."),
    ("Pressure hazards", "Cracks in pressure parts can lead to leaks or rupture: blast, fragments, poisoning, suffocation, fire (p7)."),
    ("Electrical enclosures", "Keep control boxes protected from rain/snow, clean, and free from conductive objects; grounding resistance within design (p3, p6)."),
]

REF_TABLES = {
    "Main steam & performance parameters (p8-9)": pd.DataFrame(
        [
            ["HP main steam", "313.08 t/h", "15.72 MPa.a", "567 C"],
            ["IP main steam", "50.56 t/h", "3.78 MPa.a", "278.3 C"],
            ["RH steam", "350.83 t/h", "3.52 MPa.a", "567 C"],
            ["LP main steam", "39.18 t/h", "0.45 MPa.a", "245.6 C"],
            ["Feedwater temperature", "-", "-", "55.8 C"],
            ["Exhaust gas temperature", "-", "-", "85.6 C"],
            ["Gas resistance", "-", "<= 3.3 kPa (static)", "-"],
            ["Auxiliary power", "~3217 kW", "-", "-"],
            ["Heating surface area (as printed)", "399353 m2", "-", "-"],
        ],
        columns=["Item", "Flow", "Pressure", "Temperature"],
    ),
    "Drums (p18-19, p21)": pd.DataFrame(
        [
            ["HP", "SA-302MB", "2070 x 135", "13 m", "Spherical", "-50", "1150 (+525/-625)"],
            ["IP", "SA-516GR70", "1670 x 35", "10 m", "Ellipsoidal", "-50", "800 (+375/-425)"],
            ["LP", "SA-516GR70", "3050 x 25", "13 m", "Ellipsoidal", "+400", "2050 (+1025/-1025)"],
        ],
        columns=["Drum", "Material", "Dia x thk (mm)", "Shell length", "Heads", "Normal level (mm)", "Gauge visual length (mm)"],
    ),
    "Pumps (p23)": pd.DataFrame(
        [
            ["Type", "Horizontal, constant speed", "Constant speed, centrifugal"],
            ["Quantity", "2 (1 service, 1 spare)", "2 (1 service, 1 spare)"],
            ["Inlet flow (t/h)", "347", "250"],
            ["Inlet pressure (MPa)", "0.63", "1.26"],
            ["Outlet pressure (MPa)", "17.55", "2.3"],
            ["Water temperature (C)", "155", "148"],
            ["Pumping head (MPa)", "16.92", "1.04"],
            ["Minimum flow (t/h)", "130", "70"],
            ["NPSH available (m H2O)", "21.9", "107"],
        ],
        columns=["Item", "HP/IP shared feed pump", "Recirculation pump"],
    ),
    "Steam piping (p22)": pd.DataFrame(
        [
            ["HP main steam", "273 x 28.58", "SA-335 P91"],
            ["Hot RH steam", "559 x 22.23", "SA-335 P91"],
            ["Cold RH steam", "559 x 22.23", "SA-335 P12"],
            ["LP main steam", "406.4 x 9.53", "SA-106 B"],
        ],
        columns=["Line", "Size (mm)", "Material"],
    ),
    "Heating surface blocks (p17)": pd.DataFrame(
        [
            ["I", "HP TER SH, TER RH, HP SEC SH", "23250", "3612.5"],
            ["II", "SEC RH, PRI RH, HP PRI SH", "23250", "3612.5"],
            ["III", "HP EVAP", "23550", "3612.5"],
            ["IV", "HP FIN ECON, IP SH, IP EVAP, HP SEC ECON", "23550", "3612.5"],
            ["V", "LP SH, IP ECON (HP SEC ECON), HP PRI ECON, LP EVAP", "23550", "3612.5"],
            ["VI", "LP ECON", "23550", "3612.5"],
        ],
        columns=["Block", "Heating surface", "Length (mm)", "Width (mm)"],
    ),
    "Circulation ratio & hydrostatic capacity (p16)": pd.DataFrame(
        [
            ["HP drum / IP drum / LP drum (m3)", "36.1 / 22.2 / 101.3"],
            ["RH (m3)", "33.6"],
            ["HP SH / IP SH / LP SH (m3)", "15.7 / 2.7 / 3.9"],
            ["HP EVAP / IP EVAP / LP EVAP (m3)", "36.5 / 21.3 / 28.4"],
            ["HP ECON / IP ECON / LP ECON (m3)", "68.2 / 6.8 / 55.3"],
            ["Connecting piping (m3)", "100"],
            ["Total (m3)", "532"],
            ["Circulation ratio HP / IP / LP", "5.9 / 38 / 51.8"],
        ],
        columns=["Item", "Value"],
    ),
    "Starts & operation (p11-12)": pd.DataFrame(
        [
            ["Cold (>72 h)", "10", "300"],
            ["Warm (10-72 h)", "260", "7800"],
            ["Hot (<=10 h)", "40", "1200"],
            ["Total", "310", "9300"],
        ],
        columns=["Start type", "Per year", "Lifetime"],
    ),
    "Dimensions (p12-13)": pd.DataFrame(
        [
            ["Inlet duct centre line elevation", "6500"],
            ["Outlet duct centre line elevation", "15075"],
            ["HRSG width (outside side casing)", "11660"],
            ["HRSG length (inlet joint to stack CL)", "34620"],
            ["Top casing height", "28350"],
            ["HP / IP / LP drum CL elevation", "32350 / 32750 / 32500"],
            ["Stack diameter / top elevation", "7000 / 60000"],
        ],
        columns=["Item", "mm"],
    ),
    "Site & fuel design conditions (p9-11)": pd.DataFrame(
        [
            ["Location", "Port Qasim, Malir, Karachi"],
            ["Ambient temp", "0 to +50 C"],
            ["Max rainfall / day", "142.6 mm"],
            ["Max humidity", "90 %"],
            ["Design wind", "113 kph (0.62 kN/m2)"],
            ["Seismic", "Class II, 0.24 g horizontal"],
            ["Fuel", "Natural gas + light diesel (S 0.59 %)"],
            ["Demin water", "<= 0.2 uS/cm, SiO2 <= 10 ug/L, hardness ~0"],
            ["Exhaust gas (NG / Oil)", "694.361 / 687.933 kg/s; 616.8 / 558.6 C"],
        ],
        columns=["Item", "Value"],
    ),
}

# ----------------------------------------------------------------------------
# SIDEBAR + DATA LOAD
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    unit = st.selectbox("Plant unit", ["Unit 1 (HRSG-1)", "Unit 2 (HRSG-2)"])
    uploaded = st.file_uploader(
        "➕ Add more manuals (PDF)", type=["pdf"], accept_multiple_files=True,
        help="Extra O&M / troubleshooting manuals become searchable in 'Manual Search' and in Fault Diagnosis.",
    )

PAGES = load_sources(uploaded)
CHUNKS = make_chunks(PAGES)
INDEX_KEY = "|".join(f"{p['src']}:{p['pdf_page']}:{len(p['text'])}" for p in PAGES)
INDEX = build_index(INDEX_KEY, CHUNKS) if CHUNKS else None

with st.sidebar:
    st.markdown("---")
    if PAGES:
        srcs = sorted({p["src"] for p in PAGES})
        st.success(f"📚 {len(srcs)} manual(s) loaded, {len(PAGES)} pages")
        for s in srcs:
            st.caption(f"• {s}")
    else:
        st.warning("No PDF found. Put the manual in the `data/` folder or upload it above. Built-in knowledge base still works.")
    st.markdown("---")
    st.caption("🔌 Offline mode: no API key, no internet AI. Answers come from the manual + built-in knowledge base.")
    st.caption("Always verify with plant procedures and authorised personnel before acting.")

# ----------------------------------------------------------------------------
# HEADER
# ----------------------------------------------------------------------------
st.markdown(
    f"""
<div class="hero">
  <h1>🔥 HRSG Fault & Maintenance Assistant</h1>
  <p>BQPS-III 900MW CCPP &nbsp;|&nbsp; K-Electric &nbsp;|&nbsp; MHDB-SGT5-4000F-Q1 (MHPS Dongfang)</p>
  <span class="badge">{html.escape(unit)}</span>
  <span class="badge">Triple pressure + Reheat</span>
  <span class="badge">Natural circulation</span>
  <span class="badge">Offline • No API key</span>
</div>
""",
    unsafe_allow_html=True,
)

tabs = st.tabs(
    ["🩺 Fault Diagnosis", "🛠️ Maintenance", "🔎 Manual Search", "📊 Quick Reference", "🦺 Safety", "📝 Fault Log"]
)

# ----------------------------------------------------------------------------
# TAB 1: FAULT DIAGNOSIS
# ----------------------------------------------------------------------------
def score_fault(f, query, equip):
    q = " " + re.sub(r"[^a-z0-9\s\-]", " ", expand_query(query).lower()) + " "
    s = 0.0
    for k in f["kw"]:
        if k in q:
            s += 4 if " " in k else 3
    qt = set(tokenize(q))
    s += 0.7 * len(qt & set(tokenize(f["title"])))
    if equip != "Not sure / Any" and f["equip"] == equip:
        s += 2.5
    return s


def render_fault(f):
    st.markdown(f"**Equipment area:** {f['equip']}")
    st.markdown('<span class="tag-manual">📘 What the manual says</span>', unsafe_allow_html=True)
    for m in f["manual"]:
        st.markdown(f"- {m}")
    st.markdown(
        '<span class="tag-general">🛠️ Possible causes & suggested actions</span> '
        "<small>(general engineering guidance - the manual has no troubleshooting table)</small>",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Possible causes**")
        for x in f["causes"]:
            st.markdown(f"- {x}")
    with c2:
        st.markdown("**Immediate actions**")
        for i, x in enumerate(f["actions"], 1):
            st.markdown(f"{i}. {x}")
    if f["safety"]:
        st.markdown(f'<div class="warn">⚠️ <b>Safety:</b> {html.escape(f["safety"])}</div>', unsafe_allow_html=True)
    if f["escalate"]:
        st.markdown(
            '<div class="info">📞 <b>Escalate to OEM if unresolved:</b> MHPS Dongfang Boiler Co., Ltd, Chenggong Road, Jiaxing, '
            "Zhejiang 314000 | Tel 0086-573-82625131 | Fax 0086-573-82625130 | yingyebu@mhdb.com.cn (p27)</div>",
            unsafe_allow_html=True,
        )


with tabs[0]:
    st.markdown(
        '<div class="info">Fault ya alarm likhein (English ya Roman Urdu). App built-in fault library aur manual PDF dono mein se '
        "matching information aur page references dikhayegi.</div>",
        unsafe_allow_html=True,
    )
    left, right = st.columns([1, 2])
    with left:
        equip = st.selectbox("Equipment / area", EQUIPMENT)
        state = st.selectbox("Operating state", ["Normal load", "Start-up", "Shutdown", "Standby / Layup", "Load change"])
    with right:
        query = st.text_area(
            "Describe the fault / alarm",
            height=130,
            placeholder="e.g. HP drum level low alarm during start-up\nLP ECON outlet temperature low\nRH spray valve not responding\nexpansion joint se hot gas leak ho rahi hai",
        )
    go = st.button("🔍 Diagnose", type="primary")

    if go:
        if not query.strip():
            st.warning("Please describe the fault first.")
        else:
            scored = sorted(((score_fault(f, query, equip), f) for f in FAULTS), key=lambda x: x[0], reverse=True)
            matches = [(s, f) for s, f in scored if s >= 3][:3]
            hits = manual_search(f"{query} {equip if equip != 'Not sure / Any' else ''}", k=4)
            st.session_state["last"] = {
                "query": query, "equip": equip, "state": state, "unit": unit,
                "titles": [f["title"] for _, f in matches],
                "hits": hits,
            }

    last = st.session_state.get("last")
    if last:
        st.markdown(f"### Results for: *{last['query'][:120]}*")
        if last["state"] in ("Start-up", "Load change"):
            st.markdown(
                '<div class="info">ℹ️ Operating state is start-up/load change: also consider shrink/swell, thermal-stress limits and drain/vent sequence (p21, p24-25).</div>',
                unsafe_allow_html=True,
            )
        if last["titles"]:
            for idx, t in enumerate(last["titles"]):
                f = next(x for x in FAULTS if x["title"] == t)
                with st.expander(f"{'🔴' if idx == 0 else '🟠'} {f['title']}", expanded=(idx == 0)):
                    render_fault(f)
        else:
            st.warning(
                "No matching entry in the built-in fault library. See the manual passages below; "
                "this item is not specifically covered - contact the OEM or follow plant procedures."
            )
        if last["hits"]:
            st.markdown("#### 📄 Related passages in the manual PDF")
            render_hits(last["hits"], last["query"])
        st.markdown('<div class="warn">⚠️ Verify with plant procedures and authorised personnel before acting.</div>', unsafe_allow_html=True)

        if st.button("💾 Save this diagnosis to Fault Log"):
            st.session_state.setdefault("log", []).append(
                {
                    "Date/Time": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Unit": last["unit"],
                    "Equipment": last["equip"],
                    "State": last["state"],
                    "Fault": last["query"],
                    "Matched topics": "; ".join(last["titles"]) or "none",
                    "Action taken / notes": "",
                }
            )
            st.success("Saved to Fault Log tab (this browser session).")

# ----------------------------------------------------------------------------
# TAB 2: MAINTENANCE
# ----------------------------------------------------------------------------
with tabs[1]:
    st.markdown(
        '<div class="info">Checklists are prepared from the equipment description in the manual. '
        "Inspection intervals are typical practice - follow your plant PM schedule and OEM recommendations.</div>",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    with c1:
        systems = ["All systems"] + sorted(MAINT_DF["System"].unique())
        sys_sel = st.selectbox("System", systems)
    with c2:
        freq_sel = st.multiselect(
            "Frequency", ["Daily", "Weekly", "Monthly", "Annual / Outage", "As required"],
            default=["Daily", "Weekly", "Monthly", "Annual / Outage", "As required"],
        )
    df = MAINT_DF.copy()
    if sys_sel != "All systems":
        df = df[df["System"] == sys_sel]
    df = df[df["Frequency"].isin(freq_sel)]
    st.caption(f"{len(df)} tasks")
    st.dataframe(df, hide_index=True)
    st.download_button(
        "⬇️ Download checklist (CSV)", df.to_csv(index=False).encode("utf-8"),
        file_name="hrsg_maintenance_checklist.csv", mime="text/csv",
    )

# ----------------------------------------------------------------------------
# TAB 3: MANUAL SEARCH
# ----------------------------------------------------------------------------
with tabs[2]:
    q = st.text_input("Search the manual", placeholder="e.g. attemperator, safety valve, honeycomb support, N2 line, drum level")
    k = st.slider("Number of results", 3, 15, 6)
    if q:
        if not CHUNKS:
            st.warning("No PDF loaded. Place the manual in the data/ folder or upload it in the sidebar.")
        else:
            hits = manual_search(q, k=k)
            if hits:
                render_hits(hits, q)
            else:
                st.info("No matches. Try different keywords.")

# ----------------------------------------------------------------------------
# TAB 4: QUICK REFERENCE
# ----------------------------------------------------------------------------
with tabs[3]:
    st.caption("Values taken from the manual (printed page numbers shown). Manual typos are kept as printed.")
    for title, tdf in REF_TABLES.items():
        with st.expander(title, expanded=False):
            st.dataframe(tdf, hide_index=True)
    st.markdown("**Gas path:** GT exhaust → inlet duct → HP TER SH → TER RH → HP SEC SH → SEC RH → PRI RH → HP PRI SH → HP EVAP → HP FIN ECON → IP SH → IP EVAP → HP TER ECON → LP SH → IP ECON (HP SEC ECON) → HP PRI ECON → LP EVAP → LP ECON → outlet duct → stack (p17)")

# ----------------------------------------------------------------------------
# TAB 5: SAFETY
# ----------------------------------------------------------------------------
with tabs[4]:
    st.markdown("#### Safety rules from the manual (Section 1.2 and 2)")
    for head, txt in SAFETY_RULES:
        st.markdown(f'<div class="card"><div class="meta">{html.escape(head)}</div>{html.escape(txt)}</div>', unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# TAB 6: FAULT LOG
# ----------------------------------------------------------------------------
with tabs[5]:
    st.caption("Log is kept only for this browser session. Download the CSV to keep records.")
    with st.expander("➕ Add manual entry"):
        e1, e2 = st.columns(2)
        with e1:
            m_equip = st.selectbox("Equipment", EQUIPMENT, key="m_equip")
        with e2:
            m_state = st.selectbox("State", ["Normal load", "Start-up", "Shutdown", "Standby / Layup", "Load change"], key="m_state")
        m_fault = st.text_input("Fault", key="m_fault")
        m_notes = st.text_area("Action taken / notes", key="m_notes")
        if st.button("Add entry"):
            if m_fault.strip():
                st.session_state.setdefault("log", []).append(
                    {
                        "Date/Time": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "Unit": unit, "Equipment": m_equip, "State": m_state, "Fault": m_fault,
                        "Matched topics": "-", "Action taken / notes": m_notes,
                    }
                )
                st.success("Entry added.")
            else:
                st.warning("Enter a fault description.")
    log = st.session_state.get("log", [])
    if log:
        ldf = pd.DataFrame(log)
        st.dataframe(ldf, hide_index=True)
        st.download_button("⬇️ Download log (CSV)", ldf.to_csv(index=False).encode("utf-8"),
                           file_name="hrsg_fault_log.csv", mime="text/csv")
        if st.button("🗑️ Clear log"):
            st.session_state["log"] = []
            st.rerun()
    else:
        st.info("No entries yet.")
