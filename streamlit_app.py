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
from PIL import Image

# --- CONFIGURATION via .streamlit/secrets.toml ---
cfg = st.secrets["gcp"]
PROJECT_ID, LOCATION, BUCKET, SA_BASE64 = cfg["project"], cfg["location"], cfg["bucket"], cfg["sa_key"]

# Write service account JSON
tmp_dir = tempfile.mkdtemp()
sa_path = os.path.join(tmp_dir, "sa.json")
with open(sa_path, "wb") as f:
    f.write(base64.b64decode(SA_BASE64))
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
os.environ["GOOGLE_CLOUD_PROJECT"]       = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"]      = LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"]  = "True"

# Clients
client = genai.Client(http_options=HttpOptions(api_version="v1"))
storage_client = storage.Client()
FFMPEG_EXE = iio_ffmpeg.get_ffmpeg_exe()

# --- PAGE STYLING & BRANDING ---
st.set_page_config(page_title="📦 Video-to-WI Generator", layout="wide")
st.markdown("""
<style>
/* background + container padding */
.main .block-container { padding: 2rem; background: #f0f4f8; }
/* card style */
.card { box-shadow: 0 4px 12px rgba(0,0,0,0.08); border-radius: 8px; background: #fff; padding: 1.5rem; margin-bottom: 1.5rem; }
/* Goodwill blue buttons */
button[kind="primary"] { background-color: #0057a6 !important; border-color: #0057a6 !important; }
/* branding image */
.branding { text-align: center; margin-bottom: 1rem; }
</style>
""", unsafe_allow_html=True)

# Branding: allow upload logo or show placeholder
with st.container():
    col1, col2 = st.columns([1,3])
    with col1:
        logo = st.file_uploader("Upload Logo", type=["png","jpg","jpeg"])
        if logo:
            st.image(logo, use_column_width=True, caption="Branding")
    with col2:
        st.markdown("# Video Summarizer → Work Instructions")
        st.markdown("##### (powered by Vertex AI Flash 2.0)")

# --- USER PROMPT AREA ---
default_prompt = (
    "You are an operations specialist with a background in quality analysis and engineering technician practices, "
    "observing a manufacturing process within a controlled ISO 9001:2015 environment.\n\n"
    "Visually and audibly analyze the video input to generate structured work instructions.\n\n"
    "Follow this output template format:\n\n"
    "1.0 Purpose  \n"
    "Describe the purpose of this work instruction.\n\n"
    "2.0 Scope  \n"
    "State the scope of this procedure (e.g., \"This applies to Goodwill Commercial Services\").\n\n"
    "3.0 Responsibilities  \n"
    "List key roles and responsibilities in table format:  \n"
    "ROLE     | RESPONSIBILITY  \n"
    "-------- | ----------------  \n"
    "Line Lead | ● Ensure procedural adherence, documentation, and nonconformance decisions  \n"
    "Operator  | ● Follow instructions and execute the defined procedure\n\n"
    "4.0 Tools, Materials, Equipment, Supplies  \n"
    "Use this table format:  \n"
    "DESCRIPTION | VISUAL | HAZARD  \n"
    "----------- | ------ | ------  \n"
    "(e.g. Box Cutter | [insert image] | Sharp Blade Hazard)\n\n"
    "5.0 Associated Safety and Ergonomic Concerns  \n"
    "List relevant safety issues.  \n"
    "Include a Hazard/Safety Legend with symbols and descriptions where applicable.\n\n"
    "6.0 Procedure  \n"
    "Use the table format below for the step-by-step process:  \n"
    "STEP | ACTION | VISUAL | HAZARD  \n"
    "-----|--------|--------|--------  \n"
    "1 | [Describe action clearly] | [Insert image or frame] | [Identify hazard if any]  \n"
    "2 | [Continue for each step] | [ ] | [ ]\n\n"
    "> If any part of the process is unclear, mark it as: **[uncertain action]**\n\n"
    "7.0 Reference Documents  \n"
    "List any applicable reference SOPs, work orders, or process specs.\n\n"
    "---\n\n"
    "Keep formatting clean and consistent. Ensure that each action step is clearly defined and corresponds with the "
    "appropriate visual frame from the video. Prioritize clarity, safety, and usability."
)
prompt = st.text_area("Edit your prompt", default_prompt, height=200)

# --- UPLOAD & GENERATE ---
video_file = st.file_uploader("Upload a .mp4 manufacturing video", type="mp4")
if video_file:
    st.video(video_file)
    local_path = os.path.join(tmp_dir, video_file.name)
    with open(local_path, "wb") as f: f.write(video_file.read())

    if st.button("Generate Draft Instructions", type="primary"):
        # Upload to GCS
        gcs_path = f"input/{video_file.name}"
        bucket = storage_client.bucket(BUCKET)
        bucket.blob(gcs_path).upload_from_filename(local_path)
        st.success(f"Uploaded to gs://{BUCKET}/{gcs_path}")

        # Call Vertex AI
        with st.spinner("Calling Vertex AI…"):
            resp = client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=[
                    Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs_path}", mime_type="video/mp4"),
                    prompt
                ],
            )
        summary = resp.text
        st.markdown("### Draft Instructions")
        st.code(summary, language="markdown")

        # Extract timestamps & frames
        times = re.findall(r"\[(\d{2}:\d{2}(?::\d{2})?)\]", summary)
        st.session_state.frames = []
        for t in sorted(set(times)):
            img_path = os.path.join(tmp_dir, f"frame_{t.replace(':','_')}.png")
            subprocess.run([
                FFMPEG_EXE, "-y", "-ss", t, "-i", local_path, "-vframes", "1", img_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(img_path):
                st.session_state.frames.append({"time": t, "path": img_path})
        st.session_state.current = 0

# --- IMAGE CAROUSEL & REVIEW ---
if st.session_state.get("frames"):
    st.markdown("## Review Key Frames", unsafe_allow_html=True)
    frame = st.session_state.frames[st.session_state.current]

    with st.container():
        st.image(frame["path"], use_column_width=True, caption=f"Frame at {frame['time']}")
        # pull a snippet from the summary around this timestamp:
        snippet = ""
        for line in summary.splitlines():
            if frame["time"] in line:
                snippet = line
                break
        st.markdown(f"**Description:** {snippet or 'No exact match in draft text.'}")

        cols = st.columns([1,1,1])
        if cols[0].button("← Previous") and st.session_state.current > 0:
            st.session_state.current -= 1
        if cols[1].button("Delete"):
            st.session_state.frames.pop(st.session_state.current)
            st.session_state.current = min(st.session_state.current, len(st.session_state.frames)-1)
        if cols[2].button("Next →") and st.session_state.current < len(st.session_state.frames)-1:
            st.session_state.current += 1

        if st.button("Re-Extract This Frame"):
            t = frame["time"]
            subprocess.run([
                FFMPEG_EXE, "-y", "-ss", t, "-i", local_path, "-vframes", "1", frame["path"]
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            st.success(f"Re-extracted frame at {t}")

# --- FINAL EXPORT ---
if st.session_state.get("frames") and st.button("Generate & Download WI .docx", type="primary"):
    doc = Document()
    doc.add_heading("Work Instructions", 0)
    for block in summary.strip().split("\n\n"):
        lines = block.split("\n")
        doc.add_paragraph(lines[0], style="Heading 2")
        for line in lines[1:]:
            doc.add_paragraph(line)
    docx_path = os.path.join(tmp_dir, "work_instruction.docx")
    doc.save(docx_path)
    with open(docx_path, "rb") as f:
        st.download_button("Download WI .docx", f, file_name="work_instruction.docx")
