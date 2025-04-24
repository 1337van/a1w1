import streamlit as st
import os
import tempfile
import base64
import requests
from google.cloud import storage, aiplatform
from docx import Document

# --- CONFIGURATION ---
PROJECT_ID    = "a1w104232025"
REGION        = "us-central1"
MODEL_ID      = "video-summary"
BUCKET_NAME   = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"

# --- AUTH via ADC from service_account JSON ---
# SECRET_KEY = base64-encoded contents of your service_account.json
sa_b64 = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
sa_json = base64.b64decode(sa_b64)
with open("sa.json", "wb") as f:
    f.write(sa_json)
# point Application Default Credentials to it
ios.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "sa.json"

# Initialize clients using ADC
storage_client = storage.Client()
aiplatform.init(project=PROJECT_ID, location=REGION)

# --- UI Setup ---
st.set_page_config(page_title="Video-to-WI Generator")
st.title("📦 Video-to-WI Generator")
st.write("Upload a packaging video, edit the prompt below, and generate work instructions.")

# --- Video Upload ---
video_file = st.file_uploader("Upload video (.mp4)", type=["mp4"])

if video_file:
    st.video(video_file)
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, video_file.name)
    with open(tmp_path, "wb") as f:
        f.write(video_file.read())

    # Upload to GCS
    gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
    gcs_uri  = f"gs://{BUCKET_NAME}/{gcs_path}"
    st.info(f"Uploading to {gcs_uri}")
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(tmp_path)
    st.success("Uploaded to GCS.")

    # Prompt
    default_prompt = (
        "You are a quality control analyst observing a packaging process in a regulated environment. "
        "Generate step-by-step work instructions based on visual cues only. Include step number, action, materials/tools, safety notes. "
        "Mark unclear steps as [uncertain action]."
    )
    prompt = st.text_area("Prompt", value=default_prompt, height=200)

    if st.button("Generate Work Instructions"):
        with st.spinner("Contacting Vertex AI..."):
            client = aiplatform.gapic.PredictionServiceClient()
            name = client.model_path(PROJECT_ID, REGION, f"publishers/google/models/{MODEL_ID}")
            response = client.predict(
                endpoint=name,
                instances=[{"prompt": prompt, "video": {"gcsUri": gcs_uri}}]
            )
            content = response.predictions[0]["content"]
        st.subheader("Work Instructions")
        st.code(content, language="markdown")

        # Export DOCX
        doc = Document()
        doc.add_heading("Work Instruction", level=0)
        for block in content.strip().split("\n\n"):
            lines = block.split("\n")
            doc.add_paragraph(lines[0], style="Heading 2")
            for line in lines[1:]:
                doc.add_paragraph(line)
        out_path = os.path.join(tmp_dir, "WI_OUTPUT.docx")
        doc.save(out_path)
        with open(out_path, "rb") as f:
            st.download_button("Download .docx", f, file_name="WI_OUTPUT.docx")

        blob.delete()
        st.info("Cleaned up GCS file.")
else:
    st.info("Please upload a .mp4 video to get started.")
