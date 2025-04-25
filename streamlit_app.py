```python
# streamlit_app.py
import streamlit as st
import os
import tempfile
import base64
from google import genai
from google.genai.types import HttpOptions, Part
from google.cloud import storage
from docx import Document
from docx.shared import Inches

# --- CONFIGURATION ---
# Secrets.toml should include a [gcp] section with:
# project, location, and sa_key (base64 of your service-account JSON)
PROJECT_ID = st.secrets["gcp"]["project"]
LOCATION   = st.secrets["gcp"]["location"]
SA_BASE64  = st.secrets["gcp"]["sa_key"]

# Decode and write out the service account key
tmp_dir = tempfile.mkdtemp()
sa_path = os.path.join(tmp_dir, "sa.json")
with open(sa_path, "wb") as f:
    f.write(base64.b64decode(SA_BASE64))

# Point Application Default Credentials at the file
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
# Required for the GenAI SDK
os.environ["GOOGLE_CLOUD_PROJECT"]       = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"]     = LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

# Initialize the Gemini client
client = genai.Client(http_options=HttpOptions(api_version="v1"))

# Initialize the GCS client
storage_client = storage.Client()

# --- UI SETUP ---
st.set_page_config(page_title="Video Summarizer Work Instruction Tool")
st.title("📦 Video-to-WI Generator")
st.markdown("Upload a packaging video, tweak the prompt, and generate structured work instructions.")

# --- Video Upload ---
video_file = st.file_uploader("Upload .mp4 video", type=["mp4"])

def_prompt = '''
You are a quality control analyst observing a packaging process. Analyze the video visually and generate step-by-step work instructions.
- Include step number, action, tools/materials, and observations.
- If uncertain, mark [uncertain action].
'''
prompt = st.text_area("Prompt", value=def_prompt, height=200)

if video_file:
    # Preview
    st.video(video_file)

    # Save locally
    temp_dir = tempfile.mkdtemp()
    local_path = os.path.join(temp_dir, video_file.name)
    with open(local_path, "wb") as f:
        f.write(video_file.read())

    # Upload to GCS
    gcs_path = f"input/{video_file.name}"
    bucket = storage_client.bucket(st.secrets["gcp"]["bucket"])
    blob = bucket.blob(gcs_path)
    try:
        blob.upload_from_filename(local_path)
        st.success(f"Uploaded to gs://{bucket.name}/{gcs_path}")
    except Exception as e:
        st.error(f"Failed to upload video: {e}")
        st.stop()

    gcs_uri = f"gs://{bucket.name}/{gcs_path}"

    # --- Call Gemini-Video Summarizer ---
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

    # --- Export to .docx ---
    st.markdown("### 📄 Download as DOCX")
    doc = Document()
    doc.add_heading("Work Instructions", 0)
    for block in summary.strip().split("\n\n"):
        lines = block.split("\n")
        doc.add_paragraph(lines[0], style="Heading 2")
        for line in lines[1:]:
            doc.add_paragraph(line)
    docx_path = os.path.join(temp_dir, "work_instruction.docx")
    doc.save(docx_path)
    with open(docx_path, "rb") as f:
        st.download_button("Download WI .docx", f, file_name="work_instruction.docx")
