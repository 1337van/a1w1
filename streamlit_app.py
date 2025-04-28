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

# — CONFIG & CLIENT SETUP —
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

# — GLOBAL CSS (single centered column) —
st.set_page_config(page_title="Video→WI Generator", layout="wide")
st.markdown(
    """
    <style>
      /* center content and constrain width */
      .main .block-container {
        max-width: 700px;
        margin: auto;
        padding: 2rem 1rem;
      }
      /* goodwill blue primary button */
      button[kind="primary"] {
        background-color: #0057a6 !important;
        border-color: #0057a6 !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# — HEADER —
with st.container():
    st.image("https://i.postimg.cc/L8JXmQ7t/gwlogo1.jpg", width=120)
    st.title("Video Summarizer → Work Instructions")
    st.caption("powered by Vertex AI Flash 2.0")
st.divider()

# — PROMPT EDITOR —
with st.container():
    default_prompt = """You are an operations specialist with a background in quality analysis and engineering technician practices, observing a manufacturing process within a controlled ISO 9001:2015 environment.

Visually and audibly analyze the video input to generate structured work instructions.

Follow this output template format:

1.0 Purpose  
Describe the purpose of this work instruction.

2.0 Scope  
State the scope (e.g., "This applies to Goodwill Commercial Services").

3.0 Responsibilities  
ROLE     | RESPONSIBILITY  
-------- | ----------------  
Line Lead | ● Ensure procedural adherence, documentation, and nonconformance decisions  
Operator  | ● Follow instructions and execute the defined procedure

4.0 Tools, Materials, Equipment, Supplies  
DESCRIPTION | VISUAL | HAZARD  
----------- | ------ | ------  
(e.g. Box Cutter | [insert image] | Sharp Blade Hazard)

5.0 Associated Safety and Ergonomic Concerns  
List relevant safety issues with legend.

6.0 Procedure  
STEP | ACTION | VISUAL | HAZARD  
-----|--------|--------|--------  
1 | [Describe action] | [Insert image/frame] | [Identify hazard]  

> If unclear, mark: [uncertain action]

7.0 Reference Documents  
List any applicable SOPs, work orders, or specs.
"""
    prompt = st.text_area("Edit your prompt:", value=default_prompt, height=200)
st.divider()

# — VIDEO UPLOAD & GENERATE —
with st.container():
    video = st.file_uploader("Upload a .mp4 manufacturing video", type="mp4")
    if video:
        st.video(video, use_container_width=True)
        if st.button("Generate Draft Instructions", type="primary"):
            # save & upload
            local = os.path.join(tmp_dir, video.name)
            with open(local, "wb") as f: f.write(video.read())
            gcs_path = f"input/{video.name}"
            storage_client.bucket(BUCKET).blob(gcs_path).upload_from_filename(local)
            st.success("Video uploaded; generating instructions…")
            # call Vertex AI
            resp = client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=[
                    Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs_path}", mime_type="video/mp4"),
                    prompt,
                ],
            )
            st.session_state.summary = resp.text
            st.markdown("#### Draft Instructions")
            st.code(st.session_state.summary, language="markdown")
            # extract frames
            times = re.findall(r"\[(\d{2}:\d{2}(?::\d{2})?)\]", resp.text)
            st.session_state.frames = []
            for t in sorted(set(times)):
                img = os.path.join(tmp_dir, f"frame_{t.replace(':','_')}.png")
                subprocess.run(
                    [FFMPEG_EXE, "-y", "-ss", t, "-i", local, "-vframes", "1", img],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if os.path.exists(img):
                    st.session_state.frames.append({"time": t, "path": img})
            st.session_state.current = 0
st.divider()

# — FRAME REVIEW & RE-EXTRACT —
if st.session_state.get("frames"):
    frame = st.session_state.frames[st.session_state.current]
    with st.container():
        st.image(frame["path"], use_container_width=True)
        st.markdown(f"**Timestamp:** {frame['time']}")
        snippet = next((l for l in st.session_state.summary.splitlines() if frame["time"] in l), "")
        st.markdown(f"**Description:** {snippet}")
        cols = st.columns(3)
        if cols[0].button("← Previous"):  
            st.session_state.current = max(0, st.session_state.current - 1)
        if cols[1].button("Delete"):  
            st.session_state.frames.pop(st.session_state.current)
            st.session_state.current = min(len(st.session_state.frames) - 1, st.session_state.current)
        if cols[2].button("Next →"):  
            st.session_state.current = min(len(st.session_state.frames) - 1, st.session_state.current + 1)
        if st.button("Re-Extract This Frame"):
            t = frame["time"]
            subprocess.run(
                [FFMPEG_EXE, "-y", "-ss", t, "-i", local, "-vframes", "1", frame["path"]],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            st.success(f"Re-extracted frame at {t}")
st.divider()

# — FINAL DOCX EXPORT —
if st.session_state.get("summary") and st.session_state.get("frames"):
    with st.container():
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
