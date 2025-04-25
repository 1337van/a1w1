import streamlit as st
import os
import tempfile
import subprocess
import re
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.cloud import storage, aiplatform
from google.cloud.aiplatform.gapic import PredictionServiceClient
from docx import Document

# --- CONFIGURATION ---
PROJECT_ID    = "a1w104232025"
REGION        = "us-central1"
MODEL_ID      = "gemini-1.0-mm-text-video"  # Vertex AI multimodal video model
BUCKET_NAME   = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"

# --- AUTHENTICATION ---
# Load creds from Streamlit secrets
creds = service_account.Credentials.from_service_account_info(
    st.secrets["service_account"],
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)

# Initialize clients with explicit creds
storage_client = storage.Client(credentials=creds, project=PROJECT_ID)
aiplatform.init(
    credentials=creds,
    project=PROJECT_ID,
    location=REGION
)
predict_client = PredictionServiceClient(
    client_options={"api_endpoint": f"{REGION}-aiplatform.googleapis.com"},
    credentials=creds
)

# --- STREAMLIT UI ---
st.set_page_config(page_title="📦 Video-to-WI Generator", layout="wide")
st.title("Video-to-Work Instruction Generator")
st.markdown("Upload a packaging video, edit the prompt, review output, and extract frames.")

# Prompt editor
default_prompt = (
    "You are a quality control analyst observing a packaging process. "
    "Generate clear, step-by-step work instructions based purely on the visuals."
)
prompt = st.text_area("📝 Prompt", default_prompt, height=180)

# Video uploader
video_file = st.file_uploader("📹 Upload .mp4 video", type=["mp4"])
if not video_file:
    st.stop()
st.video(video_file)

# Save locally
tmp_dir = tempfile.mkdtemp()
local_path = os.path.join(tmp_dir, video_file.name)
with open(local_path, "wb") as f:
    f.write(video_file.read())

# Upload to GCS
st.info(f"Uploading to gs://{BUCKET_NAME}/{UPLOAD_PREFIX}/{video_file.name}")
gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
gcs_uri = f"gs://{BUCKET_NAME}/{gcs_path}"
bucket = storage_client.bucket(BUCKET_NAME)
blob = bucket.blob(gcs_path)
try:
    blob.upload_from_filename(local_path)
    st.success("✅ Uploaded video to GCS.")
except Exception as e:
    st.error(f"Failed to upload video: {e}")
    st.stop()

# Vertex AI predict
st.markdown("### Generating Work Instructions...")
with st.spinner("Calling Vertex AI..."):
    # For Google-published models, use the publisher endpoint resource path
    endpoint = (
        f"projects/{PROJECT_ID}/locations/{REGION}"
        f"/publishers/google/models/{MODEL_ID}:predict"
    )
    # Build the raw gRPC request using PredictionServiceClient
    instances = [{"content": {"uri": gcs_uri}}]
    parameters = {"temperature": 0.2, "maxOutputTokens": 512}
    response = predict_client.predict(
        endpoint=endpoint,
        instances=instances,
        parameters=parameters,
    )
    summary = response.predictions[0].get("content", "")

# Display summary
st.markdown("## ✏️ Work Instruction Draft")
st.code(summary, language="markdown")

# Extract timestamps and frames
st.markdown("## 🖼️ Extracted Frames")
times = sorted({float(t) for t in re.findall(r"(\d+(?:\.\d+)?)s", summary)})
cols = st.columns(min(len(times), 5))
for i, ts in enumerate(times):
    out_png = os.path.join(tmp_dir, f"frame_{ts}s.png")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(ts),
        "-i", local_path,
        "-frames:v", "1",
        "-q:v", "2",
        out_png,
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        cols[i % len(cols)].image(out_png, caption=f"{ts}s")
    except Exception:
        cols[i % len(cols)].write(f"⚠️ Couldn't grab {ts}s frame")

# Export .docx
st.markdown("## 💾 Download .docx")
doc = Document()
doc.add_heading("Work Instruction", level=1)
for block in summary.strip().split("\n\n"):
    lines = block.split("\n")
    doc.add_heading(lines[0], level=2)
    for line in lines[1:]:
        doc.add_paragraph(line)
out_path = os.path.join(tmp_dir, "WI_OUTPUT.docx")
doc.save(out_path)
with open(out_path, "rb") as f:
    st.download_button("⬇️ Download WI_OUTPUT.docx", f, file_name="WI_OUTPUT.docx")

# Cleanup on GCS
try:
    blob.delete()
    st.info("🗑️ Removed temp video from GCS.")
except Exception:
    pass
