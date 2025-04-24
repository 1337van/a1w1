import streamlit as st
import os
import tempfile
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

# --- AUTHENTICATION via Streamlit Secrets ---
# Add your full service account JSON to Streamlit Secrets under key "service_account"
service_account_info = st.secrets["service_account"]
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
# fetch access token
request = Request()
credentials.refresh(request)
ACCESS_TOKEN = credentials.token

# --- UI SETUP ---
st.set_page_config(page_title="Video-to-WI Generator")
st.title("📦 Video-to-WI Generator")
st.write("Upload a packaging video, enter or edit the prompt, then generate work instructions.")

# --- VIDEO UPLOAD ---
video_file = st.file_uploader("Upload video (.mp4)", type=["mp4"])

if video_file:
    st.video(video_file)
    temp_dir = tempfile.mkdtemp()
    file_path = os.path.join(temp_dir, video_file.name)
    with open(file_path, "wb") as f:
        f.write(video_file.read())

    # upload to GCS
    gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
    gcs_uri  = f"gs://{BUCKET_NAME}/{gcs_path}"
    st.write(f"Uploading to GCS: `{gcs_uri}`")
    client = storage.Client(project=PROJECT_ID, credentials=credentials)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(file_path)
    st.success("Video uploaded to GCS.")

    # prompt
    default_prompt = (
        "You are a quality control analyst observing a packaging process in a regulated manufacturing environment. "
        "Generate clear, step-by-step work instructions based on only what you can see. Include step number, action, materials/tools, safety notes. "
        "Mark unclear steps as [uncertain action]."
    )
    prompt = st.text_area("Summarization Prompt", value=default_prompt, height=180)

    if st.button("Generate Work Instructions"):
        with st.spinner("Calling Vertex AI..."):
            endpoint = (f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}"
                        f"/locations/{REGION}/publishers/google/models/{MODEL_ID}:predict")
            headers = {
                "Authorization": f"Bearer {ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {"instances": [{"prompt": prompt, "video": {"gcsUri": gcs_uri}}]}
            res = requests.post(endpoint, headers=headers, json=payload)
            res.raise_for_status()
            content = res.json()["predictions"][0]["content"]

        # display
        st.subheader("Generated Work Instructions")
        st.code(content, language="markdown")

        # export .docx
        doc = Document()
        doc.add_heading("Work Instruction", level=0)
        for section in content.strip().split("\n\n"):
            lines = section.split("\n")
            doc.add_paragraph(lines[0], style="Heading 2")
            for line in lines[1:]:
                doc.add_paragraph(line)
        out_file = os.path.join(temp_dir, "WI_OUTPUT.docx")
        doc.save(out_file)
        with open(out_file, "rb") as f:
            st.download_button("Download .docx", f, file_name="WI_OUTPUT.docx")

        # cleanup
        blob.delete()
        st.info("Temporary GCS video deleted.")
else:
    st.info("Please upload a video to begin.")
