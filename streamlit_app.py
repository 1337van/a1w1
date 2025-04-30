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

# Write service account JSON to temp file
tmp_dir = tempfile.mkdtemp()
sa_path = os.path.join(tmp_dir, "sa.json")
with open(sa_path, "wb") as f:
    f.write(base64.b64decode(SA_BASE64))

# Set Google Cloud env
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
os.environ["GOOGLE_CLOUD_PROJECT"]       = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"]      = LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"]  = "True"

# Initialize clients
client = genai.Client(http_options=HttpOptions(api_version="v1"))
storage_client = storage.Client()

# Locate the bundled ffmpeg binary
FFMPEG_EXE = iio_ffmpeg.get_ffmpeg_exe()

# --- UI Setup ---
st.set_page_config(page_title="📦 Video-to-WI Generator")
# 1) Logo at the very top
st.image("https://i.postimg.cc/L8JXmQ7t/gwlogo1.jpg", width=120)
st.title("Video Summarizer → Work Instructions")
st.markdown(
    "Upload a manufacturing video, tweak the prompt, generate and review work instructions, and preview key frames."
)

# 2) Prompt now explicitly asks for time markers
def_prompt = (
    "You are an operations specialist with a background in quality analysis and engineering technician practices, "
    "observing a manufacturing process within a controlled ISO 9001:2015 environment.\n\n"
    "Visually and audibly analyze the video input to generate structured work instructions.\n\n"
    "**For each action step, prefix the line with the video timestamp formatted exactly as `[MM:SS]`.**\n\n"
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
    "Use the table below for the step-by-step process, each row prefixed with `[MM:SS]`:\n\n"
    "| STEP     | ACTION                     | VISUAL               | HAZARD                         |\n"
    "| -------- | -------------------------- | -------------------- | ------------------------------ |\n"
    "| `[MM:SS]` | Describe action clearly   | [Insert image/frame] | [Identify hazard if any]       |\n"
    "| `[MM:SS]` | Continue for each step    | [Insert image/frame] | [Identify hazard if any]       |\n\n"
    "> If any part of the process is unclear, mark it as: **[uncertain action]**\n\n"
    "7.0 Reference Documents  \n"
    "List any applicable reference SOPs, work orders, or process specs.\n\n"
    "---\n\n"
    "Keep formatting clean and consistent. Ensure each action step is precisely tied to its visual frame."
)

prompt = st.text_area("Prompt", value=def_prompt, height=250)

if video_file := st.file_uploader("Upload .mp4 video", type=["mp4"]):
    st.video(video_file)

    # Save locally
    local_path = os.path.join(tmp_dir, video_file.name)
    with open(local_path, "wb") as f:
        f.write(video_file.read())

    # Upload to GCS
    gcs_path = f"input/{video_file.name}"
    bucket = storage_client.bucket(BUCKET)
    blob = bucket.blob(gcs_path)
    try:
        blob.upload_from_filename(local_path)
        st.success(f"Uploaded to gs://{BUCKET}/{gcs_path}")
    except Exception as e:
        st.error(f"Failed to upload video: {e}")
        st.stop()

    # Generate with Vertex AI
    st.markdown("### ✏️ Generating Work Instructions…")
    try:
        resp = client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                Part.from_uri(file_uri=f"gs://{BUCKET}/{gcs_path}", mime_type="video/mp4"),
                prompt
            ],
        )
        summary = resp.text
        st.markdown("#### Draft Instructions")
        st.code(summary, language="markdown")
    except Exception as e:
        st.error(f"Vertex AI request failed: {e}")
        st.stop()

    # Preview key frames based on those timestamps
    st.markdown("### 🖼️ Key Frame Previews")
    times = re.findall(r"\[(\d{2}:\d{2})\]", summary)
    for t in sorted(set(times)):
        img_path = os.path.join(tmp_dir, f"frame_{t.replace(':','_')}.png")
        subprocess.run(
            [FFMPEG_EXE, "-y", "-ss", t, "-i", local_path, "-vframes", "1", img_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if os.path.exists(img_path):
            st.image(img_path, caption=f"Frame at {t}")

    # Export to DOCX
    st.markdown("### 📄 Download as DOCX")
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
