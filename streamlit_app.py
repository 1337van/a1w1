import streamlit as st
import tempfile, os, subprocess, re
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.cloud import storage
from google.cloud import aiplatform

# --- CONFIGURATION ---
PROJECT_ID    = "a1w104232025"
REGION        = "us-central1"
MODEL_ID      = "video-summary"
BUCKET_NAME   = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"

# --- AUTHENTICATION ---
# (Assumes you have a nested [service_account] in your Streamlit secrets)
creds = service_account.Credentials.from_service_account_info(
    st.secrets["service_account"],
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
# Initialize clients
storage_client = storage.Client(credentials=creds, project=PROJECT_ID)
aiplatform.init(
    credentials=creds, project=PROJECT_ID, location=REGION
)

# --- UI SETUP ---
st.set_page_config(page_title="📦 Video-to-WI Generator", layout="wide")
st.title("Video Summarizer → Work Instructions")

st.markdown(
    """
1. **Upload** your packaging video  
2. **Edit or refine** the prompt  
3. **Review** the AI-generated output  
4. **Extract & display** example frames for each detected step  
"""
)

# --- PROMPT EDITOR ---
default_prompt = """
You are a quality control analyst observing a packaging process in a regulated manufacturing environment. Your task is to analyze the video and generate step-by-step work instructions based on what you see visually.

For each step, provide:
- Step number
- Short, clear description of the action
- Any tools, materials, or packaging components seen
- Observations about handling, positioning, sealing, or labeling

If a step is unclear, mark it as [uncertain action].
"""
prompt = st.text_area("📝 Summarization Prompt", default_prompt, height=220)

# --- VIDEO UPLOAD ---
video_file = st.file_uploader("📹 Upload a .mp4 video", type="mp4")
if not video_file:
    st.stop()

st.video(video_file)

# Save locally
tmp_dir = tempfile.mkdtemp()
local_path = os.path.join(tmp_dir, video_file.name)
with open(local_path, "wb") as f:
    f.write(video_file.read())

# --- GCS UPLOAD ---
gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
gcs_uri  = f"gs://{BUCKET_NAME}/{gcs_path}"
st.info(f"Uploading to GCS → `{gcs_uri}`")
bucket = storage_client.bucket(BUCKET_NAME)
blob   = bucket.blob(gcs_path)
blob.upload_from_filename(local_path)
st.success("✅ Uploaded to GCS")

# --- CALL VERTEX AI ---
with st.spinner("📡 Generating summary..."):
    endpoint = (
        f"https://{REGION}-aiplatform.googleapis.com"
        f"/v1/projects/{PROJECT_ID}/locations/{REGION}"
        f"/publishers/google/models/{MODEL_ID}:predict"
    )
    token = creds.token or creds.refresh(Request()) or creds.token
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    payload = {
        "instances": [{"prompt": prompt, "video": {"gcsUri": gcs_uri}}]
    }
    res = requests.post(endpoint, headers=headers, json=payload)
    res.raise_for_status()
    summary = res.json()["predictions"][0]["content"]

# --- DISPLAY & REVIEW ---
st.markdown("## ✏️ Work Instruction Draft")
st.code(summary, language="markdown")

# --- EXTRACT TIMESTAMPS & FRAMES ---
st.markdown("## 🖼️ Extracted Frames for Each Step")

# find all timestamps like "12.3s" or "5s"
times = set(re.findall(r"(\d+(?:\.\d+)?)s", summary))
if not times:
    st.info("No timestamps found in summary; cannot extract frames.")
else:
    cols = st.columns(min(len(times), 5))
    for i, t in enumerate(sorted(times, key=lambda x: float(x))):
        ts = float(t)
        out_png = os.path.join(tmp_dir, f"frame_{t}s.png")
        # using ffmpeg if available
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
            cols[i % len(cols)].image(out_png, caption=f"{t}s")
        except Exception:
            cols[i % len(cols)].warning(f"Couldn’t extract {t}s")

# --- DOWNLOAD OPTION ---
st.markdown("## 💾 Download as .docx")
from docx import Document
doc = Document()
doc.add_heading("Work Instruction", level=1)
for block in summary.strip().split("\n\n"):
    lines = block.split("\n")
    doc.add_paragraph(lines[0], style="Heading 2")
    for line in lines[1:]:
        doc.add_paragraph(line)
docx_path = os.path.join(tmp_dir, "WI_OUTPUT.docx")
doc.save(docx_path)
with open(docx_path, "rb") as f:
    st.download_button("⬇️ Download WI_OUTPUT.docx", f, file_name="WI_OUTPUT.docx")

# --- CLEANUP GCS ---
blob.delete()
st.info("🗑️ Temporary video removed from GCS")
