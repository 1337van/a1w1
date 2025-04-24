import streamlit as st
import os
import tempfile
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.cloud import storage
from docx import Document

# --- CONFIGURATION ---
PROJECT_ID = "a1w104232025"
REGION     = "us-central1"
MODEL_ID   = "video-summary"
BUCKET_NAME = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"

# --- AUTHENTICATION via Streamlit Secrets ---
# Ensure your service account JSON is saved in Streamlit Secrets under the key "service_account"
service_account_info = st.secrets["service_account"]
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
# Refresh to get a valid access token
request = Request()
credentials.refresh(request)
ACCESS_TOKEN = credentials.token

# --- UI SETUP ---
st.set_page_config(page_title="Video Summarizer Work Instruction Tool")
st.title("📦 Video-to-WI Generator")
st.markdown("Upload a packaging video, enter a prompt, and generate work instructions.")

# --- VIDEO UPLOAD ---
video_file = st.file_uploader("Upload a packaging video (.mp4)", type=["mp4"])

if video_file:
    st.video(video_file)
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, video_file.name)
    with open(tmp_path, "wb") as f:
        f.write(video_file.read())

    # Upload to GCS
    gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
    gcs_uri = f"gs://{BUCKET_NAME}/{gcs_path}"
    st.markdown(f"**Uploading to GCS:** {gcs_uri}")
    client = storage.Client(project=PROJECT_ID, credentials=credentials)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(tmp_path)
    st.success("✅ Video uploaded to GCS.")

    # Prompt input
    prompt = st.text_area("Enter prompt for summarization", height=200)
    if st.button("Generate Work Instructions"):
        with st.spinner("Calling Vertex AI..."):
            endpoint = (f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}" 
                        f"/locations/{REGION}/publishers/google/models/{MODEL_ID}:predict")
            headers = {
                "Authorization": f"Bearer {ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {"instances": [{"prompt": prompt, "video": {"gcsUri": gcs_uri}}]}
            response = requests.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            summary = response.json()["predictions"][0]["content"]

        st.code(summary, language="markdown")

        # Export to DOCX
        doc = Document()
        doc.add_heading("Work Instruction", level=0)
        for block in summary.strip().split("\n\n"):
            lines = block.split("\n")
            doc.add_paragraph(lines[0], style="Heading 2")
            for line in lines[1:]:
                doc.add_paragraph(line)
        doc_path = os.path.join(tmp_dir, "WI_OUTPUT.docx")
        doc.save(doc_path)
        with open(doc_path, "rb") as f:
            st.download_button("Download Work Instruction (.docx)", f, file_name="WI_OUTPUT.docx")

        # Cleanup GCS
        blob.delete()
        st.info("✅ Temporary video deleted from GCS.")
else:
    st.info("Please upload a video to begin.")
