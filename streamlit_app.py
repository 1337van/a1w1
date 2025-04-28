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

# --- PROMPT ---
FULL_PROMPT = """\
You are an operations specialist with a background in quality analysis and engineering technician practices, observing a manufacturing process within a controlled ISO 9001:2015 environment.

Visually and audibly analyze the video input to generate structured work instructions.  
**For each step, prepend the time marker in the video where it occurs, formatted exactly like `[MM:SS]` at the start of the line.**

6.0 Procedure  
Use a table:  
STEP | ACTION | VISUAL | HAZARD  
-----|--------|--------|--------  
`[MM:SS]` | Describe action | [Insert image/frame] | [Identify hazard]  
"""
prompt = st.text_area("Edit your prompt:", value=FULL_PROMPT, height=180)
st.markdown("---")

# --- VIDEO UPLOAD & DRAFT GENERATION ---
video_file = st.file_uploader("Upload a .mp4 video", type="mp4")
if video_file:
    st.video(video_file)

    if st.button("Generate Draft Instructions", type="primary"):
        # save video
        local_path = os.path.join(tmp_dir, video_file.name)
        with open(local_path, "wb") as f:
            f.write(video_file.read())

        # upload to GCS
        gcs_path = f"input/{video_file.name}"
        storage_client.bucket(BUCKET).blob(gcs_path).upload_from_filename(local_path)
        st.success("Video uploaded; generating instructions…")

        # call Vertex AI
        with st.spinner("Generating…"):
            resp = client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=[
                    Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs_path}", mime_type="video/mp4"),
                    prompt,
                ],
            )
        summary = resp.text
        st.session_state.summary = summary

        # display draft
        st.markdown("#### Draft Work Instructions")
        st.code(summary, language="markdown")

        # parse timestamped steps
        steps = []
        for line in summary.splitlines():
            m = re.match(r"\[(\d{2}:\d{2})\]\s*(.+)", line)
            if m:
                steps.append((m.group(1), m.group(2)))
        st.session_state.steps = steps

        # extract frames for each step
        frames = []
        for ts, _ in steps:
            img = os.path.join(tmp_dir, f"frame_{ts.replace(':','_')}.png")
            subprocess.run(
                [FFMPEG_EXE, "-y", "-ss", ts, "-i", local_path, "-vframes", "1", img],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if os.path.exists(img):
                frames.append({"time": ts, "path": img})
        st.session_state.frames = frames
        st.session_state.index = 0

st.markdown("---")

# --- IMAGE REVIEW WITH BUTTONS ---
if st.session_state.get("frames"):
    st.markdown("### 🖼️ Review Extracted Step Images")
    idx = st.session_state.index
    ts, desc = st.session_state.steps[idx]
    path = st.session_state.frames[idx]["path"]

    st.markdown(f"**Step {idx+1} [{ts}]:** {desc}")
    st.image(path, use_container_width=True)

    col_prev, col_del, col_next = st.columns(3)
    if col_prev.button("← Previous"):
        st.session_state.index = max(idx - 1, 0)
    if col_del.button("Delete this image"):
        # remove both step and frame
        del st.session_state.steps[idx]
        del st.session_state.frames[idx]
        st.session_state.index = min(idx, len(st.session_state.frames) - 1)
        st.experimental_rerun()
    if col_next.button("Next →"):
        st.session_state.index = min(idx + 1, len(st.session_state.frames) - 1)

else:
    st.info("No timestamped steps (and thus no images) found. Check your prompt includes `[MM:SS]` markers.")

st.markdown("---")

# --- ALWAYS-ON DOCX EXPORT ---
if st.session_state.get("steps"):
    if st.button("Generate & Download WI .docx"):
        doc = Document()
        doc.add_heading("Work Instructions", 0)

        for ts, text in st.session_state.steps:
            # write step text
            p = doc.add_paragraph(f"[{ts}] {text}", style="Heading 3")
            # attach image if exists
            img = next((f["path"] for f in st.session_state.frames if f["time"] == ts), None)
            if img:
                doc.add_picture(img, width=Inches(3))

        out = os.path.join(tmp_dir, "work_instructions.docx")
        doc.save(out)
        with open(out, "rb") as f:
            st.download_button("Download WI .docx", f, file_name="work_instructions.docx")
