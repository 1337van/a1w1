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
st.title("📦 Video Summarizer → Work Instructions")
st.caption("powered by Vertex AI Flash 2.0")
st.markdown("---")

# --- FULL PROMPT (1.0–7.0) ---
PROMPT = """\
You are an operations specialist with a background in quality analysis and engineering technician practices, observing a manufacturing process within a controlled ISO 9001:2015 environment.

Visually and audibly analyze the video input to generate structured work instructions.

**For each action step, prefix the line with the video timestamp formatted exactly as `[MM:SS]`.**

Follow this output template format:

1.0 Purpose  
Describe the purpose of this work instruction.

2.0 Scope  
State the scope of this procedure (e.g., "This applies to Goodwill Commercial Services").

3.0 Responsibilities  
List key roles and responsibilities in table format:  
ROLE     | RESPONSIBILITY  
-------- | ----------------  
Line Lead | ● Ensure procedural adherence, documentation, and nonconformance decisions  
Operator  | ● Follow instructions and execute the defined procedure

4.0 Tools, Materials, Equipment, Supplies  
Use this table format:  
DESCRIPTION | VISUAL | HAZARD  
----------- | ------ | ------  
(e.g. Box Cutter | [insert image] | Sharp Blade Hazard)

5.0 Associated Safety and Ergonomic Concerns  
List relevant safety issues.  
Include a Hazard/Safety Legend with symbols and descriptions where applicable.

6.0 Procedure  
Use the table below for the step-by-step process, each row prefixed with `[MM:SS]`:

| STEP | TIMESTAMP | ACTION                     | VISUAL               | HAZARD                         |
| ---- | --------- | -------------------------- | -------------------- | ------------------------------ |
| 1    | `[MM:SS]` | Describe action clearly    | [Insert image/frame] | [Identify hazard if any]       |
| 2    | `[MM:SS]` | Continue for each step     | [Insert image/frame] | [Identify hazard if any]       |

> If any part of the process is unclear, mark it as: **[uncertain action]**

7.0 Reference Documents  
List any applicable reference SOPs, work orders, or process specs.

Keep formatting clean and consistent. Ensure each action step is precisely tied to its visual frame.
"""
prompt = st.text_area("Edit Prompt (1.0–7.0):", value=PROMPT, height=320)
st.markdown("---")

# --- UPLOAD & GENERATE ---
video = st.file_uploader("Upload .mp4 video", type="mp4")
if video and st.button("Generate Draft Work Instructions", type="primary"):
    # save locally
    local = os.path.join(tmp_dir, video.name)
    with open(local, "wb") as f:
        f.write(video.read())

    # upload to GCS
    gcs = f"input/{video.name}"
    storage_client.bucket(BUCKET).blob(gcs).upload_from_filename(local)
    st.success("Video uploaded; generating instructions…")

    # call Vertex AI
    with st.spinner("Generating…"):
        resp = client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs}", mime_type="video/mp4"),
                prompt,
            ],
        )
    summary = resp.text
    st.session_state.summary = summary

    # display draft (no boilerplate)
    st.markdown("#### Draft Instructions (Sections 1.0–7.0)")
    st.code(summary, language="markdown")

    # parse 6.0 Procedure table rows
    steps = []
    in_proc = False
    for line in summary.splitlines():
        if line.strip().startswith("|") and "STEP" in line and "TIMESTAMP" in line:
            in_proc = True
            continue
        if in_proc and line.strip().startswith("|") and re.match(r"\|\s*\d", line):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            step_num = cols[0]
            ts_raw   = cols[1]
            action   = cols[2]
            hazard   = cols[4]
            ts = re.search(r"\[(\d{2}:\d{2})\]", ts_raw).group(1)
            steps.append((step_num, ts, action, hazard))
        # exit when procedure section ends
        if in_proc and line.strip() == "":
            break

    st.session_state.steps = steps

    # extract frames
    frames = []
    for step_num, ts, _, _ in steps:
        img = os.path.join(tmp_dir, f"step_{step_num}_{ts.replace(':','_')}.png")
        subprocess.run(
            [FFMPEG_EXE, "-y", "-ss", ts, "-i", local, "-vframes", "1", img],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if os.path.exists(img):
            frames.append({"step": step_num, "time": ts, "path": img})
    st.session_state.frames = frames
    st.session_state.idx = 0

st.markdown("---")

# --- IMAGE REVIEW ---
if "summary" in st.session_state:
    if st.session_state.frames:
        st.markdown("### 🖼️ Review Step Images")
        i = st.session_state.idx
        step_num, ts, action, hazard = st.session_state.steps[i]
        img_path = st.session_state.frames[i]["path"]

        st.markdown(
            f"**Step {step_num} – [{ts}]**  \n"
            f"**Action:** {action}  \n"
            f"**Hazard:** {hazard}"
        )
        st.image(img_path, use_container_width=True)

        prev_col, del_col, next_col = st.columns(3)
        if prev_col.button("← Previous"):
            st.session_state.idx = max(i-1, 0)
        if del_col.button("Delete"):
            st.session_state.steps.pop(i)
            st.session_state.frames.pop(i)
            st.session_state.idx = min(i, len(st.session_state.frames)-1)
            st.experimental_rerun()
        if next_col.button("Next →"):
            st.session_state.idx = min(i+1, len(st.session_state.frames)-1)
    else:
        st.info("No 6.0 Procedure steps found or no images extracted.")

    st.markdown("---")

    # --- DOCX EXPORT ---
    if st.button("Generate & Download WI .docx"):
        doc = Document()
        doc.add_heading("Work Instructions", 0)
        doc.add_heading("6.0 Procedure", level=1)

        for step_num, ts, action, hazard in st.session_state.steps:
            # Heading: Step X – [MM:SS] Action
            p = doc.add_paragraph(style="Heading 2")
            p.add_run(f"Step {step_num} – [{ts}] {action}")
            # Hazard
            doc.add_paragraph(f"Hazard: {hazard}", style="List Bullet")
            # Image
            img = next((f["path"] for f in st.session_state.frames if f["step"] == step_num), None)
            if img:
                doc.add_picture(img, width=Inches(3))
                doc.add_paragraph()

        out = os.path.join(tmp_dir, "Work_Instructions.docx")
        doc.save(out)
        with open(out, "rb") as f:
            st.download_button("Download WI .docx", f, file_name="Work_Instructions.docx")
