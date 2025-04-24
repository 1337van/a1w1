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

# --- AUTH via Application Default Credentials ---
# Decode the base64-encoded service account JSON stored in Streamlit Secrets
sa_b64 = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON")
if not sa_b64:
    st.error("Missing GOOGLE_SERVICE_ACCOUNT_JSON in Streamlit Secrets")
    st.stop()
sa_json = base64.b64decode(sa_b64)
# Write it to a temp file and set the ADC environment variable
creds_path = os.path.join(tempfile.gettempdir(), "sa.json")
with open(creds_path, "wb") as f:
    f.write(sa_json)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

# Initialize GCP clients with ADC
storage_client = storage.Client()
aiplatform.init(project=PROJECT_ID, location=REGION)

# --- UI Setup ---
st.set_page_config(page_title="Video-to-WI Generator")
st.title("📦 Video-to-WI Generator")
st.write("Upload a packaging video, customize your prompt, then generate and download work instructions.")

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
    st.success("✅ Video uploaded to GCS.")

    # Prompt entry
    default_prompt = (
        "You are a quality control analyst observing a packaging process in a regulated environment. "
        "Generate clear, step-by-step work instructions based on visual cues only. Include step number, action, materials/tools, safety notes. "
        "Mark unclear steps as [uncertain action]."
    )
    prompt = st.text_area("Summarization Prompt", value=default_prompt, height=200)

    if st.button("Generate Work Instructions"):
        with st.spinner("Contacting Vertex AI..."):
            client = aiplatform.gapic.PredictionServiceClient()
            model_name = client.model_path(PROJECT_ID, REGION, f"publishers/google/models/{MODEL_ID}")
            response = client.predict(
                endpoint=model_name,
                instances=[{"prompt": prompt, "video": {"gcsUri": gcs_uri}}]
            )
            content = response.predictions[0]["content"]

        # Display summary
        st.subheader("Generated Work Instructions")
        st.code(content, language="markdown")

        # Export to DOCX
        doc = Document()
        doc.add_heading("Work Instruction", level=0)
        for block in content.strip().split("\n\n"):
            lines = block.split("\n")
            doc.add_paragraph(lines[0], style="Heading 2")
            for line in lines[1:]:
                doc.add_paragraph(line)
        out_doc = os.path.join(tmp_dir, "WI_OUTPUT.docx")
        doc.save(out_doc)
        with open(out_doc, "rb") as f:
            st.download_button("Download .docx", f, file_name="WI_OUTPUT.docx")

        # Clean up GCS
        blob.delete()
        st.info("✅ Temporary video deleted from GCS.")
else:
    st.info("Please upload a .mp4 video to begin.")
