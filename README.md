# 🎬 URL Video Downloader GUI 🔽

A powerful, feature-rich desktop  URL Video downloader built with `yt-dlp`, `tkinter`, and `ttkbootstrap` for a modern and dynamic user experience.

---

## 🚀 Features

- 🎥 **Video & Audio Downloads** – Grab media in your preferred format and resolution.
- 📜 **Subtitle Support** – Optional subtitle download (manual or auto-generated).
- ⏯️ **Full Download Controls** – Pause, resume, restart, or cancel downloads.
- 🧠 **Smart Queue System** – Add multiple downloads to a queue and process them in bulk.
- 💾 **Persistent Queue** – Keeps your download state saved even after app restarts.
- 📊 **Live Progress View** – Real-time progress, ETA, speed, and file size display.
- 🎨 **Customizable Themes** – Choose from multiple built-in `ttkbootstrap` themes.
- 🖱️ **Responsive UI** – Smooth, scrollable, and adaptable window interface.
- 🛠️ **FFmpeg Integration** – Automatic merging of video/audio using FFmpeg.
- 🔌 **Auto Format Picker** – Smart detection of best available format for audio/video.
- 🧰 **Cross-platform** – Runs on Windows, macOS, and Linux (with Python & dependencies).

---

## 📦 Requirements

- Python 3.8+
- yt-dlp
- ffmpeg
- ttkbootstrap

---

## 🧪 Quick Start

```bash
# Clone the repo
git clone https://github.com/yourusername/youtube-downloader-gui.git
cd youtube-downloader-gui

# Install dependencies
pip install -r requirements.txt

# Run the app
python gui.py
````

---

## 📂 Output

* Downloads are saved in your `~/Videos/Youtube/` folder by default.
* Output filenames are automatically sanitized and deduplicated.
* Subtitles are saved alongside the media (if selected).

---

## 🖼️ Screenshot

> ![image](https://github.com/user-attachments/assets/b00cd8da-196f-41e2-8b58-274f508dc58f)


---

## 🧠 Pro Tips

* Use the **"Download Now"** button for immediate action.
* Use **"Add to Queue"** if you want to queue multiple videos and start them together.
* Choose **audio-only** if you're saving space or just want the MP3 🎧.
---

## 🛑 Known Issues

* Some formats might not be available depending on the video source.
* Without `ffmpeg` in PATH, merging may fail for certain streams.
* Large queues may slow down UI on very low-end machines.

---

## 🤝 Contributions

Pull requests are welcome! Please open an issue first for major changes.
Let’s make this the best YouTube downloader GUI out there! 🙌

---



## 💬 About

Crafted with love using Python 💻 and `yt-dlp` 🧪
UI inspired by modern design using `ttkbootstrap`.

> Made by [Aathishwar](https://github.com/Aathishwar) ✨
