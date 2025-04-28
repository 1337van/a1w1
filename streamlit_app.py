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
st.set_page_config(page_title="📦 Video-to-WI Generator", layout="centered")
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

# --- PROMPT ---
PROMPT = """\
You are an operations specialist with a background in quality analysis and engineering technician practices, observing a manufacturing process within a controlled ISO 9001:2015 environment.

Visually and audibly analyze the video input to generate structured work instructions.  
**For each step, prepend the time marker in the video where it occurs, formatted exactly like `[MM:SS]` at the start of the line.**

6.0 Procedure  
Use a table:  
STEP | ACTION | VISUAL | HAZARD  
-----|--------|--------|--------  
`[MM:SS]` | Describe action | [Insert image/frame] | [Identify hazard]  
"""
prompt = st.text_area("Edit your prompt:", value=PROMPT, height=180)
st.markdown("---")

# --- VIDEO UPLOAD & GENERATION ---
video = st.file_uploader("Upload a .mp4 video", type="mp4")
if video and st.button("Generate Draft Instructions", type="primary"):
    # Save
    local_path = os.path.join(tmp_dir, video.name)
    with open(local_path, "wb") as f:
        f.write(video.read())

    # Upload
    gcs = f"input/{video.name}"
    storage_client.bucket(BUCKET).blob(gcs).upload_from_filename(local_path)
    st.success("Video uploaded; generating instructions…")

    # Call AI
    with st.spinner("Calling Vertex AI…"):
        resp = client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs}", mime_type="video/mp4"),
                prompt,
            ],
        )
    summary = resp.text
    st.session_state.summary = summary

    # Show draft
    st.markdown("#### Draft Work Instructions")
    st.code(summary, language="markdown")

    # Parse the Markdown table rows
    steps = []
    for line in summary.splitlines():
        if line.startswith("|") and "]" in line:
            cols = [c.strip() for c in line.split("|")[1:-1]]
            # cols = [STEP, ACTION, VISUAL, HAZARD]
            ts_cell = cols[0]
            match = re.search(r"`?\[(\d{2}:\d{2})\]`?", ts_cell)
            if match:
                ts = match.group(1)
                action = cols[1]
                steps.append((ts, action))

    st.session_state.steps = steps

    # Extract frames
    frames = []
    for ts, _ in steps:
        img = os.path.join(tmp_dir, f"frame_{ts.replace(':','_')}.png")
        subprocess.run(
            [FFMPEG_EXE, "-y", "-ss", ts, "-i", local_path, "-vframes", "1", img],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if os.path.exists(img):
            frames.append({"time": ts, "path": img})
    st.session_state.frames = frames
    st.session_state.index = 0

st.markdown("---")

# --- IMAGE REVIEW ---
if "summary" in st.session_state:
    if st.session_state.frames:
        st.markdown("### 🖼️ Review Step Images")
        idx = st.session_state.index
        ts, action = st.session_state.steps[idx]
        path = st.session_state.frames[idx]["path"]

        st.markdown(f"**Step {idx+1} [{ts}]**  \n{action}")
        st.image(path, use_container_width=True)

        prev_col, del_col, next_col = st.columns([1,1,1])
        if prev_col.button("← Previous"):
            st.session_state.index = max(idx - 1, 0)
        if del_col.button("Delete this image"):
            st.session_state.steps.pop(idx)
            st.session_state.frames.pop(idx)
            st.session_state.index = min(idx, len(st.session_state.frames)-1)
            st.experimental_rerun()
        if next_col.button("Next →"):
            st.session_state.index = min(idx + 1, len(st.session_state.frames)-1)
    else:
        st.info("No timestamped steps found. Ensure your prompt requests `[MM:SS]` markers.")

    st.markdown("---")

    # --- DOCX EXPORT ---
    if st.button("Generate & Download WI .docx"):
        doc = Document()
        doc.add_heading("Work Instructions", 0)
        for ts, action in st.session_state.steps:
            p = doc.add_paragraph(f"[{ts}] {action}", style="Heading 3")
            img = next((f["path"] for f in st.session_state.frames if f["time"] == ts), None)
            if img:
                doc.add_picture(img, width=Inches(3))
        out = os.path.join(tmp_dir, "work_instructions.docx")
        doc.save(out)
        with open(out, "rb") as f:
            st.download_button("Download WI .docx", f, file_name="work_instructions.docx")
