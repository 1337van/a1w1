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

# --- UI SETUP ---
st.set_page_config(page_title="📦 Video-to-WI Generator", layout="centered")
st.image("https://i.postimg.cc/L8JXmQ7t/gwlogo1.jpg", width=120)
st.title("Video Summarizer → Work Instructions")
st.markdown("Upload a manufacturing video, edit the prompt, generate WI, then review & export.")

# --- PROMPT ---
default_prompt = """You are an operations specialist with a background in quality analysis and engineering technician practices, observing a manufacturing process in an ISO 9001:2015 environment.

**Prefix each action step with its video timestamp `[MM:SS]`.**

Follow this template:

1.0 Purpose  
…  
2.0 Scope  
…  
3.0 Responsibilities  
…  
4.0 Tools, Materials, Equipment, Supplies  
…  
5.0 Safety & Ergonomics  
…  
6.0 Procedure  
STEP | TIMESTAMP | ACTION | VISUAL | HAZARD  
-----|-----------|--------|--------|-------  
1    | `[00:01]` | Pick up sheet | [00:01 frame] | None  
2    | `[00:02]` | Fold paper     | [00:02 frame] | Paper cut risk  
…  
> If unclear, mark **[uncertain action]**.  

7.0 Reference Documents  
…  
"""
prompt = st.text_area("Prompt", default_prompt, height=250)

# --- VIDEO UPLOAD, AI CALL & SUMMARY DISPLAY ---
video_file = st.file_uploader("Upload .mp4 video", type="mp4")
if video_file:
    st.video(video_file)
    if st.button("Generate Draft WI", type="primary"):
        # save locally
        local_path = os.path.join(tmp_dir, video_file.name)
        with open(local_path, "wb") as f:
            f.write(video_file.read())

        # upload to GCS
        gcs_path = f"input/{video_file.name}"
        storage_client.bucket(BUCKET).blob(gcs_path).upload_from_filename(local_path)
        st.success("Video uploaded, generating…")

        # call Vertex AI
        with st.spinner("Calling Vertex AI…"):
            resp = client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=[
                    Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs_path}", mime_type="video/mp4"),
                    prompt
                ],
            )
        summary = resp.text
        st.session_state.summary = summary

        # display full draft
        st.markdown("#### Draft Instructions (1.0–7.0)")
        st.code(summary, language="markdown")

        # --- PARSE 6.0 PROCEDURE into steps ---
        steps = []
        lines = summary.splitlines()
        # find header
        try:
            idx = next(i for i,l in enumerate(lines) if l.strip().startswith("6.0"))
        except StopIteration:
            idx = -1
        if idx>=0:
            # skip 2 lines (header + separator)
            for row in lines[idx+2:]:
                if not row.strip().startswith("|"):
                    break
                cols = [c.strip() for c in row.split("|")[1:-1]]
                if len(cols) < 5 or not cols[0].isdigit():
                    continue
                num = cols[0]
                ts_match = re.search(r"\[(\d{2}:\d{2})\]", cols[1])
                if not ts_match:
                    continue
                ts = ts_match.group(1)
                action = cols[2]
                hazard = cols[4]
                steps.append((num, ts, action, hazard))
        st.session_state.steps = steps

        # --- EXTRACT FRAMES for each step ---
        frames = []
        for _, ts, _, _ in steps:
            img = os.path.join(tmp_dir, f"frame_{ts.replace(':','_')}.png")
            subprocess.run(
                [FFMPEG_EXE, "-y", "-ss", ts, "-i", local_path, "-vframes", "1", img],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if os.path.exists(img):
                frames.append({"time": ts, "path": img})
        st.session_state.frames = frames

# --- IMAGE DROPDOWN REVIEW ---
if st.session_state.get("steps"):
    st.markdown("### 🖼️ Review Step Images")
    labels = [f"Step {n} – [{t}] {a}" for n,t,a,h in st.session_state.steps]
    choice = st.selectbox("Select a step", labels)
    idx = labels.index(choice)
    ts = st.session_state.steps[idx][1]
    hazard = st.session_state.steps[idx][3]
    st.markdown(f"**{choice}**  \n_Hazard:_ {hazard}")
    st.image(st.session_state.frames[idx]["path"], use_container_width=True)

# --- DOCX EXPORT WITH IMAGES ---
if st.session_state.get("steps"):
    st.markdown("---")
    if st.button("Export WI as DOCX"):
        doc = Document()
        doc.add_heading("Work Instructions", 0)

        # write everything before 6.0
        pre6 = "\n".join(summary.split("6.0 Procedure")[0].splitlines())
        for block in pre6.split("\n\n"):
            for line in block.splitlines():
                if line.strip():
                    if re.match(r"^\d\.\d", line):
                        doc.add_heading(line, level=1)
                    else:
                        doc.add_paragraph(line)

        # 6.0 with images
        doc.add_heading("6.0 Procedure", level=1)
        for idx, (num, ts, action, hazard) in enumerate(st.session_state.steps, start=1):
            p = doc.add_paragraph(style="Heading 2")
            p.add_run(f"Step {num} – [{ts}] {action}")
            if hazard:
                doc.add_paragraph(f"Hazard: {hazard}", style="List Bullet")
            img = st.session_state.frames[idx-1]["path"]
            doc.add_picture(img, width=Inches(3))
            doc.add_paragraph()

        # write anything after the table (7.0)
        post6 = summary.split("6.0 Procedure")[-1]
        for block in post6.split("\n\n")[1:]:
            for line in block.splitlines():
                if line.strip():
                    if line.startswith("7.0"):
                        doc.add_heading(line, level=1)
                    else:
                        doc.add_paragraph(line)

        out = os.path.join(tmp_dir, "Work_Instructions.docx")
        doc.save(out)
        with open(out, "rb") as f:
            st.download_button("Download WI .docx", f, file_name="Work_Instructions.docx")
