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

# --- CONFIGURATION via .streamlit/secrets.toml ---
cfg = st.secrets["gcp"]
PROJECT_ID, LOCATION, BUCKET, SA_BASE64 = (
    cfg["project"], cfg["location"], cfg["bucket"], cfg["sa_key"]
)

# write service account JSON to temp
tmp_dir = tempfile.mkdtemp()
sa_path = os.path.join(tmp_dir, "sa.json")
with open(sa_path, "wb") as f:
    f.write(base64.b64decode(SA_BASE64))

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
os.environ["GOOGLE_CLOUD_PROJECT"]       = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"]      = LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"]  = "True"

# initialize clients
client = genai.Client(http_options=HttpOptions(api_version="v1"))
storage_client = storage.Client()
FFMPEG_EXE = iio_ffmpeg.get_ffmpeg_exe()

# --- PAGE LAYOUT ---
st.set_page_config(page_title="📦 Video-to-WI Generator", layout="centered")

# Logo + Title
st.image("https://i.postimg.cc/L8JXmQ7t/gwlogo1.jpg", width=120)
st.title("Video Summarizer → Work Instructions")
st.caption("powered by Vertex AI Flash 2.0")
st.markdown("---")

# Full prompt (with timestamp instruction)
FULL_PROMPT = """\
You are an operations specialist with a background in quality analysis and engineering technician practices, observing a manufacturing process within a controlled ISO 9001:2015 environment.

Visually and audibly analyze the video input to generate structured work instructions.  
**For each step, prepend the time marker in the video where it occurs, formatted exactly like `[MM:SS]` at the start of the line.**

**Output format**:

1.0 Purpose  
Describe the purpose.

2.0 Scope  
State the scope (e.g., "This applies to Goodwill Commercial Services").

3.0 Responsibilities  
ROLE     | RESPONSIBILITY  
-------- | ----------------  
Line Lead | ● Ensure adherence, documentation, nonconformance decisions  
Operator  | ● Follow instructions and execute procedure

4.0 Tools, Materials, Equipment, Supplies  
DESCRIPTION | VISUAL | HAZARD  
----------- | ------ | ------  
(e.g. Box Cutter | [insert image] | Sharp Blade Hazard)

5.0 Safety & Ergonomic Concerns  
List safety issues; include legend if needed.

6.0 Procedure  
Use a table:  
STEP | ACTION | VISUAL | HAZARD  
-----|--------|--------|--------  
`[MM:SS]` | Describe action | [Insert image/frame] | [Identify hazard]  

7.0 Reference Documents  
List SOPs, work orders, etc.
"""

prompt = st.text_area("Edit your prompt:", value=FULL_PROMPT, height=220)
st.markdown("---")

# Video uploader
video_file = st.file_uploader("Upload a .mp4 video", type="mp4")
if video_file:
    st.video(video_file)  # display inline

    if st.button("Generate Draft Instructions"):
        # Save locally
        local_path = os.path.join(tmp_dir, video_file.name)
        with open(local_path, "wb") as f:
            f.write(video_file.read())

        # Upload to GCS
        gcs_path = f"input/{video_file.name}"
        storage_client.bucket(BUCKET).blob(gcs_path).upload_from_filename(local_path)
        st.success(f"Uploaded to gs://{BUCKET}/{gcs_path}")

        # Call Vertex AI
        with st.spinner("Generating instructions…"):
            resp = client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=[
                    Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs_path}", mime_type="video/mp4"),
                    prompt
                ],
            )
        st.session_state.summary = resp.text

        # Show draft
        st.markdown("#### Draft Work Instructions")
        st.code(st.session_state.summary, language="markdown")

        # Extract timestamps & snippet text
        times = re.findall(r"\[(\d{2}:\d{2})\]", st.session_state.summary)
        st.session_state.frames = []
        st.session_state.steps = []  # store tuples (time, text)
        for line in st.session_state.summary.splitlines():
            m = re.match(r"\[(\d{2}:\d{2})\]\s*(.+)", line)
            if m:
                ts, text = m.groups()
                st.session_state.steps.append((ts, text))

        # Extract frame images
        for ts, _ in st.session_state.steps:
            img_path = os.path.join(tmp_dir, f"frame_{ts.replace(':','_')}.png")
            subprocess.run(
                [FFMPEG_EXE, "-y", "-ss", ts, "-i", local_path, "-vframes", "1", img_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if os.path.exists(img_path):
                st.session_state.frames.append({"time": ts, "path": img_path})

        # initialize carousel
        if st.session_state.frames:
            st.session_state.current = 0

st.markdown("---")

# Interactive image reviewer
if st.session_state.get("frames"):
    st.markdown("### 🖼️ Review Key Frames")
    idx = st.session_state.current
    frame = st.session_state.frames[idx]
    ts = frame["time"]

    # Show image + associated snippet
    st.image(frame["path"], use_container_width=True)
    description = next((txt for t, txt in st.session_state.steps if t == ts), "")
    st.markdown(f"**Timestamp:** {ts}     \n**Description:** {description}")

    # Controls
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("← Previous"):
        st.session_state.current = max(0, idx - 1)
    if c2.button("Delete"):
        st.session_state.frames.pop(idx)
        # also remove step
        st.session_state.steps = [s for s in st.session_state.steps if s[0] != ts]
        st.session_state.current = min(len(st.session_state.frames) - 1, idx)
    if c3.button("Next →"):
        st.session_state.current = min(len(st.session_state.frames) - 1, idx + 1)
    if c4.button("Re-Extract Frame"):
        subprocess.run(
            [FFMPEG_EXE, "-y", "-ss", ts, "-i", local_path, "-vframes", "1", frame["path"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        st.success(f"Re-extracted frame at {ts}")

st.markdown("---")

# Always-on DOCX export
if st.session_state.get("summary"):
    if st.button("Generate & Download WI .docx"):
        doc = Document()
        doc.add_heading("Work Instructions", 0)

        # Walk through each step in the summary
        for ts, text in st.session_state.steps:
            # add the step text
            p = doc.add_paragraph(f"[{ts}] {text}", style="Heading 3")
            # find matching image
            img = next((f["path"] for f in st.session_state.frames if f["time"] == ts), None)
            if img:
                doc.add_picture(img, width=Inches(3))

        # Save & send
        out = os.path.join(tmp_dir, "work_instructions.docx")
        doc.save(out)
        with open(out, "rb") as f:
            st.download_button("Download WI .docx", f, file_name="work_instructions.docx")
