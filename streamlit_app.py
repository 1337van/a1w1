import streamlit as st
import os
import tempfile
import re
import requests
import imageio
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.cloud import storage
from docx import Document
from PIL import Image

# --- CONFIGURATION ---
PROJECT_ID    = "a1w104232025"
REGION        = "us-central1"
MODEL_ID      = "video-summary-bison@001"
BUCKET_NAME   = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"

# --- AUTH via Streamlit Secrets ---
creds = service_account.Credentials.from_service_account_info(
    st.secrets["service_account"],
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
req = Request()
creds.refresh(req)
ACCESS_TOKEN = creds.token

# Initialize GCS
storage_client = storage.Client(credentials=creds, project=PROJECT_ID)

# --- UI ---
st.set_page_config(page_title="📦 Video‑to‑WI Generator", layout="wide")
st.title("Video‑to‑Work Instruction Generator")
st.markdown(
    "Upload a video of your manufacturing process, refine the prompt, generate detailed work instructions with time‑stamps, preview key frames, and export a DOCX file."
)

# Prompt
default_prompt = (
    "You are an operations specialist with a background as a quality control analyst and engineering technician observing a manufacturing process in an ISO 9001:2015 regulated environment.\n"
    "Analyze the video visual and audio to generate step-by-step work instructions.\n\n"
    "For each step:\n"
    "- Prefix with the timestamp in the format [MM:SS].\n"
    "- Include step number, action, tools/materials, and observations.\n"
    "If uncertain, mark [uncertain action]."
)
prompt = st.text_area("📝 Prompt", default_prompt, height=200)

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
gcs_uri  = f"gs://{BUCKET_NAME}/{gcs_path}"
bucket = storage_client.bucket(BUCKET_NAME)
blob   = bucket.blob(gcs_path)
try:
    blob.upload_from_filename(local_path)
    st.success("✅ Video uploaded to GCS.")
except Exception as e:
    st.error(f"Failed to upload video: {e}")
    st.stop()

# Call Vertex AI
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
        "instances": [{"prompt": prompt, "content": {"uri": gcs_uri}}],
        "parameters": {"temperature": 0.0, "maxOutputTokens": 2048}
    }
    res = requests.post(url, headers=headers, json=payload)
    if res.status_code != 200:
        st.error(f"Vertex AI request failed: {res.text}")
        st.stop()
    summary = res.json()["predictions"][0]["content"]

# Display
st.markdown("## ✏️ Work Instruction Draft")
st.code(summary, language="markdown")

# Key frames via imageio
st.markdown("## 🖼️ Key Frame Previews")
times = sorted({float(t) for t in re.findall(r"(\d+(?:\.\d+)?)s", summary)})
if times:
    reader = imageio.get_reader(local_path)
    meta = reader.get_meta_data()
    fps = meta.get('fps', 24)
    cols = st.columns(min(len(times), 5))
    for idx, t in enumerate(times):
        frame_no = int(t * fps)
        frame = reader.get_data(frame_no)
        img = Image.fromarray(frame)
        cols[idx % len(cols)].image(img, caption=f"{int(t//60):02d}:{int(t%60):02d}")
    reader.close()
else:
    st.info("No timestamps found for key frames.")

# Export DOCX
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

# Cleanup
try:
    blob.delete()
    st.info("🗑️ Removed temp video from GCS.")
except:
    pass
