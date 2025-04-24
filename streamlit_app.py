import streamlit as st
import os
import tempfile
import base64
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.cloud import storage
from docx import Document

# --- CONFIGURATION ---
PROJECT_ID    = "a1w104232025"
REGION        = "us-central1"
MODEL_ID      = "video-summary"
BUCKET_NAME   = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"

# --- AUTH via Base64-encoded JSON secret ---
# Store your service-account JSON as a base64 string under key GOOGLE_SERVICE_ACCOUNT_JSON
sa_b64 = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
# Decode and write to temp file
tmp_dir = tempfile.mkdtemp()
sa_path = os.path.join(tmp_dir, "sa.json")
with open(sa_path, "wb") as f:
    f.write(base64.b64decode(sa_b64))
# Load credentials
credentials = service_account.Credentials.from_service_account_file(
    sa_path,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
# Refresh to obtain access token
request = Request()
credentials.refresh(request)
ACCESS_TOKEN = credentials.token

# --- UI Setup ---
st.set_page_config(page_title="Video Summarizer Work Instruction Tool")
st.title("📦 Video-to-WI Generator")
st.markdown("Upload a packaging video, edit prompt, and generate work instructions.")

# --- Video Upload ---
video_file = st.file_uploader("Upload a video (.mp4)", type=["mp4"])

# --- Default Prompt ---
def_prompt = """
You are a quality control analyst observing a packaging process. Generate clear, step-by-step work instructions based on visual observation only. Include step number, action description, materials/tools, and safety notes. Mark unclear steps as [uncertain action].
"""
prompt = st.text_area("Prompt", value=def_prompt, height=200)

if video_file:
    st.video(video_file)
    # Save locally
    vid_dir = tempfile.mkdtemp()
    vid_path = os.path.join(vid_dir, video_file.name)
    with open(vid_path, "wb") as f:
        f.write(video_file.read())
    # Upload to GCS
    gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
    gcs_uri = f"gs://{BUCKET_NAME}/{gcs_path}"
    st.markdown(f"**Uploading to GCS:** {gcs_uri}")
    client = storage.Client(project=PROJECT_ID, credentials=credentials)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(vid_path)
    st.success("✅ Video uploaded to GCS.")

    # --- Call Vertex AI ---
    st.markdown("### Generated Work Instructions")
    with st.spinner("Summarizing..."):
        endpoint = (
            f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{REGION}/publishers/google/models/{MODEL_ID}:predict"
        )
        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {"instances": [{"prompt": prompt, "video": {"gcsUri": gcs_uri}}]}
        res = requests.post(endpoint, headers=headers, json=payload)
        res.raise_for_status()
        summary = res.json()["predictions"][0]["content"]
    st.code(summary, language="markdown")

    # --- Export to DOCX ---
    st.markdown("### Download Work Instruction (.docx)")
    doc = Document()
    doc.add_heading("Work Instruction", 0)
    for block in summary.strip().split("\n\n"):
        lines = block.split("\n")
        doc.add_paragraph(lines[0], style="Heading 2")
        for line in lines[1:]:
            doc.add_paragraph(line)
    out_path = os.path.join(tmp_dir, "WI_OUTPUT.docx")
    doc.save(out_path)
    with open(out_path, "rb") as f:
        st.download_button("Download .docx", f, file_name="WI_OUTPUT.docx")

    # Cleanup GCS
    blob.delete()
    st.info("✅ Temporary video deleted from GCS.")
else:
    st.info("Upload a video to begin.")
