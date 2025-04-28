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

# --- CONFIGURATION via .streamlit/secrets.toml ---
cfg = st.secrets["gcp"]
PROJECT_ID, LOCATION, BUCKET, SA_BASE64 = (
    cfg["project"], cfg["location"], cfg["bucket"], cfg["sa_key"]
)

# write service account JSON
tmp_dir = tempfile.mkdtemp()
sa_path = os.path.join(tmp_dir, "sa.json")
with open(sa_path, "wb") as f:
    f.write(base64.b64decode(SA_BASE64))

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

# initialize clients
client = genai.Client(http_options=HttpOptions(api_version="v1"))
storage_client = storage.Client()
FFMPEG_EXE = iio_ffmpeg.get_ffmpeg_exe()

# --- STYLING & LAYOUT ---
st.set_page_config(page_title="📦 Video-to-WI Generator", layout="wide")
st.markdown(
    """
    <style>
    .block-container { padding: 1rem 2rem; background: #f0f4f8; }
    button[kind="primary"] { background-color: #0057a6 !important; border-color: #0057a6 !important; }
    .logo { max-width: 150px; margin-bottom: 1rem; }
    @media (max-width: 600px) {
      .logo { max-width: 100px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- BRANDING HEADER ---
logo_url = "https://yourdomain.com/path/to/goodwill-logo.png"
col_logo, col_title = st.columns([1, 4])
with col_logo:
    st.image(logo_url, use_container_width=True, caption="Goodwill", output_format="PNG")
with col_title:
    st.markdown("## Video Summarizer → Work Instructions")
    st.markdown("##### powered by Vertex AI Flash 2.0")

# --- USER PROMPT ---
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
2 | [...] | [...] | [...]

> If unclear, mark: [uncertain action]

7.0 Reference Documents  
List any applicable SOPs, work orders, or specs.
"""
prompt = st.text_area("Edit your prompt:", default_prompt, height=200)

# --- VIDEO UPLOAD & GENERATION ---
video_file = st.file_uploader("Upload a .mp4 manufacturing video", type="mp4")
if video_file:
    # show video in a smaller column for desktop, full width on mobile
    vcol1, vcol2 = st.columns([1, 1])
    with vcol1:
        st.video(video_file, format="video/mp4")
    with vcol2:
        st.markdown("### Ready to generate draft instructions?")

    # save locally
    local_path = os.path.join(tmp_dir, video_file.name)
    with open(local_path, "wb") as f:
        f.write(video_file.read())

    if st.button("Generate Draft Instructions", type="primary"):
        # upload
        gcs_path = f"input/{video_file.name}"
        bucket = storage_client.bucket(BUCKET)
        bucket.blob(gcs_path).upload_from_filename(local_path)
        st.success(f"Uploaded to gs://{BUCKET}/{gcs_path}")

        # call Vertex AI
        with st.spinner("Calling Vertex AI..."):
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
        times = re.findall(r"\[(\d{2}:\d{2}(?::\d{2})?)\]", st.session_state.summary)
        st.session_state.frames = []
        for t in sorted(set(times)):
            img_path = os.path.join(tmp_dir, f"frame_{t.replace(':','_')}.png")
            subprocess.run(
                [FFMPEG_EXE, "-y", "-ss", t, "-i", local_path, "-vframes", "1", img_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.path.exists(img_path):
                st.session_state.frames.append({"time": t, "path": img_path})
        st.session_state.current = 0

# --- IMAGE CAROUSEL & REVIEW ---
if st.session_state.get("frames"):
    st.markdown("### Review Key Frames")
    frame = st.session_state.frames[st.session_state.current]

    icol1, icol2 = st.columns([1, 1])
    with icol1:
        st.image(frame["path"], use_container_width=True)
    with icol2:
        st.markdown(f"**Timestamp:** {frame['time']}")
        # snippet
        snippet = next(
            (line for line in st.session_state.summary.splitlines() if frame["time"] in line),
            None,
        )
        st.markdown(f"**Description:** {snippet or 'No match in text.'}")

        # controls
        c1, c2, c3 = st.columns(3)
        if c1.button("← Previous") and st.session_state.current > 0:
            st.session_state.current -= 1
        if c2.button("Delete"):
            st.session_state.frames.pop(st.session_state.current)
            st.session_state.current = min(st.session_state.current, len(st.session_state.frames) - 1)
        if c3.button("Next →") and st.session_state.current < len(st.session_state.frames) - 1:
            st.session_state.current += 1

        if st.button("Re-Extract This Frame"):
            t = frame["time"]
            subprocess.run(
                [FFMPEG_EXE, "-y", "-ss", t, "-i", local_path, "-vframes", "1", frame["path"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            st.success(f"Re-extracted frame at {t}")

# --- FINAL DOCX EXPORT BUTTON ---
if st.session_state.get("summary") and st.session_state.get("frames"):
    if st.button("Generate & Download WI .docx", type="primary"):
        doc = Document()
        doc.add_heading("Work Instructions", 0)
        for block in st.session_state.summary.strip().split("\n\n"):
            lines = block.split("\n")
            doc.add_paragraph(lines[0], style="Heading 2")
            for line in lines[1:]:
                doc.add_paragraph(line)
        out_path = os.path.join(tmp_dir, "work_instruction.docx")
        doc.save(out_path)
        with open(out_path, "rb") as f:
            st.download_button("Download WI .docx", f, file_name="work_instruction.docx")
