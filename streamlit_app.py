import streamlit as st
import os
import tempfile
import base64
import re
import subprocess
from google import genai
from google.genai.types import HttpOptions, Part
from google.cloud import storage
from docx import Document
from docx.shared import Inches
from PIL import Image

# --- CONFIGURATION via .streamlit/secrets.toml ---
# .streamlit/secrets.toml should include:
# [gcp]
# project  = "a1w104232025"
# location = "us-central1"
# bucket   = "a1w1"
# sa_key   = "<BASE64_OF_SERVICE_ACCOUNT_JSON>"

cfg = st.secrets["gcp"]
PROJECT_ID = cfg["project"]
LOCATION   = cfg["location"]
BUCKET     = cfg["bucket"]
SA_BASE64  = cfg["sa_key"]

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

# --- UI Setup ---
st.set_page_config(page_title="📦 Video-to-WI Generator")
st.title("Video Summarizer → Work Instructions")
st.markdown(
    "Upload a packaging video, tweak the prompt, generate and review work instructions, and preview key frames."
)

# Upload
video_file = st.file_uploader("Upload .mp4 video", type=["mp4"])
def_prompt = (
    "You are a quality control analyst observing a packaging process. "
    "Analyze the video visually and generate step-by-step work instructions. "
    "Include step number, action, tools/materials, and observations. "
    "If uncertain, mark [uncertain action]."
)
prompt = st.text_area("Prompt", value=def_prompt, height=200)

if video_file:
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

    gcs_uri = f"gs://{BUCKET}/{gcs_path}"
    st.markdown("### ✏️ Generating Work Instructions…")
    try:
        resp = client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[
                Part.from_uri(file_uri=gcs_uri, mime_type="video/mp4"),
                prompt
            ],
        )
        summary = resp.text
        st.markdown("#### Draft Instructions")
        st.code(summary, language="markdown")
    except Exception as e:
        st.error(f"Vertex AI request failed: {e}")
        st.stop()

    # --- Preview Key Frames Based on Timestamps ---
    st.markdown("### 🖼️ Key Frame Previews")
    times = re.findall(r"\[(\d{2}:\d{2}(?::\d{2})?)", summary)
    for t in sorted(set(times)):
        # Prepare output image path
        img_path = os.path.join(tmp_dir, f"frame_{t.replace(':','_')}.png")
        # Extract frame via ffmpeg
        cmd = [
            "ffmpeg", "-y", "-ss", t, "-i", local_path,
            "-vframes", "1", img_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
