import streamlit as st
from PIL import Image
import os
import tempfile
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.cloud import storage
# from moviepy.editor import VideoFileClip  # Disabled on Streamlit Cloud
from docx import Document
from docx.shared import Inches

# --- CONFIGURATION ---
PROJECT_ID    = "a1w104232025"
REGION        = "us-central1"
MODEL_ID      = "video-summary"
BUCKET_NAME   = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"

# --- AUTHENTICATION via Local JSON Key ---
# Make sure service_account.json (your downloaded key) is at the repo root and listed in .gitignore
SERVICE_ACCOUNT_FILE = "service_account.json"
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
# Refresh to get an access token
request = Request()
credentials.refresh(request)
ACCESS_TOKEN = credentials.token

# --- UI Setup ---
st.set_page_config(page_title="Video Summarizer Work Instruction Tool")
st.title("📦 Video-to-WI Generator")
st.markdown("Upload a packaging video, select a prompt, and generate structured work instructions.")

# --- Video Upload ---
video_file = st.file_uploader("Upload a video file (.mp4)", type=["mp4"])

# --- Prompt Section ---
def_prompt = """
You are a quality control analyst observing a packaging process in a regulated manufacturing environment.\
Generate step-by-step work instructions based on visual observation only.\
For each step, include: step number, description, tools/materials, and any handling or safety notes.\
Mark unclear steps as [uncertain action].
"""
prompt = st.text_area("Prompt for Video Summarization", value=def_prompt, height=200)

if video_file:
    st.video(video_file)

    # Save locally
    temp_dir = tempfile.mkdtemp()
    video_path = os.path.join(temp_dir, video_file.name)
    with open(video_path, "wb") as f:
        f.write(video_file.read())

    # Upload to GCS
    gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
    gcs_uri  = f"gs://{BUCKET_NAME}/{gcs_path}"
    st.markdown(f"**Uploading to GCS:** `{gcs_uri}`")
    storage_client = storage.Client(credentials=credentials, project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob   = bucket.blob(gcs_path)
    blob.upload_from_filename(video_path)
    st.success("✅ Video uploaded to GCS.")

    # --- (Frame extraction disabled) ---
    st.markdown("### 🖼 Extracted Frames (Disabled)")

    # --- Call Vertex AI Video Summarizer ---
    st.markdown("### ✏️ Generated Work Instructions")
    with st.spinner("Summarizing video…"):
        try:
            endpoint = (
                f"https://{REGION}-aiplatform.googleapis.com"
                f"/v1/projects/{PROJECT_ID}/locations/{REGION}"
                f"/publishers/google/models/{MODEL_ID}:predict"
            )
            headers = {
                "Authorization": f"Bearer {ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {"instances": [{"prompt": prompt, "video": {"gcsUri": gcs_uri}}]}
            resp = requests.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            summary = resp.json()["predictions"][0]["content"]

            st.code(summary, language="markdown")

            # --- Export to .docx ---
            st.markdown("### 📄 Download Work Instruction")
            doc = Document()
            doc.add_heading("Work Instruction", level=0)
            for section in summary.strip().split("\n\n"):
                lines = section.split("\n")
                doc.add_paragraph(lines[0], style="Heading 2")
                for line in lines[1:]:
                    doc.add_paragraph(line)
            out_path = os.path.join(temp_dir, "WI_OUTPUT.docx")
            doc.save(out_path)
            with open(out_path, "rb") as f:
                st.download_button("Download .docx", f, file_name="WI_OUTPUT.docx")

            # Clean up GCS
            blob.delete()
            st.info("✅ Temporary video deleted from GCS.")
        except Exception as e:
            st.error(f"Error during summary: {e}")
else:
    st.info("Upload a video to get started.")
