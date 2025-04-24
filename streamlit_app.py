import streamlit as st
from PIL import Image
import os
import tempfile

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

# --- Placeholder for Video Preview ---
if video_file:
    st.video(video_file)

    # Save uploaded video temporarily
    temp_dir = tempfile.mkdtemp()
    temp_video_path = os.path.join(temp_dir, video_file.name)
    with open(temp_video_path, "wb") as f:
        f.write(video_file.read())

    # Simulate extracted frames (you can replace with actual logic)
    st.markdown("### Extracted Frames (simulated)")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.image("https://placekitten.com/200/200", caption="frame_5s.png")
    with col2:
        st.image("https://placekitten.com/201/200", caption="frame_10s.png")
    with col3:
        st.image("https://placekitten.com/202/200", caption="frame_30s.png")

    # Simulated result
    st.markdown("### ✏️ Generated Work Instructions")
    summary = """
1. **Step 1:** Fold flaps of cardboard container.
   - Materials: Flat, unfolded cardboard container
   - Observation: Worker folds the cardboard along pre-scored lines.

2. **Step 2:** Place frame into cardboard container.
   - Materials: Framed item, container
   - Observation: Frame has corner protectors.

3. **Step 3:** Place packing slip into container.
   - Materials: Packing slip
   - Observation: Positioned on top of the frame.

4. **Step 4:** Seal container with tape.
   - Materials: Tape dispenser
   - Observation: All seams sealed with one or more strips.

5. **Step 5:** Affix shipping label.
   - Materials: Shipping label
   - Observation: Label attached squarely to the top.
"""
    st.code(summary, language="markdown")
    st.success("Copy and paste the above into your .docx template.")
else:
    st.info("Upload a video to begin.")
