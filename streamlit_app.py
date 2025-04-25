import streamlit as st
import os
import tempfile
import subprocess
import re
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.cloud import storage
from docx import Document

# --- CONFIGURATION ---
PROJECT_ID    = "a1w104232025"
REGION        = "us-central1"
MODEL_ID      = "video-summary-bison@001"
BUCKET_NAME   = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"

# --- AUTHENTICATION via Streamlit Secrets ---
# Define your service account JSON in .streamlit/secrets.toml under [service_account]
creds = service_account.Credentials.from_service_account_info(
    st.secrets["service_account"],
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
# Refresh to obtain an access token for REST calls
request = Request()
creds.refresh(request)
ACCESS_TOKEN = creds.token

# Initialize Google Cloud Storage client
storage_client = storage.Client(credentials=creds, project=PROJECT_ID)

# --- STREAMLIT UI ---
st.set_page_config(page_title="📦 Video‑to‑WI Generator", layout="wide")
st.title("Video‑to‑Work Instruction Generator")
st.markdown(
    "Upload a packaging video, tweak the prompt, review the AI draft, and grab key frames."
)

# Prompt editor
default_prompt = (
    "You are a QC analyst observing a packaging process. "
    "Generate clear, step‑by‑step work instructions based purely on visuals."
)
prompt = st.text_area("📝 Prompt", default_prompt, height=180)

# Video uploader
video_file = st.file_uploader("📹 Upload .mp4 video", type=["mp4"])
if not video_file:
    st.stop()
st.video(video_file)

# Save file locally
tmp_dir = tempfile.mkdtemp()
local_path = os.path.join(tmp_dir, video_file.name)
with open(local_path, "wb") as f:
    f.write(video_file.read())

# Upload to GCS
st.info(f"Uploading to gs://{BUCKET_NAME}/{UPLOAD_PREFIX}/{video_file.name}")
gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
gcs_uri  = f"gs://{BUCKET_NAME}/{gcs_path}"
bucket = storage_client.bucket(BUCKET_NAME)
blob   = bucket.blob(gcs_path)
try:
    blob.upload_from_filename(local_path)
    st.success("✅ Video uploaded to GCS.")
except Exception as e:
    st.error(f"Failed to upload video: {e}")
    st.stop()

# --- Vertex AI REST call ---
st.markdown("### Generating Work Instructions...")
with st.spinner("Calling Vertex AI..."):
    url = (
        f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}"
        f"/locations/{REGION}/publishers/google/models/{MODEL_ID}:predict"
    )
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "instances": [
            {"prompt": prompt, "content": {"uri": gcs_uri}}
        ],
        "parameters": {"temperature": 0.0, "maxOutputTokens": 1024}
    }
    res = requests.post(url, headers=headers, json=payload)
    try:
        res.raise_for_status()
        summary = res.json()["predictions"][0]["content"]
    except Exception as e:
        st.error(f"Vertex AI request failed: {e}\n{res.text}")
        st.stop()

# Display AI draft
st.markdown("## ✏️ Work Instruction Draft")
st.code(summary, language="markdown")

# --- Extract key frames ---
st.markdown("## 🖼️ Extracted Frames")
timestamps = sorted({float(ts) for ts in re.findall(r"(\d+(?:\.\d+)?)s", summary)})
cols = st.columns(min(len(timestamps), 5))
for idx, ts in enumerate(timestamps):
    out_png = os.path.join(tmp_dir, f"frame_{int(ts)}s.png")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(ts),
        "-i", local_path,
        "-frames:v", "1",
        "-q:v", "2",
        out_png
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        cols[idx % len(cols)].image(out_png, caption=f"{ts}s")
    except Exception:
        cols[idx % len(cols)].write(f"⚠️ Couldn't grab {ts}s")

# --- Export to .docx ---
st.markdown("## 💾 Download Work Instruction (.docx)")
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
    st.download_button("⬇️ Download .docx", f, file_name="WI_OUTPUT.docx")

# Cleanup temporary GCS file
try:
    blob.delete()
    st.info("🗑️ Removed temp video from GCS.")
except:
    pass
