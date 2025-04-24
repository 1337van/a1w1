import streamlit as st
from PIL import Image
import os
import tempfile
import requests
import json
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.cloud import storage
from moviepy.editor import VideoFileClip
from docx import Document
from docx.shared import Inches

# --- CONFIGURATION ---
PROJECT_ID = "a1w104232025"
REGION = "us-central1"
MODEL_ID = "video-summary"
BUCKET_NAME = "a1w1"
UPLOAD_PREFIX = "input/A1W1APP"
SERVICE_ACCOUNT_FILE = "a1w1key.json"  # Path to your downloaded JSON key file

# --- AUTHENTICATION ---
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
request = Request()
credentials.refresh(request)
ACCESS_TOKEN = credentials.token

# --- UI Setup ---
st.set_page_config(page_title="Video Summarizer Work Instruction Tool")
st.title("📦 Video-to-WI Generator")
st.markdown("Upload a packaging video, edit or select a prompt, and generate structured work instructions.")

# --- Upload Video ---
video_file = st.file_uploader("Upload a video file (.mp4)", type=["mp4"])

# --- Prompt Section ---
def_prompt = """
You are a quality control analyst observing a packaging process in a regulated manufacturing environment. Your task is to analyze the video and generate step-by-step work instructions based on what is visually observed.

For each step, provide:
- Step number
- A short, clear description of the action
- Any tools, materials, or packaging components seen
- Observations about handling, positioning, sealing, or labeling

Do not assume audio. Only describe what you can visually confirm. If a step is unclear, mark it as [uncertain action].
"""

prompt = st.text_area("Prompt for Video Summarization", value=def_prompt, height=250)

if video_file:
    st.video(video_file)

    # Save uploaded video locally
    temp_dir = tempfile.mkdtemp()
    temp_video_path = os.path.join(temp_dir, video_file.name)
    with open(temp_video_path, "wb") as f:
        f.write(video_file.read())

    # Upload to GCS
    gcs_path = f"{UPLOAD_PREFIX}/{video_file.name}"
    gcs_uri = f"gs://{BUCKET_NAME}/{gcs_path}"
    st.markdown(f"**Uploading to GCS:** `{gcs_uri}`")
    
    storage_client = storage.Client(credentials=credentials, project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(temp_video_path)
    st.success("✅ Video uploaded to GCS.")

    # --- Frame Extraction ---
    st.markdown("### 🖼 Extracted Frames")
    clip = VideoFileClip(temp_video_path)
    duration = int(clip.duration)
    frames_dir = os.path.join(temp_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    frame_paths = []

    for t in [5, 10, 30, 60, min(85, duration - 1)]:
        frame_path = os.path.join(frames_dir, f"frame_{t}s.png")
        clip.save_frame(frame_path, t)
        frame_paths.append(frame_path)

    cols = st.columns(len(frame_paths))
    for col, path in zip(cols, frame_paths):
        col.image(path, caption=os.path.basename(path))

    # --- Vertex AI API Call ---
st.markdown("### ✏️ Generated Work Instructions")
with st.spinner("Generating summary via Vertex AI..."):
    try:
        endpoint = f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{REGION}/publishers/google/models/{MODEL_ID}:predict"
        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "instances": [
                {
                    "prompt": prompt,
                    "video": {"gcsUri": gcs_uri}
                }
            ]
        }
        response = requests.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        summary = response.json()['predictions'][0]['content']

        st.code(summary, language="markdown")

        # --- .docx Export ---
        st.markdown("### 📄 Export Work Instruction")
        doc = Document()
        doc.add_heading("Work Instruction", 0)

for i, line in enumerate(summary.strip().split("\n\n")):

            parts = line.split("
")
            if len(parts) >= 1:
                doc.add_paragraph(parts[0], style='Heading 2')
                for p in parts[1:]:
                    doc.add_paragraph(p)

        for path in frame_paths:
            doc.add_picture(path, width=Inches(2.0))

        docx_path = os.path.join(temp_dir, "WI_OUTPUT.docx")
        doc.save(docx_path)
        with open(docx_path, "rb") as f:
            st.download_button("Download Work Instruction (.docx)", f, file_name="WI_OUTPUT.docx")

        # --- Auto-delete GCS file ---
        blob.delete()
        st.info("✅ Temporary video file deleted from GCS.")

    except Exception as e:
        st.error(f"Failed to generate summary: {e}")

        except Exception as e:
            st.error(f"Failed to generate summary: {e}")
else:
    st.info("Upload a video to begin.")
