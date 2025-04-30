import streamlit as st
import os
import tempfile
import base64
import re
import subprocess
import imageio_ffmpeg as iio_ffmpeg
from google import genai
from google.genai.types import HttpOptions, Part
from google.cloud import storage
from docx import Document
from docx.shared import Inches

# --- CONFIG & CLIENT SETUP ---
cfg = st.secrets["gcp"]
PROJECT_ID, LOCATION, BUCKET, SA_BASE64 = (
    cfg["project"], cfg["location"], cfg["bucket"], cfg["sa_key"]
)
tmp_dir = tempfile.mkdtemp()
sa_path = os.path.join(tmp_dir, "sa.json")
with open(sa_path, "wb") as f:
    f.write(base64.b64decode(SA_BASE64))
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
os.environ["GOOGLE_CLOUD_PROJECT"]       = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"]      = LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"]  = "True"
client = genai.Client(http_options=HttpOptions(api_version="v1"))
storage_client = storage.Client()
FFMPEG_EXE = iio_ffmpeg.get_ffmpeg_exe()

# --- PAGE LAYOUT & CSS ---
st.set_page_config(page_title="Video→WI Generator", layout="centered")
st.markdown("""
<style>
  .main .block-container {
    max-width: 700px; margin: auto; padding: 2rem 1rem; background: #f0f4f8;
  }
  button[kind="primary"] {
    background-color: #0057a6 !important; border-color: #0057a6 !important;
  }
</style>
""", unsafe_allow_html=True)

# --- HEADER ---
st.image("https://i.postimg.cc/L8JXmQ7t/gwlogo1.jpg", width=120)
st.title("Video Summarizer → Work Instructions")
st.caption("powered by Vertex AI Flash 2.0")
st.markdown("---")

# --- TIGHT PROMPT FOR JUST THE PROCEDURE TABLE ---
PROMPT = """You are an operations specialist with a background in quality analysis and engineering technician practices in an ISO 9001:2015 environment.

Extract **only** a markdown table for **Section 6.0 Procedure**. The table must have four columns:

| STEP | ACTION | VISUAL | HAZARD |

Each row **must** start with a timestamp in the form `[MM:SS]`.  
Do **not** include any introductory or concluding text—only the table.
"""
prompt = st.text_area("Edit your prompt:", value=PROMPT, height=180)
st.markdown("---")

# --- VIDEO UPLOAD & TABLE GENERATION ---
video = st.file_uploader("Upload a .mp4 video", type="mp4")
if video and st.button("Generate Draft Procedure Table", type="primary"):
    # Save locally
    local = os.path.join(tmp_dir, video.name)
    with open(local, "wb") as f:
        f.write(video.read())

    # Upload to GCS
    gcs = f"input/{video.name}"
    storage_client.bucket(BUCKET).blob(gcs).upload_from_filename(local)
    st.success("Video uploaded; generating procedure table…")

    # Call Vertex AI
    with st.spinner("Generating…"):
        resp = client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs}", mime_type="video/mp4"),
                prompt,
            ],
        )
    table_md = resp.text
    st.session_state.table_md = table_md

    # Display the raw table
    st.markdown("#### 6.0 Procedure Table")
    st.code(table_md, language="markdown")

    # Parse rows into steps
    steps = []
    for line in table_md.splitlines():
        if line.strip().startswith("|["):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            # cols = [STEP, ACTION, VISUAL, HAZARD]
            ts = re.match(r"\[(\d{2}:\d{2})\]", cols[0]).group(1)
            action = cols[1]
            hazard = cols[3]
            steps.append((ts, action, hazard))
    st.session_state.steps = steps

    # Extract one frame per step
    frames = []
    for ts, _, _ in steps:
        img = os.path.join(tmp_dir, f"frame_{ts.replace(':','_')}.png")
        subprocess.run(
            [FFMPEG_EXE, "-y", "-ss", ts, "-i", local, "-vframes", "1", img],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if os.path.exists(img):
            frames.append({"time": ts, "path": img})
    st.session_state.frames = frames
    st.session_state.idx = 0

st.markdown("---")

# --- IMAGE REVIEW (Prev/Next buttons) ---
if st.session_state.get("table_md"):
    if st.session_state.frames:
        st.markdown("### 🖼️ Review Step Images")
        i = st.session_state.idx
        ts, action, hazard = st.session_state.steps[i]
        img_path = st.session_state.frames[i]["path"]

        st.markdown(f"**Step {i+1} [{ts}]**  \n**Action:** {action}  \n**Hazard:** {hazard}")
        st.image(img_path, use_container_width=True)

        c1, c2, c3 = st.columns(3)
        if c1.button("← Previous"):
            st.session_state.idx = max(i-1, 0)
        if c2.button("Delete"):
            st.session_state.steps.pop(i)
            st.session_state.frames.pop(i)
            st.session_state.idx = min(i, len(st.session_state.frames)-1)
            st.experimental_rerun()
        if c3.button("Next →"):
            st.session_state.idx = min(i+1, len(st.session_state.frames)-1)
    else:
        st.info("No steps/image pairs found—check your prompt and video.")

    st.markdown("---")
    # --- DOCX EXPORT ---
    if st.button("Generate & Download WI .docx"):
        doc = Document()
        doc.add_heading("6.0 Procedure", level=1)

        for ts, action, hazard in st.session_state.steps:
            p = doc.add_paragraph(f"[{ts}] {action}", style="Heading 3")
            # insert image
            img = next((f["path"] for f in st.session_state.frames if f["time"]==ts), None)
            if img:
                doc.add_picture(img, width=Inches(3))
                # add hazard note
                doc.add_paragraph(f"Hazard: {hazard}", style="Intense Quote")
        out = os.path.join(tmp_dir, "Procedure_WI.docx")
        doc.save(out)
        with open(out, "rb") as f:
            st.download_button("Download WI .docx", f, file_name="Procedure_WI.docx")
