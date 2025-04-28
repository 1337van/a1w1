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

# --- GLOBAL STYLES ---
st.set_page_config(page_title="📦 Video-to-WI Generator", layout="wide")
st.markdown("""
<style>
  .block-container { background: #f0f4f8; padding: 2rem; }
  .card {
    background: #fff; padding: 1.5rem; margin-bottom: 1.5rem;
    border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.08);
  }
  button[kind="primary"] {
    background-color: #0057a6 !important; border-color: #0057a6 !important;
  }
  .logo { max-width: 120px; margin-bottom: 1rem; }
  @media (max-width: 640px) {
    .logo { max-width: 80px; }
  }
</style>
""", unsafe_allow_html=True)

# --- HEADER CARD ---
st.markdown('<div class="card">', unsafe_allow_html=True)
col1, col2 = st.columns([1,3])
with col1:
    logo_url = "https://i.postimg.cc/L8JXmQ7t/gwlogo1.jpg"
    # use HTML so our .logo CSS applies
    st.markdown(f'<img src="{logo_url}" class="logo">', unsafe_allow_html=True)
with col2:
    st.markdown("## Video Summarizer → Work Instructions")
    st.markdown("##### powered by Vertex AI Flash 2.0")
st.markdown('</div>', unsafe_allow_html=True)

# --- PROMPT CARD ---
st.markdown('<div class="card">', unsafe_allow_html=True)
default_prompt = """You are an operations specialist ..."""
prompt = st.text_area("Edit your prompt:", default_prompt, height=200)
st.markdown('</div>', unsafe_allow_html=True)

# --- UPLOAD & GENERATE CARD ---
st.markdown('<div class="card">', unsafe_allow_html=True)
video_file = st.file_uploader("Upload a .mp4 manufacturing video", type="mp4")
if video_file:
    c1, c2 = st.columns([2,1])
    with c1:
        st.video(video_file, format="video/mp4", use_container_width=True)
    with c2:
        st.write("### Ready to generate draft instructions?")
        if st.button("Generate Draft Instructions", type="primary"):
            local_path = os.path.join(tmp_dir, video_file.name)
            with open(local_path, "wb") as f:
                f.write(video_file.read())
            gcs_path = f"input/{video_file.name}"
            storage_client.bucket(BUCKET).blob(gcs_path).upload_from_filename(local_path)
            st.success(f"Uploaded to gs://{BUCKET}/{gcs_path}")
            with st.spinner("Calling Vertex AI…"):
                resp = client.models.generate_content(
                    model="gemini-2.0-flash-001",
                    contents=[
                        Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs_path}", mime_type="video/mp4"),
                        prompt
                    ],
                )
            st.session_state.summary = resp.text
            st.markdown("#### Draft Instructions")
            st.code(st.session_state.summary, language="markdown")

            # extract frames
            times = re.findall(r"\[(\d{2}:\d{2}(?::\d{2})?)\]", st.session_state.summary)
            st.session_state.frames = []
            for t in sorted(set(times)):
                img_path = os.path.join(tmp_dir, f"frame_{t.replace(':','_')}.png")
                subprocess.run(
                    [FFMPEG_EXE, "-y", "-ss", t, "-i", local_path, "-vframes", "1", img_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                if os.path.exists(img_path):
                    st.session_state.frames.append({"time": t, "path": img_path})
            st.session_state.current = 0
st.markdown('</div>', unsafe_allow_html=True)

# --- REVIEW CAROUSEL CARD ---
if st.session_state.get("frames"):
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### Review Key Frames")
    frame = st.session_state.frames[st.session_state.current]
    rc1, rc2 = st.columns([2,1])
    with rc1:
        st.image(frame["path"], use_container_width=True)
    with rc2:
        st.markdown(f"**Timestamp:** {frame['time']}")
        snippet = next((l for l in st.session_state.summary.splitlines() if frame["time"] in l), "")
        st.markdown(f"**Description:** {snippet}")
        b1, b2, b3 = st.columns(3)
        if b1.button("← Previous"):
            st.session_state.current = max(0, st.session_state.current-1)
        if b2.button("Delete"):
            st.session_state.frames.pop(st.session_state.current)
            st.session_state.current = min(st.session_state.current, len(st.session_state.frames)-1)
        if b3.button("Next →"):
            st.session_state.current = min(len(st.session_state.frames)-1, st.session_state.current+1)
        if st.button("Re-Extract This Frame"):
            t = frame["time"]
            subprocess.run(
                [FFMPEG_EXE, "-y", "-ss", t, "-i", local_path, "-vframes", "1", frame["path"]],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            st.success(f"Re-extracted frame at {t}")
    st.markdown('</div>', unsafe_allow_html=True)

# --- EXPORT CARD ---
if st.session_state.get("summary") and st.session_state.get("frames"):
    st.markdown('<div class="card">', unsafe_allow_html=True)
    if st.button("Generate & Download WI .docx", type="primary"):
        doc = Document()
        doc.add_heading("Work Instructions", 0)
        for block in st.session_state.summary.strip().split("\n\n"):
            lines = block.split("\n")
            doc.add_paragraph(lines[0], style="Heading 2")
            for ln in lines[1:]:
                doc.add_paragraph(ln)
        out = os.path.join(tmp_dir, "work_instruction.docx")
        doc.save(out)
        with open(out, "rb") as f:
            st.download_button("Download WI .docx", f, file_name="work_instruction.docx")
    st.markdown('</div>', unsafe_allow_html=True)
