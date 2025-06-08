import threading
import time
import queue
import os
import subprocess
import yt_dlp
import tkinter as tk
from tkinter import messagebox, filedialog
import re
import ttkbootstrap as ttk
import json
import logging

QUEUE_FILE = "download_queue.json"

# --- Logging Setup (Simplified) ---
# Configure root logger to output to console
logger = logging.getLogger()
logger.setLevel(logging.INFO) # Set a default logging level

# Create a console handler for all logging
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
console_handler.setFormatter(formatter) # CORRECTED LINE: Set the formatter object
logger.addHandler(console_handler)
# --- End Logging Setup ---


def sanitize_filename(filename):
    safe_filename = "".join(c if c.isalnum() or c in (' ', '.', '_', '-', '(', ')') else '_' for c in filename)
    safe_filename = safe_filename.strip()
    safe_filename = '_'.join(filter(None, safe_filename.split('_')))
    if not safe_filename:
        return "downloaded_file"
    return safe_filename

def generate_unique_filename(base_path):
    if not os.path.exists(base_path):
        return base_path
    name, ext = os.path.splitext(base_path)
    counter = 1
    while True:
        new_path = f"{name}({counter}){ext}"
        if not os.path.exists(new_path):
            return new_path
        counter += 1

class DownloadJob:
    def __init__(self, url, choice, format_info, sub_lang, out_dir, title=None, status="Queued"):
        self.url = url
        self.choice = choice
        self.format_info = format_info
        self.sub_lang = sub_lang
        self.out_dir = out_dir
        self.title = title if title else "Fetching title..."
        self.status = status
        self.progress = 0
        self.eta = "N/A"
        self.current_size = "0 MB"
        self.total_size = "Unknown"
        self.speed = "0 B/s"
        self.thread = None
        self.stop_event = threading.Event()
        self.is_paused = False
        self.tree_item_id = None
        self.last_ui_update_time = 0
        self.temp_files = []
        self.video_downloaded_bytes = 0
        self.audio_downloaded_bytes = 0
        self.video_total_bytes = 0
        self.audio_total_bytes = 0
        self.current_phase = "video"

class YTDownloaderApp(ttk.Window):
    def __init__(self):
        super().__init__() 

        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
            logger.debug("SetProcessDpiAwareness(1) called for DPI awareness.")
        except (AttributeError, OSError):
            logger.debug("DPI awareness setting not applicable or failed (non-Windows OS or shcore not found).")
        except Exception as e:
            logger.error(f"Unexpected error setting DPI awareness: {e}")

        self.title("URL Downloader")
        self.geometry("900x600") 
        self.minsize(600, 400)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.jobs = []
        self.jobs_lock = threading.Lock()
        self.ui_update_interval = 0.5
        self.min_update_interval = 0.5  # ADD THIS
        self.max_update_interval = 2.0  # ADD THIS

        self.ui_queue = queue.Queue()

        self.available_themes = ttk.Style().theme_names()
        self.current_theme_var = tk.StringVar(value=self.style.theme.name)

        self.create_widgets()
        self.load_queue()
        self.after(100, self._check_ui_queue)
        logger.info("Application started.")

    def _setup_scrolling(self):
        """Set up scrolling behavior for Windows after window is fully loaded"""
        def check_scroll_needed():
            self.main_canvas.update_idletasks()
            self.scrollable_frame.update_idletasks()
            
            canvas_width = self.main_canvas.winfo_width()
            canvas_height = self.main_canvas.winfo_height()
            frame_width = self.scrollable_frame.winfo_reqwidth()
            frame_height = self.scrollable_frame.winfo_reqheight()
            
            if self.state() == 'zoomed':
                # Full-screen mode: no scrolling, adjust to window size
                self.v_scrollbar.grid_forget()
                self.h_scrollbar.grid_forget()
                self.main_canvas.itemconfig(self.canvas_window, width=canvas_width, height=canvas_height)
                self.main_canvas.configure(scrollregion=(0, 0, canvas_width, canvas_height))
            else:
                if frame_height > canvas_height:
                    self.v_scrollbar.grid(row=0, column=1, sticky='ns')
                else:
                    self.v_scrollbar.grid_forget()

                if frame_width > canvas_width:
                    self.h_scrollbar.grid(row=1, column=0, sticky='ew')
                else:
                    self.h_scrollbar.grid_forget()

                self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

        
        self.main_canvas.bind('<Configure>', lambda e: check_scroll_needed())
        self.scrollable_frame.bind('<Configure>', lambda e: check_scroll_needed())
        self.bind('<Configure>', lambda e: check_scroll_needed())
        scroll_speed = 5  # Adjust for smoother/faster scrolling on Windows

        def _on_mousewheel(event):
            if self.main_canvas.winfo_ismapped():
                delta = int(-1*(event.delta/120)*scroll_speed)
                if event.state & 0x1:  # Shift key pressed for horizontal scrolling
                    self.main_canvas.xview_scroll(delta, "units")
                else:  # Vertical scrolling
                    self.main_canvas.yview_scroll(delta, "units")
            return "break"

        self.main_canvas.bind("<MouseWheel>", _on_mousewheel) # Windows/macOS
        self.scrollable_frame.bind("<MouseWheel>", _on_mousewheel) # Windows/macOS

    
        def bind_mousewheel_to_children(widget):
            widget.bind("<MouseWheel>", _on_mousewheel) 
            for child in widget.winfo_children():
                bind_mousewheel_to_children(child)
        
        self.after(200, lambda: bind_mousewheel_to_children(self.scrollable_frame))

        def _on_key_scroll(event):
            if event.keysym == "Up":
                self.main_canvas.yview_scroll(-1, "units")
            elif event.keysym == "Down":
                self.main_canvas.yview_scroll(1, "units")
            elif event.keysym == "Left":
                self.main_canvas.xview_scroll(-1, "units")
            elif event.keysym == "Right":
                self.main_canvas.xview_scroll(1, "units")
            elif event.keysym == "Prior":  # Page Up
                self.main_canvas.yview_scroll(-5, "units")
            elif event.keysym == "Next":  # Page Down
                self.main_canvas.yview_scroll(5, "units")
            elif event.keysym == "Home":
                if event.state & 0x4:  # Ctrl+Home
                    self.main_canvas.yview_moveto(0)
                    self.main_canvas.xview_moveto(0)
                else:  # Home only
                    self.main_canvas.xview_moveto(0)
            elif event.keysym == "End":
                if event.state & 0x4:  # Ctrl+End
                    self.main_canvas.yview_moveto(1)
                    self.main_canvas.xview_moveto(1)
                else:  # End only
                    self.main_canvas.xview_moveto(1)
        
        self.bind_all("<Up>", _on_key_scroll)
        self.bind_all("<Down>", _on_key_scroll)
        self.bind_all("<Left>", _on_key_scroll)
        self.bind_all("<Right>", _on_key_scroll)
        self.bind_all("<Prior>", _on_key_scroll)
        self.bind_all("<Next>", _on_key_scroll)
        self.bind_all("<Home>", _on_key_scroll)
        self.bind_all("<End>", _on_key_scroll)
        
        # Make canvas focusable for keyboard navigation
        self.main_canvas.bind("<Button-1>", lambda e: self.main_canvas.focus_set())

    def create_widgets(self):
        # Create menu
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        theme_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Themes", menu=theme_menu)
        for theme_name in self.available_themes:
            theme_menu.add_radiobutton(label=theme_name, variable=self.current_theme_var, command=self.change_theme)

        # Create main container with grid layout for better scrollbar management
        main_container = ttk.Frame(self)
        main_container.pack(fill="both", expand=True)
        main_container.grid_rowconfigure(0, weight=1)
        main_container.grid_columnconfigure(0, weight=1)

        # Create canvas
        self.main_canvas = tk.Canvas(main_container, highlightthickness=0)
        self.main_canvas.grid(row=0, column=0, sticky='nsew')
        
        # Create scrollbars
        self.v_scrollbar = ttk.Scrollbar(main_container, orient="vertical", command=self.main_canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(main_container, orient="horizontal", command=self.main_canvas.xview)
        
        # Create scrollable frame
        self.scrollable_frame = ttk.Frame(self.main_canvas)

        # Configure canvas window
        self.canvas_window = self.main_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        self.main_canvas.configure(
            yscrollcommand=self.v_scrollbar.set,
            xscrollcommand=self.h_scrollbar.set
        )
        
        def configure_scroll_region(event=None):
            self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))
            
            canvas_width = self.main_canvas.winfo_width()
            frame_width = self.scrollable_frame.winfo_reqwidth()
            if frame_width < canvas_width:
                self.main_canvas.itemconfig(self.canvas_window, width=canvas_width)
        
        self.scrollable_frame.bind("<Configure>", configure_scroll_region)

        # Create all widgets inside scrollable_frame with minimum width constraint
        content_frame = ttk.Frame(self.scrollable_frame)
        content_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Main input frame
        frame = ttk.Frame(content_frame, padding="10")
        frame.pack(fill='x')
        frame.grid_columnconfigure(1, weight=1) 

        ttk.Label(frame, text="YouTube URL:").grid(row=0, column=0, sticky='w', pady=(0,2))
        self.url_entry = ttk.Entry(frame, width=70)
        self.url_entry.grid(row=0, column=1, sticky='ew', pady=(0,5))

        ttk.Button(frame, text="Fetch Info", command=self.fetch_info).grid(row=0, column=2, padx=5, pady=(0,5))

        ttk.Label(frame, text="Title:").grid(row=1, column=0, sticky='w')
        self.title_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.title_var, wraplength=500).grid(row=1, column=1, columnspan=2, sticky='w')

        self.choice_var = tk.StringVar(value="video")
        ttk.Radiobutton(frame, text="Video", variable=self.choice_var, value="video", command=self.update_format_list).grid(row=2, column=0, sticky='w', pady=(5,0))
        ttk.Radiobutton(frame, text="Audio", variable=self.choice_var, value="audio", command=self.update_format_list).grid(row=2, column=1, sticky='w', pady=(5,0))

        ttk.Label(frame, text="Available formats:").grid(row=3, column=0, sticky='w', pady=(5,2))
        format_list_frame = ttk.Frame(frame)
        format_list_frame.grid(row=4, column=0, columnspan=3, sticky='ew')

        self.format_listbox = tk.Listbox(format_list_frame, height=10, width=70, font=('Segoe UI', 9))
        self.format_listbox.pack(side=tk.LEFT, fill='both', expand=True)

        list_scrollbar = ttk.Scrollbar(format_list_frame, orient="vertical", command=self.format_listbox.yview)
        list_scrollbar.pack(side=tk.RIGHT, fill="y")
        self.format_listbox.config(yscrollcommand=list_scrollbar.set)
        self.format_listbox.bind('<<ListboxSelect>>', self.on_format_select)

        ttk.Label(frame, text="Subtitles:").grid(row=5, column=0, sticky='w', pady=(5,2))
        self.sub_lang_var = tk.StringVar()
        self.sub_lang_combo = ttk.Combobox(frame, textvariable=self.sub_lang_var, state='readonly')
        self.sub_lang_combo.grid(row=5, column=1, sticky='w', pady=(5,0))
        self.sub_lang_combo['values'] = ["None"]
        self.sub_lang_combo.current(0)

        ttk.Label(frame, text="Output Folder:").grid(row=6, column=0, sticky='w', pady=(5,2))
        default_output_dir = os.path.join(os.path.expanduser("~"), "Videos", "Youtube")
        os.makedirs(default_output_dir, exist_ok=True)
        self.out_dir_var = tk.StringVar(value=default_output_dir)
        out_dir_entry = ttk.Entry(frame, textvariable=self.out_dir_var, width=55)
        out_dir_entry.grid(row=6, column=1, sticky='ew', pady=(5,0))
        ttk.Button(frame, text="Browse", command=self.browse_output_dir).grid(row=6, column=2, padx=5, pady=(5,0))

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=7, column=0, columnspan=3, sticky='ew', pady=5)

        self.status_var = tk.StringVar(value="Idle")
        self.status_label = ttk.Label(frame, textvariable=self.status_var, font=('Segoe UI', 10, 'bold'), bootstyle="info")
        self.status_label.grid(row=8, column=0, columnspan=3, sticky='w')

        # Action buttons frame
        action_btn_frame = ttk.Frame(content_frame)
        action_btn_frame.pack(pady=5, anchor='center')

        self.add_job_btn = ttk.Button(action_btn_frame, text="Add to Queue", command=self.add_job, state='disabled', bootstyle="primary")
        self.add_job_btn.grid(row=0, column=0, padx=5)

        self.download_now_btn = ttk.Button(action_btn_frame, text="Download Now", command=self.download_now, state='disabled', bootstyle="success")
        self.download_now_btn.grid(row=0, column=1, padx=5)

        self.start_all_btn = ttk.Button(action_btn_frame, text="Start All Downloads", command=self.start_all_downloads, bootstyle="info")
        self.start_all_btn.grid(row=0, column=2, padx=5)
        
        self.pause_btn = ttk.Button(action_btn_frame, text="Pause", command=self.pause_resume_job, state='disabled', bootstyle="warning")
        self.pause_btn.grid(row=1, column=0, padx=5, pady=5)

        self.cancel_btn = ttk.Button(action_btn_frame, text="Cancel", command=self.cancel_job, state='disabled', bootstyle="danger")
        self.cancel_btn.grid(row=1, column=1, padx=5, pady=5)

        self.restart_btn = ttk.Button(action_btn_frame, text="Restart", command=self.restart_job, state='disabled', bootstyle="info")
        self.restart_btn.grid(row=1, column=2, padx=5, pady=5)

        self.clear_btn = ttk.Button(action_btn_frame, text="Clear Queue", command=self.clear_queue, bootstyle="secondary")
        self.clear_btn.grid(row=1, column=3, padx=5, pady=5)
        
        self.clear_finished_errored_btn = ttk.Button(action_btn_frame, text="Clear Finished/Errored", command=self.clear_finished_or_errored_jobs, bootstyle="secondary")
        self.clear_finished_errored_btn.grid(row=0, column=3, padx=5)

        # Download queue label
        ttk.Label(content_frame, text="Download Queue:").pack(anchor='w', pady=(10,2))
        
        # Create a frame for the treeview that will expand
        tree_frame = ttk.Frame(content_frame)
        tree_frame.pack(fill='both', expand=True)
        
        self.jobs_tree = ttk.Treeview(tree_frame, columns=("title", "status", "progress", "eta", "size", "speed"), show='headings', height=8)
        self.jobs_tree.heading("title", text="Title")
        self.jobs_tree.heading("status", text="Status")
        self.jobs_tree.heading("progress", text="Progress")
        self.jobs_tree.heading("eta", text="ETA")
        self.jobs_tree.heading("size", text="Size")
        self.jobs_tree.heading("speed", text="Speed")
        self.jobs_tree.column("title", width=300, anchor='w')
        self.jobs_tree.column("status", width=80, anchor='center')
        self.jobs_tree.column("progress", width=80, anchor='center')
        self.jobs_tree.column("eta", width=70, anchor='center')
        self.jobs_tree.column("size", width=100, anchor='center')
        self.jobs_tree.column("speed", width=80, anchor='center')
        self.jobs_tree.pack(fill='both', expand=True, side=tk.LEFT)
        
        # Treeview scrollbar
        tree_scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.jobs_tree.yview)
        tree_scrollbar.pack(side=tk.RIGHT, fill="y")
        self.jobs_tree.config(yscrollcommand=tree_scrollbar.set)
        
        self.jobs_tree.bind('<<TreeviewSelect>>', self.on_job_select)

        # About button at the bottom
        about_btn_frame = ttk.Frame(content_frame)
        about_btn_frame.pack(side=tk.BOTTOM, anchor=tk.E, pady=(10,0))

        self.about_btn = ttk.Button(about_btn_frame, text="About", command=self.show_about, bootstyle="light")
        self.about_btn.pack(side=tk.RIGHT)

        # Configure column weights for responsiveness
        frame.columnconfigure(1, weight=1)

        # Initialize variables
        self.info = None
        self.candidates = []
        self.after(100, self._setup_scrolling)

    def change_theme(self):
        selected_theme = self.current_theme_var.get()
        self.style.theme_use(selected_theme)
            # No log text widget to update

    def _check_ui_queue(self):
        processed_count = 0
        max_process_per_cycle = 10
        
        # Create a job lookup dict once per cycle to reduce repeated lookups
        with self.jobs_lock:
            job_lookup = {j.tree_item_id: j for j in self.jobs}
        
        while processed_count < max_process_per_cycle:
            try:
                job_id, message_type, *args = self.ui_queue.get_nowait()
                processed_count += 1

                try:
                    job = job_lookup.get(job_id)

                    if job:
                        if message_type == 'progress':
                            self.update_job_list_item_ui(job)
                            selected_items = self.jobs_tree.selection()
                            if selected_items and selected_items[0] == job.tree_item_id:
                                self.progress_var.set(job.progress)
                                self.status_var.set(f"{job.status} {job.title}: {job.progress:.1f}% ({self._strip_ansi_codes(job.speed)}, ETA: {self._strip_ansi_codes(job.eta)})")

                        elif message_type == 'status_update':
                            job.status = args[0]
                            self.update_job_list_item_ui(job)
                            selected_items = self.jobs_tree.selection()
                            if selected_items and selected_items[0] == job.tree_item_id:
                                status_text = args[1] if len(args) > 1 else f"{job.status} {job.title}"
                                self.status_var.set(status_text)
                                self.progress_var.set(job.progress)
                            elif not any(j.status in ("Downloading", "Processing", "Pausing...") for j in job_lookup.values()):
                                status_text = args[1] if len(args) > 1 else f"{job.status} {job.title}"
                                self.status_var.set(status_text)
                                self.progress_var.set(0)

                        elif message_type == 'select_and_update_status':
                            job.status = args[0]
                            self.update_job_list_item_ui(job)
                            self.jobs_tree.selection_set(job.tree_item_id)
                            self.jobs_tree.focus(job.tree_item_id)
                            self.jobs_tree.see(job.tree_item_id)
                            status_text = args[1] if len(args) > 1 else f"{job.status} {job.title}"
                            self.status_var.set(status_text)
                            self.progress_var.set(job.progress)
                            self.on_job_select(None)

                        elif message_type == 'error':
                            job.status = "Error"
                            self.update_job_list_item_ui(job)
                            logger.error(args[0])
                            messagebox.showerror("Download Error", args[0])
                            selected_items = self.jobs_tree.selection()
                            if selected_items and selected_items[0] == job.tree_item_id:
                                self.status_var.set(f"Error: {job.title}")
                                self.progress_var.set(job.progress)
                            self.on_job_select(None)
                        elif message_type == 'warning':
                            logger.warning(args[0])
                            messagebox.showwarning("Warning", args[0])
                    elif message_type == 'status_update' and job_id is None:
                        self.status_var.set(args[1])
                        if not any(j.status in ("Downloading", "Processing") for j in job_lookup.values()):
                            self.progress_var.set(0)
                finally:
                    self.ui_queue.task_done()
            except queue.Empty:
                break
            except Exception as e:
                logger.exception("Error processing UI queue message (top level).")
        
        # Dynamic UI check interval based on queue size
        queue_size = self.ui_queue.qsize() if hasattr(self.ui_queue, 'qsize') else 0
        if queue_size > 50:
            next_check = 50  # Check more frequently if queue is building up
        elif queue_size > 20:
            next_check = 75
        else:
            next_check = 100  # Normal interval
        
        if queue_size > 20:
            logger.debug(f"UI queue has {queue_size} pending messages")
        
        self.after(next_check, self._check_ui_queue)

    def browse_output_dir(self):
        folder = filedialog.askdirectory(initialdir=self.out_dir_var.get())
        if folder:
            self.out_dir_var.set(folder)
            logger.info(f"Output directory set to: {folder}")


    def fetch_info(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Input error", "Please enter a YouTube URL.")
            logger.warning("Attempted to fetch info with empty URL.")
            return

        self.status_var.set("Fetching video info... This might take a moment.")
        self.update()
        logger.info(f"Fetching info for URL: {url}")

        self.info = None
        self.title_var.set("")
        self.format_listbox.delete(0, 'end')
        self.candidates.clear()
        self.sub_lang_combo['values'] = ["None"]
        self.sub_lang_combo.current(0)
        self.add_job_btn['state'] = 'disabled'
        self.download_now_btn['state'] = 'disabled'

        
        def _on_treeview_mousewheel(event):
         self.jobs_tree.yview_scroll(int(-1*(event.delta/120)), "units")
         return "break"

        self.jobs_tree.bind("<MouseWheel>", _on_treeview_mousewheel)

        def _on_listbox_mousewheel(event):
         self.format_listbox.yview_scroll(int(-1*(event.delta/120)), "units")
         return "break"

        self.format_listbox.bind("<MouseWheel>", _on_listbox_mousewheel)

       

        def worker():
            try:
                ydl_opts = {
                    'quiet': True,
                    'skip_download': True,
                    'extract_flat': True,
                    'no_warnings': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    self.info = ydl.extract_info(url, download=False)
                self.after(0, self._update_info_ui)
            except yt_dlp.utils.DownloadError as e:
                error_msg = f"Failed to fetch video info: {e}"
                if "unavailable" in str(e).lower() or "private" in str(e).lower():
                    error_msg = "Error: Video is unavailable or private or geographical restrictions."
                elif "no appropriate" in str(e).lower() or "unsupported URL" in str(e).lower():
                    error_msg = "Error: No downloadable content or unsupported URL found for this link."
                self.after(0, lambda: messagebox.showerror("Error", error_msg))
                self.after(0, lambda: self.status_var.set("Idle"))
                self.after(0, lambda: self.add_job_btn.config(state='disabled'))
                self.after(0, lambda: self.download_now_btn.config(state='disabled'))
                logger.error(f"yt-dlp DownloadError: {error_msg}")
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", f"An unexpected error occurred:\n{e}"))
                self.after(0, lambda: self.status_var.set("Idle"))
                self.after(0, lambda: self.add_job_btn.config(state='disabled'))
                self.after(0, lambda: self.download_now_btn.config(state='disabled'))
                logger.exception("An unexpected error occurred during fetch_info.")

        threading.Thread(target=worker, daemon=True).start()

    def _update_info_ui(self):
        if not self.info:
            self.status_var.set("Info fetch failed.")
            logger.warning("Info data is empty after fetch attempt.")
            return

        self.title_var.set(self.info.get('title', 'Unknown Title'))
        self.update_format_list()

        subtitles = self.info.get('subtitles') or {}
        automatic_captions = self.info.get('automatic_captions') or {}
        
        available_subs = set(k for k,v in subtitles.items() if v)
        available_auto = set(k for k,v in automatic_captions.items() if v)
        langs = sorted(list(available_subs | available_auto))

        langs_display = ["None"] + langs
        self.sub_lang_combo['values'] = langs_display
        self.sub_lang_combo.current(0)

        self.add_job_btn['state'] = 'normal'
        self.download_now_btn['state'] = 'normal'
        self.status_var.set("Info fetched successfully. Select a format and add to queue or download.")
        logger.info(f"Info fetched successfully for '{self.info.get('title', 'Unknown Title')}'")

    def update_format_list(self, *_):
        choice = self.choice_var.get()
        if not self.info:
            return

        formats = self.info.get('formats', [])
        self.format_listbox.delete(0, 'end')
        self.candidates.clear()

        idx = 1
        if choice == 'video':
            combined_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
            video_only_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
            # video_only_formats.sort(key=lambda x: (x.get('height', 0), x.get('filesize', 0) or x.get('filesize_approx', 0)), reverse=True)

            # Add combined formats
            for f in combined_formats:
                size_bytes = f.get('filesize') or f.get('filesize_approx') or 0
                size_mb_str = f"{size_bytes / 1024 / 1024:.2f} MB" if size_bytes else "Unknown"
                res = f"{f.get('width', '?')}x{f.get('height', '?')}"
                ext = f.get('ext', 'N/A')
                fps = f.get('fps', '?')
                abr = f.get('abr', '?') # Audio Bitrate
                display_text = f"{idx}. {res} ({ext}) | {size_mb_str} | {fps}fps | Audio: {abr}k (Combined)"
                self.format_listbox.insert('end', display_text)
                self.candidates.append(f)
                idx += 1

            # Add video-only formats
            for f in video_only_formats:
                size_bytes = f.get('filesize') or f.get('filesize_approx') or 0
                size_mb_str = f"{size_bytes / 1024 / 1024:.2f} MB" if size_bytes else "Unknown"
                res = f"{f.get('width', '?')}x{f.get('height', '?')}"
                ext = f.get('ext', 'N/A')
                fps = f.get('fps', '?')
                display_text = f"{idx}. {res} ({ext}) | {size_mb_str} | {fps}fps (Video Only)"
                self.format_listbox.insert('end', display_text)
                self.candidates.append(f)
                idx += 1
            
            if not combined_formats and not video_only_formats:
                self.format_listbox.insert('end', "No video formats found. Try Audio option.")
                self.candidates.append({"dummy": True})
                logger.info("No video formats found for the current video.")

        elif choice == 'audio':
            audio_formats = [f for f in formats if f.get('acodec') != 'none']
            audio_formats.sort(key=lambda x: (x.get('abr', 0), x.get('filesize', 0) or x.get('filesize_approx', 0)), reverse=True)

            # Add a general "Best Audio" option first
            display_text = "1. Best Audio (MP3 320kbps) - recommended"
            self.format_listbox.insert('end', display_text)
            self.candidates.append({"format_id": "bestaudio/best_dummy_format_id", "is_best_audio_option": True})
            idx += 1

            # Add other available audio formats
            for f in audio_formats:
                size_bytes = f.get('filesize') or f.get('filesize_approx') or 0
                size_mb_str = f"{size_bytes / 1024 / 1024:.2f} MB" if size_bytes else "Unknown"
                ext = f.get('ext', 'N/A')
                abr = f.get('abr', '?') # Audio Bitrate
                display_text = f"{idx}. {ext} | {size_mb_str} | {abr}kbps"
                self.format_listbox.insert('end', display_text)
                self.candidates.append(f)
                idx += 1

            if not audio_formats and not any(c.get("is_best_audio_option") for c in self.candidates):
                self.format_listbox.insert('end', "No audio formats found.")
                self.candidates.append({"dummy": True})
                logger.info("No audio formats found for the current video.")
        
        self.on_format_select(None)
        if self.candidates and not self.candidates[0].get("dummy"):
            self.format_listbox.selection_set(0)
            self.on_format_select(None) # Manually trigger selection update

    def on_format_select(self, event):
        if self.format_listbox.curselection() and self.info:
            selected_index = self.format_listbox.curselection()[0]
            if self.candidates[selected_index].get("dummy"):
                self.add_job_btn['state'] = 'disabled'
                self.download_now_btn['state'] = 'disabled'
                logger.debug("Dummy format selected, disabling download buttons.")
                return

            self.add_job_btn['state'] = 'normal'
            self.download_now_btn['state'] = 'normal'
            logger.debug(f"Format selected: {self.candidates[selected_index].get('format_id')}")
        else:
            self.add_job_btn['state'] = 'disabled'
            self.download_now_btn['state'] = 'disabled'
            logger.debug("No format selected or info missing, disabling download buttons.")

    def _create_and_start_job(self, start_immediately=False):
        if not self.info:
            messagebox.showwarning("No video info", "Please fetch video info first.")
            logger.warning("Attempted to add job without video info.")
            return

        if not self.format_listbox.curselection():
            messagebox.showwarning("No format", "Please select a format.")
            logger.warning("Attempted to add job without format selection.")
            return

        selected_index = self.format_listbox.curselection()[0]
        if self.candidates[selected_index].get("dummy"):
            messagebox.showwarning("Invalid Selection", "Please select an actual format.")
            logger.warning("Attempted to add job with dummy format selection.")
            return

        format_info = self.candidates[selected_index]

        url = self.url_entry.get().strip()
        choice = self.choice_var.get()
        sub_lang = self.sub_lang_var.get()
        out_dir = self.out_dir_var.get()

        job = DownloadJob(url, choice, format_info, sub_lang, out_dir, title=self.title_var.get())
        
        with self.jobs_lock:
            self.jobs.append(job)
            job.tree_item_id = self.jobs_tree.insert('', 'end',
                                                    values=(job.title, job.status, f"{job.progress:.1f}%"))
            self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Added to queue: {job.title}"))
        logger.info(f"Job '{job.title}' added to queue. Choice: {job.choice}, Format: {job.format_info.get('format_id', 'N/A')}")

        if start_immediately:
            self.start_download_job(job, select_in_ui=True)
            self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Starting download immediately: {job.title}"))
            logger.info(f"Job '{job.title}' started immediately.")
        
        self.save_queue()

    def add_job(self):
        self._create_and_start_job(start_immediately=False)

    def download_now(self):
        self._create_and_start_job(start_immediately=True)

    def _strip_ansi_codes(self, text):
        return re.sub(r'\x1b\[[0-9;]*m', '', text)
    
    def update_job_list_item_ui(self, job: DownloadJob):
        current_time = time.time()
        if hasattr(job, '_last_ui_list_update') and current_time - job._last_ui_list_update < 0.1:
            return
        job._last_ui_list_update = current_time
        
        if job.tree_item_id:
            display_eta = self._strip_ansi_codes(job.eta)
            display_speed = self._strip_ansi_codes(job.speed)
            self.jobs_tree.item(job.tree_item_id, values=(job.title, job.status, f"{job.progress:.1f}%", display_eta, f"{job.current_size}/{job.total_size}", display_speed))

        with self.jobs_lock:
            selected_items = self.jobs_tree.selection()
            currently_selected_job = next((j for j in self.jobs if j.tree_item_id == selected_items[0]), None) if selected_items else None

            if currently_selected_job and currently_selected_job.tree_item_id == job.tree_item_id:
                self.progress_var.set(job.progress)
                if job.status == "Downloading":
                    status_text = f"Downloading: {job.title} - {job.progress:.1f}% ({display_speed}, ETA: {display_eta})"
                elif job.status == "Processing":
                    status_text = f"Processing: {job.title} - {job.progress:.1f}% (Merging...)"
                elif job.status == "Pausing...":
                    status_text = f"Pausing: {job.title}"
                elif job.status == "Paused":
                    status_text = f"Paused: {job.title}"
                elif job.status == "Completed":
                    status_text = f"Completed: {job.title}"
                elif job.status == "Error":
                    status_text = f"Error: {job.title}"
                else:
                    status_text = f"{job.status} {job.title}: {job.progress:.1f}%"
                self.status_var.set(status_text)
            elif not any(j.status in ("Downloading", "Processing", "Pausing...") for j in self.jobs):
                self.progress_var.set(0)
                if not currently_selected_job:
                    self.status_var.set("Idle")

    def _start_all_downloads_worker(self):
        with self.jobs_lock:
            jobs_to_start = [job for job in self.jobs if job.status in ("Queued", "Paused", "Canceled", "Error")]
        
        if not jobs_to_start:
            self.ui_queue.put((None, 'status_update', "No downloads to start.", "No downloads to start."))
            logger.info("No queued, paused, canceled, or error jobs to start.")
            return

        logger.info(f"About to start {len(jobs_to_start)} jobs")
        
        for job in jobs_to_start:
            if not (job.thread and job.thread.is_alive()):
                self.start_download_job(job, select_in_ui=False)
                time.sleep(0.1)
        
        with self.jobs_lock:
            active_count = sum(1 for job in self.jobs if job.status in ("Downloading", "Processing"))
        
        if active_count > 0:
            self.ui_queue.put((None, 'status_update', "Started all pending downloads.", f"Started {len(jobs_to_start)} downloads."))
            logger.info(f"Started {len(jobs_to_start)} pending downloads.")
        else:
            self.ui_queue.put((None, 'status_update', "No downloads to start.", "No downloads to start."))

    def start_all_downloads(self):
        threading.Thread(target=self._start_all_downloads_worker, daemon=True).start()

    def start_download_job(self, job: DownloadJob, select_in_ui=True):
        if job.thread and job.thread.is_alive():
            logger.warning(f"Attempted to start job '{job.title}' which is already active.")
            return

        job.stop_event.clear()
        job.is_paused = False
        job.status = "Downloading"
        job.progress = 0
        job.eta = "N/A"
        job.current_size = "0 MB"
        job.total_size = "Unknown"
        job.speed = "0 B/s"
        job.last_ui_update_time = time.time()
        job.temp_files = []
        job.video_downloaded_bytes = 0
        job.audio_downloaded_bytes = 0
        job.video_total_bytes = 0
        job.audio_total_bytes = 0
        job.current_phase = "video"

        if select_in_ui:
            self.ui_queue.put((job.tree_item_id, 'select_and_update_status', job.status, f"Starting download: {job.title}"))
        else:
            self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Starting download: {job.title}"))
        
        logger.info(f"Initiating download for '{job.title}'.")
        
        job.thread = threading.Thread(target=self.download_worker, args=(job,))
        job.thread.daemon = True
        job.thread.start()
        self.save_queue()

    def download_worker(self, job: DownloadJob):
        try:
            os.makedirs(job.out_dir, exist_ok=True)
            base_outtmpl_no_ext = os.path.join(job.out_dir, sanitize_filename(job.title))
            logger.info(f"Download worker started for '{job.title}'. Output directory: {job.out_dir}")

            if job.stop_event.is_set():
                job.status = "Paused"
                self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Download {job.title} paused before start."))
                logger.info(f"Download for '{job.title}' paused before start by user.")
                self.save_queue()
                return

            ydl_opts = {
                'outtmpl': f"{base_outtmpl_no_ext}.%(ext)s",
                'noplaylist': True,
                'progress_hooks': [lambda d: self.ytdl_hook(d, job)],
                'quiet': True,
                'no_warnings': True,
                'retries': 5,
                'fragment_retries': 5,
            }

            if job.choice == "video":
                is_combined_format = job.format_info.get('vcodec') != 'none' and job.format_info.get('acodec') != 'none'
                is_video_only = job.format_info.get('vcodec') != 'none' and job.format_info.get('acodec') == 'none'

                if is_video_only:
                    # Original logic for YouTube separate streams
                    job.current_phase = "video"
                    video_outtmpl_part = f"{base_outtmpl_no_ext}_video.%(ext)s"
                    ydl_opts_video = {**ydl_opts, 'format': job.format_info['format_id'], 'outtmpl': video_outtmpl_part}
                    
                    self.ui_queue.put((job.tree_item_id, 'status_update', "Downloading", f"Downloading video stream for: {job.title}"))
                    logger.info(f"Starting video stream download for '{job.title}' (Format ID: {job.format_info['format_id']}).")
                    with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                        info_dict_video = ydl.extract_info(job.url, download=True)
                        video_file_path = ydl.prepare_filename(info_dict_video)
                        job.temp_files.append(video_file_path)
                    logger.info(f"Video stream for '{job.title}' downloaded to: {video_file_path}")

                    if job.stop_event.is_set():
                        job.status = "Paused"
                        self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Download {job.title} paused during video download."))
                        logger.info(f"Download for '{job.title}' paused during video download.")
                        self.save_queue()
                        return

                    job.current_phase = "audio"
                    audio_outtmpl_part = f"{base_outtmpl_no_ext}_audio.%(ext)s"
                    ydl_opts_audio = {
                        **ydl_opts,
                        'format': "bestaudio/best",
                        'outtmpl': audio_outtmpl_part,
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'aac',
                            'preferredquality': '320',
                        }],
                    }
                    
                    self.ui_queue.put((job.tree_item_id, 'status_update', "Downloading", f"Downloading audio stream for: {job.title}"))
                    logger.info(f"Starting audio stream download for '{job.title}'.")
                    with yt_dlp.YoutubeDL(ydl_opts_audio) as ydl:
                        info_dict_audio = ydl.extract_info(job.url, download=True)
                        audio_file_path = info_dict_audio.get('filepath')
                        if not audio_file_path:
                            for f in os.listdir(job.out_dir):
                                    if f.startswith(os.path.basename(base_outtmpl_no_ext) + "_audio.") and f.endswith(('.aac', '.m4a', '.mp3')):
                                        audio_file_path = os.path.join(job.out_dir, f)
                                        break
                        
                        if not audio_file_path or not os.path.exists(audio_file_path):
                            raise Exception("Failed to determine downloaded audio file path for explicit merge.")
                        job.temp_files.append(audio_file_path)
                    logger.info(f"Audio stream for '{job.title}' downloaded to: {audio_file_path}")


                    if job.stop_event.is_set():
                        job.status = "Paused"
                        self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Download {job.title} paused during audio download."))
                        logger.info(f"Download for '{job.title}' paused during audio download.")
                        self.save_queue()
                        return

                    final_mp4_path = generate_unique_filename(os.path.join(job.out_dir, f"{sanitize_filename(job.title)}.mp4"))
                    self.ui_queue.put((job.tree_item_id, 'status_update', "Processing", f"Merging video and audio for: {job.title}"))
                    logger.info(f"Starting merge process for '{job.title}' (Video: {video_file_path}, Audio: {audio_file_path}) to {final_mp4_path}.")

                    ffmpeg_cmd = [
                        "ffmpeg",
                        "-i", video_file_path,
                        "-i", audio_file_path,
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-b:a", "320k",
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-y",
                        final_mp4_path
                    ]

                    try:
                        subprocess.run(["ffmpeg", "-version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        logger.error("FFmpeg not found or not in PATH. Required for merging/conversion.")
                        raise Exception("FFmpeg is not installed or not in your system's PATH. It's required for video/audio merging and conversion.")

                    process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                    
                    job.status = "Processing"
                    job.progress = 99.9
                    job.eta = "Processing..."
                    job.speed = "N/A"
                    
                    while True:
                        if job.stop_event.is_set():
                            process.terminate()
                            job.status = "Paused"
                            self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Download {job.title} paused during merging."))
                            logger.info(f"Merge for '{job.title}' paused by user.")
                            break

                        line = process.stdout.readline()
                        if not line:
                            break
                        logger.debug(f"FFmpeg output for '{job.title}': {line.strip()}")
                
                        if time.time() - job.last_ui_update_time > self.ui_update_interval:
                            if job.progress < 100:
                                job.progress = min(job.progress + 0.01, 99.9)
                            self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Processing {job.title}: {job.progress:.1f}%"))
                            self.ui_queue.put((job.tree_item_id, 'progress'))
                            job.last_ui_update_time = time.time()
                    
                    process.wait()
                    if process.returncode != 0 and not job.stop_event.is_set():
                        logger.error(f"FFmpeg merging for '{job.title}' failed with exit code {process.returncode}. Output: {process.communicate()[0]}")
                        raise Exception(f"FFmpeg merging failed with exit code {process.returncode}")

                    for temp_f in job.temp_files:
                        if os.path.exists(temp_f):
                            os.remove(temp_f)
                            logger.debug(f"Cleaned up temp file: {temp_f}")
                    job.temp_files.clear()

                    job.status = "Completed"
                    job.progress = 100
                    job.eta = "Done"
                    job.speed = "Done"
                    self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Completed download: {job.title}"))
                    logger.info(f"Download and merge completed for '{job.title}'. Final file: {final_mp4_path}")

                elif is_combined_format:
                    job.current_phase = "combined_video_audio"
                    final_path = generate_unique_filename(os.path.join(job.out_dir, f"{sanitize_filename(job.title)}.{job.format_info.get('ext', 'mp4')}"))
                    ydl_opts_combined = {**ydl_opts, 'format': job.format_info['format_id'], 'outtmpl': final_path}

                    self.ui_queue.put((job.tree_item_id, 'status_update', "Downloading", f"Downloading video (combined) for: {job.title}"))
                    logger.info(f"Starting combined video/audio download for '{job.title}' (Format ID: {job.format_info['format_id']}).")
                    with yt_dlp.YoutubeDL(ydl_opts_combined) as ydl:
                        ydl.download([job.url])
                    
                    if not job.stop_event.is_set():
                        job.status = "Completed"
                        job.progress = 100
                        job.eta = "Done"
                        job.speed = "Done"
                        self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Completed download: {job.title}"))
                        logger.info(f"Combined download completed for '{job.title}'. Final file: {final_path}")
                    else:
                        job.status = "Paused"
                        self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Download {job.title} was interrupted."))
                        logger.info(f"Combined download for '{job.title}' interrupted by user.")
            
            elif job.choice == "audio":
                job.current_phase = "audio_only"
                final_mp3_path = generate_unique_filename(os.path.join(job.out_dir, f"{sanitize_filename(job.title)}.mp3"))
                if job.format_info.get("is_best_audio_option"):
                    yt_dlp_outtmpl = os.path.splitext(final_mp3_path)[0] + '.%(ext)s'
                    ydl_opts_audio = {
                        **ydl_opts,
                        'format': "bestaudio/best",
                        'outtmpl': yt_dlp_outtmpl,
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '320',
                        }, {
                            'key': 'FFmpegMetadata',
                        }],
                        'keepvideo': False,
                    }
                else: 
                    yt_dlp_outtmpl = os.path.splitext(final_mp3_path)[0] + '.%(ext)s'
                    ydl_opts_audio = {
                        **ydl_opts,
                        'format': job.format_info['format_id'],
                        'outtmpl': yt_dlp_outtmpl,
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3', 
                            'preferredquality': '192', 
                        }, {
                            'key': 'FFmpegMetadata',
                        }],
                        'keepvideo': False,
                    }

                self.ui_queue.put((job.tree_item_id, 'status_update', "Downloading", f"Downloading audio: {job.title}"))
                logger.info(f"Starting audio-only download for '{job.title}'.")
                with yt_dlp.YoutubeDL(ydl_opts_audio) as ydl:
                    ydl.download([job.url])

                if not job.stop_event.is_set():
                    job.status = "Completed"
                    job.progress = 100
                    job.eta = "Done"
                    job.speed = "Done"
                    self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Completed download: {job.title}"))
                    logger.info(f"Audio download completed for '{job.title}'. Final file: {final_mp3_path}")
                else:
                    job.status = "Paused"
                    self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Download {job.title} was interrupted."))
                    logger.info(f"Audio download for '{job.title}' interrupted by user.")
            
            if job.sub_lang != "None" and not job.stop_event.is_set():
                sub_outtmpl = f"{base_outtmpl_no_ext}.{job.sub_lang}.%(ext)s"
                ydl_opts_subs = {
                    'writesubtitles': True,
                    'writeautomaticsub': True,
                    'subtitleslangs': [job.sub_lang],
                    'skip_download': True,
                    'outtmpl': sub_outtmpl,
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': True,
                }
                self.ui_queue.put((job.tree_item_id, 'status_update', "Downloading Subtitles", f"Downloading subtitles for: {job.title} ({job.sub_lang})"))
                logger.info(f"Attempting to download subtitles for '{job.title}' in language: {job.sub_lang}.")
                try:
                    with yt_dlp.YoutubeDL(ydl_opts_subs) as ydl:
                        ydl.download([job.url])
                    self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Subtitles downloaded for: {job.title}"))
                    logger.info(f"Subtitles downloaded for '{job.title}' ({job.sub_lang}).")
                except Exception as e:
                    self.ui_queue.put((job.tree_item_id, 'warning', f"Could not download subtitles for {job.title}: {e}"))
                    logger.warning(f"Could not download subtitles for '{job.title}' ({job.sub_lang}): {e}")
        
        except yt_dlp.utils.DownloadError as e:
            if job.stop_event.is_set():
                job.status = "Paused"
                self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Paused download: {job.title}"))
                logger.info(f"Download '{job.title}' paused due to yt-dlp error during interruption.")
            else:
                job.status = "Error"
                error_detail = str(e)
                if "FFmpeg" in str(e) and ("not found" in str(e) or "executable not found" in str(e)):
                    error_detail = "FFmpeg is missing or not in PATH, needed for this conversion/merging. Please install FFmpeg."
                elif "no appropriate format" in str(e).lower():
                    error_detail = "No suitable format found for the selected options or URL."
                self.ui_queue.put((job.tree_item_id, 'error', f"Error downloading {job.title}:\n{error_detail}"))
                self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Error on download: {job.title}"))
                logger.error(f"DownloadError for '{job.title}': {error_detail}")
            for temp_f in job.temp_files:
                if os.path.exists(temp_f):
                    try:
                        os.remove(temp_f)
                        logger.debug(f"Cleaned up temp file on yt-dlp error: {temp_f}")
                    except Exception as e_clean:
                        logger.error(f"Error cleaning up temp file {temp_f} on yt-dlp error: {e_clean}")
            job.temp_files.clear()
        except Exception as e:
            job.status = "Error"
            self.ui_queue.put((job.tree_item_id, 'error', f"An unexpected error occurred downloading {job.title}:\n{e}"))
            self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Error on download: {job.title}"))
            logger.exception(f"An unexpected error occurred in download_worker for '{job.title}'.") # Log full traceback
            for temp_f in job.temp_files:
                if os.path.exists(temp_f):
                    try:
                        os.remove(temp_f)
                        logger.debug(f"Cleaned up temp file on unexpected error: {temp_f}")
                    except Exception as e_clean:
                        logger.error(f"Error cleaning up temp file {temp_f} on unexpected error: {e_clean}")
            job.temp_files.clear()
        finally:
            self.save_queue()
            logger.info(f"Download worker for '{job.title}' finished.")


    def ytdl_hook(self, d, job: DownloadJob):
        if job.stop_event.is_set():
            logger.info(f"yt-dlp hook: Stop event detected for '{job.title}'. Raising DownloadError.")
            raise yt_dlp.utils.DownloadError("Download stopped by user.")

        current_time = time.time()
        
        job.eta = self._strip_ansi_codes(d.get('_eta_str', "N/A"))
        job.speed = self._strip_ansi_codes(d.get('_speed_str', "0 B/s"))

        if d['status'] == 'downloading':
            downloaded_bytes = d.get('downloaded_bytes', 0)
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0

            if job.current_phase == "video":
                job.video_downloaded_bytes = downloaded_bytes
                if total_bytes > 0:
                    job.video_total_bytes = total_bytes
            elif job.current_phase == "audio":
                job.audio_downloaded_bytes = downloaded_bytes
                if total_bytes > 0:
                    job.audio_total_bytes = total_bytes
            elif job.current_phase == "audio_only" or job.current_phase == "combined_video_audio":
                job.audio_downloaded_bytes = downloaded_bytes # Using audio_downloaded_bytes for simplicity
                if total_bytes > 0:
                    job.audio_total_bytes = total_bytes
                job.video_downloaded_bytes = 0 # Not applicable for these phases
                job.video_total_bytes = 0 # Not applicable for these phases

            total_downloaded = job.video_downloaded_bytes + job.audio_downloaded_bytes
            total_expected_size = job.video_total_bytes + job.audio_total_bytes

            if total_expected_size > 0:
                job.progress = (total_downloaded / total_expected_size) * 100
                job.current_size = f"{total_downloaded / (1024*1024):.2f} MB"
                job.total_size = f"{total_expected_size / (1024*1024):.2f} MB"
            else:
                job.progress = min(job.progress + (downloaded_bytes / (1024*1024) / 100.0), 99.9) # Small increment if size unknown
                job.current_size = f"{downloaded_bytes / (1024*1024):.2f} MB" if downloaded_bytes else "0 MB"
                job.total_size = "Unknown"
            
            active_downloads = sum(1 for j in self.jobs if j.status == "Downloading")
            if active_downloads <= 1:
                update_interval = self.min_update_interval
            elif active_downloads <= 3:
                update_interval = self.min_update_interval * 1.5
            elif active_downloads <= 5:
                update_interval = self.min_update_interval * 2
            else:
                update_interval = self.max_update_interval
            
            if current_time - job.last_ui_update_time > update_interval:
                self.ui_queue.put((job.tree_item_id, 'progress'))
                job.last_ui_update_time = current_time

        elif d['status'] == 'finished':
            if job.current_phase == "video":
                job.video_total_bytes = d.get('total_bytes') or d.get('downloaded_bytes', 0)
                job.video_downloaded_bytes = job.video_total_bytes
                logger.debug(f"Video download finished for '{job.title}'. Total bytes: {job.video_total_bytes}")
            elif job.current_phase == "audio":
                job.audio_total_bytes = d.get('total_bytes') or d.get('downloaded_bytes', 0)
                job.audio_downloaded_bytes = job.audio_total_bytes
                logger.debug(f"Audio download finished for '{job.title}'. Total bytes: {job.audio_total_bytes}")
            elif job.current_phase == "audio_only" or job.current_phase == "combined_video_audio":
                job.audio_total_bytes = d.get('total_bytes') or d.get('downloaded_bytes', 0)
                job.audio_downloaded_bytes = job.audio_total_bytes
                logger.debug(f"Audio-only/Combined download finished for '{job.title}'. Total bytes: {job.audio_total_bytes}")

            total_downloaded = job.video_downloaded_bytes + job.audio_downloaded_bytes
            total_expected_size = job.video_total_bytes + job.audio_total_bytes
            if total_expected_size > 0:
                job.progress = (total_downloaded / total_expected_size) * 100
                job.current_size = f"{total_downloaded / (1024*1024):.2f} MB"
                job.total_size = f"{total_expected_size / (1024*1024):.2f} MB"
            
            self.ui_queue.put((job.tree_item_id, 'progress'))


        if job.status == "Queued":
            job.eta = "N/A"
            job.speed = "N/A"
            job.current_size = "0 MB"
            job.total_size = "Unknown"
        elif job.status == "Paused":
            job.eta = "Paused"
            job.speed = "Paused"
        elif job.status == "Pausing...":
            job.eta = "Pausing..."
            job.speed = "Pausing..."
        elif job.status == "Processing":
            job.eta = "Processing..."
            job.speed = "N/A"
            if job.progress < 100:
                job.progress = 99.9
        elif job.status == "Completed":
            job.eta = "Done"
            job.speed = "Done"
            job.progress = 100
        elif job.status == "Error":
            job.eta = "Error"
            job.speed = "Error"
            
    def on_job_select(self, event):
        selected_items = self.jobs_tree.selection()
        if not selected_items:
            if not any(j.status in ("Downloading", "Processing") for j in self.jobs):
                self.status_var.set("Idle")
                self.progress_var.set(0)
            self.pause_btn['state'] = 'disabled'
            self.cancel_btn['state'] = 'disabled'
            self.restart_btn['state'] = 'disabled'
            return

        selected_id = selected_items[0]
        with self.jobs_lock:
            job = next((j for j in self.jobs if j.tree_item_id == selected_id), None)

        if not job:
            return

        self.progress_var.set(job.progress)
        display_eta = self._strip_ansi_codes(job.eta)
        display_speed = self._strip_ansi_codes(job.speed)

        if job.status == "Downloading":
            status_text = f"Downloading: {job.title} - {job.progress:.1f}% ({display_speed}, ETA: {display_eta})"
        elif job.status == "Processing":
            status_text = f"Processing: {job.title} - {job.progress:.1f}% (Merging...)"
        elif job.status == "Pausing...":
            status_text = f"Pausing: {job.title}"
        elif job.status == "Paused":
            status_text = f"Paused: {job.title}"
        elif job.status == "Completed":
            status_text = f"Completed: {job.title}"
        elif job.status == "Error":
            status_text = f"Error: {job.title}"
        elif job.status == "Queued":
            status_text = f"Queued: {job.title}"
        elif job.status == "Canceled":
            status_text = f"Canceled: {job.title}"
        else:
            status_text = f"{job.status} {job.title}: {job.progress:.1f}%"
        
        self.status_var.set(status_text)


        self.cancel_btn['state'] = 'normal'
        
        self.restart_btn['state'] = 'normal' if job.status in ("Queued", "Paused", "Canceled", "Error", "Completed") else 'disabled'

        if job.status in ("Downloading", "Processing"):
            self.pause_btn['text'] = "Pause"
            self.pause_btn['state'] = 'normal'
        elif job.status == "Pausing...":
            self.pause_btn['text'] = "Pausing..."
            self.pause_btn['state'] = 'disabled'
        elif job.status == "Paused":
            self.pause_btn['text'] = "Resume"
            self.pause_btn['state'] = 'normal'
        else:
            self.pause_btn['state'] = 'disabled'


    def pause_resume_job(self):
        selected_items = self.jobs_tree.selection()
        if not selected_items: return

        selected_id = selected_items[0]
        with self.jobs_lock:
            job = next((j for j in self.jobs if j.tree_item_id == selected_id), None)
        if not job: return

        if job.status in ("Downloading", "Processing"):
            job.stop_event.set()
            job.status = "Pausing..."
            self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Pausing: {job.title}"))
            logger.info(f"User requested pause for '{job.title}'. Signaling stop event.")
        elif job.status == "Paused":
            self.start_download_job(job, select_in_ui=True)
            logger.info(f"User requested resume for '{job.title}'. Restarting download.")
        self.on_job_select(None)
        self.save_queue()

    def cancel_job(self):
        selected_items = self.jobs_tree.selection()
        if not selected_items: return

        selected_id = selected_items[0]
        with self.jobs_lock:
            job = next((j for j in self.jobs if j.tree_item_id == selected_id), None)
        if not job: return

        if messagebox.askyesno("Confirm Cancel", f"Are you sure you want to cancel and remove '{job.title}'?"):
            logger.info(f"User confirmed cancellation for '{job.title}'.")
            if job.thread and job.thread.is_alive():
                job.stop_event.set()
                logger.debug(f"Signaling stop for thread of '{job.title}'.")
                job.thread.join(timeout=3) # Wait for thread to finish
                if job.thread.is_alive():
                    logger.warning(f"Thread for '{job.title}' did not terminate gracefully within timeout.")
            for temp_f in job.temp_files:
                if os.path.exists(temp_f):
                    try:
                        os.remove(temp_f)
                        logger.debug(f"Cleaned up temp file: {temp_f}")
                    except Exception as e:
                        logger.error(f"Error cleaning up temp file {temp_f} for '{job.title}': {e}")
            job.temp_files.clear()

            with self.jobs_lock:
                if job in self.jobs:
                    self.jobs.remove(job)
                    self.jobs_tree.delete(job.tree_item_id)
                    logger.info(f"Job '{job.title}' removed from queue.")

            self.ui_queue.put((None, 'status_update', "Canceled", f"Canceled and removed: {job.title}"))
            self.on_job_select(None)
            self.save_queue()

    def restart_job(self):
        selected_items = self.jobs_tree.selection()
        if not selected_items: return

        selected_id = selected_items[0]
        with self.jobs_lock:
            job = next((j for j in self.jobs if j.tree_item_id == selected_id), None)
        if not job: return

        logger.info(f"User requested restart for '{job.title}'.")
        if job.thread and job.thread.is_alive():
            job.stop_event.set()
            logger.debug(f"Signaling stop for thread of '{job.title}' before restart.")
            job.thread.join(timeout=3)
            if job.thread.is_alive():
                logger.warning(f"Thread for '{job.title}' did not terminate gracefully before restart.")
        for temp_f in job.temp_files:
            if os.path.exists(temp_f):
                try:
                    os.remove(temp_f)
                    logger.debug(f"Cleaned up temp file for restart: {temp_f}")
                except Exception as e:
                    logger.error(f"Error cleaning up temp file {temp_f} for restart of '{job.title}': {e}")
        job.temp_files.clear()

        job.status = "Queued"
        job.progress = 0
        job.eta = "N/A"
        job.current_size = "0 MB"
        job.total_size = "Unknown"
        job.speed = "0 B/s"
        job.stop_event.clear()
        job.video_downloaded_bytes = 0
        job.audio_downloaded_bytes = 0
        job.video_total_bytes = 0
        job.audio_total_bytes = 0
        job.current_phase = "video"

        self.start_download_job(job, select_in_ui=True)
        self.on_job_select(None)
        self.save_queue()

    def clear_queue(self):
        if not messagebox.askyesno("Clear Queue", "Are you sure you want to clear the entire queue and stop all active downloads?"):
            return

        logger.info("User confirmed clearing the entire queue.")
        with self.jobs_lock:
            for job in self.jobs:
                if job.thread and job.thread.is_alive():
                    job.stop_event.set()
                    logger.debug(f"Signaling stop for '{job.title}' during queue clear.")
            
            for job in self.jobs:
                if job.thread and job.thread.is_alive():
                    job.thread.join(timeout=5) # Increased timeout for robustness
                    if job.thread.is_alive():
                        logger.warning(f"Thread for '{job.title}' did not terminate gracefully during queue clear.")
                    for temp_f in job.temp_files:
                        if os.path.exists(temp_f):
                            try:
                                os.remove(temp_f)
                                logger.debug(f"Cleaned up temp file on clear: {temp_f}")
                            except Exception as e:
                                logger.error(f"Error cleaning up temp file {temp_f} on clear: {e}")
                    job.temp_files.clear()
            self.jobs.clear() 

        self.jobs_tree.delete(*self.jobs_tree.get_children())
        self.status_var.set("Cleared download queue.")
        self.progress_var.set(0)
        self.on_job_select(None)
        self.save_queue()
        logger.info("Download queue cleared.")

    def clear_finished_or_errored_jobs(self):
        if not messagebox.askyesno("Clear Finished/Errored", "Are you sure you want to remove all completed and errored jobs from the queue?"):
            return

        logger.info("User confirmed clearing finished/errored jobs.")
        jobs_to_keep = []
        removed_count = 0
        with self.jobs_lock:
            for job in self.jobs:
                if job.status in ("Completed", "Error"):
                    if job.tree_item_id:
                        self.jobs_tree.delete(job.tree_item_id)
                    for temp_f in job.temp_files:
                        if os.path.exists(temp_f):
                            try:
                                os.remove(temp_f)
                                logger.debug(f"Cleaned up temp file for removed job: {temp_f}")
                            except Exception as e:
                                logger.error(f"Error cleaning up temp file {temp_f} for removed job: {e}")
                    job.temp_files.clear()
                    removed_count += 1
                else:
                    jobs_to_keep.append(job)
            self.jobs = jobs_to_keep

        if removed_count > 0:
            self.status_var.set(f"Removed {removed_count} completed/errored jobs.")
            logger.info(f"Removed {removed_count} completed/errored jobs from the queue.")
            self.save_queue()
            self.on_job_select(None)
        else:
            self.status_var.set("No completed or errored jobs to clear.")
            logger.info("No completed or errored jobs found to clear.")


    def save_queue(self):
        serializable_jobs = []
        with self.jobs_lock:
            for job in self.jobs:
                if job.status not in ("Downloading", "Processing", "Pausing..."):
                    serializable_jobs.append({
                        'url': job.url,
                        'choice': job.choice,
                        'format_info': job.format_info,
                        'sub_lang': job.sub_lang,
                        'out_dir': job.out_dir,
                        'title': job.title,
                        'status': job.status,
                    })
        try:
            with open(QUEUE_FILE, 'w') as f:
                json.dump(serializable_jobs, f, indent=4)
            logger.info(f"Queue saved to {QUEUE_FILE} ({len(serializable_jobs)} jobs).")
        except Exception as e:
            logger.error(f"Error saving queue to {QUEUE_FILE}: {e}")

    def load_queue(self):
        if not os.path.exists(QUEUE_FILE):
            logger.info(f"No queue file found at {QUEUE_FILE}.")
            return

        try:
            with open(QUEUE_FILE, 'r') as f:
                loaded_jobs_data = json.load(f)
            
            jobs_to_keep = []
            with self.jobs_lock:
                for job_data in loaded_jobs_data:
                    expected_file_path_base = None
                    if job_data['status'] == "Completed":
                        sanitized_title = sanitize_filename(job_data['title'])
                        if job_data['choice'] == "video":
                            expected_file_path_base = os.path.join(job_data['out_dir'], f"{sanitized_title}.mp4")
                        elif job_data['choice'] == "audio":
                            expected_file_path_base = os.path.join(job_data['out_dir'], f"{sanitized_title}.mp3")

                        file_found = False
                        if expected_file_path_base:
                            base_name_without_ext, ext = os.path.splitext(expected_file_path_base)
                            parent_dir = os.path.dirname(expected_file_path_base)
                            
                            if os.path.exists(expected_file_path_base):
                                file_found = True
                            else:
                                expected_filename_prefix = os.path.basename(base_name_without_ext) + "("
                                if os.path.isdir(parent_dir): # Ensure directory exists before listing
                                    for filename in os.listdir(parent_dir):
                                        if filename.startswith(expected_filename_prefix) and filename.endswith(ext):
                                            file_found = True
                                            break
                                if not file_found and os.path.exists(os.path.join(parent_dir, os.path.basename(base_name_without_ext) + ext)):
                                    file_found = True

                        if job_data['status'] == "Completed" and not file_found:
                            logger.warning(f"Completed download '{job_data['title']}' not found at expected location '{expected_file_path_base}'. Removing from queue.")
                            continue 
                        elif job_data['status'] == "Completed" and file_found:
                            logger.info(f"Completed download '{job_data['title']}' found on disk.")
                    
                    job = DownloadJob(
                        url=job_data['url'],
                        choice=job_data['choice'],
                        format_info=job_data['format_info'],
                        sub_lang=job_data['sub_lang'],
                        out_dir=job_data['out_dir'],
                        title=job_data['title'],
                        status=job_data['status']
                    )

                    size_bytes = job.format_info.get('filesize') or job.format_info.get('filesize_approx')
                    if size_bytes:
                        job.total_size = f"{size_bytes / (1024*1024):.2f} MB"
                        if job.status == "Completed":
                            job.current_size = job.total_size
                            job.progress = 100.0 # Set progress to 100% if completed


                    jobs_to_keep.append(job)
                    job.tree_item_id = self.jobs_tree.insert('', 'end',
                                                             values=(job.title, job.status, f"{job.progress:.1f}%"))
                    self.ui_queue.put((job.tree_item_id, 'status_update', job.status, f"Loaded from queue: {job.title}"))
            
            self.jobs = jobs_to_keep # Update the jobs list with the filtered jobs
            logger.info(f"Queue loaded from {QUEUE_FILE} ({len(self.jobs)} active jobs after validation).")
            self.save_queue() # Resave the queue to reflect any removals
        except json.JSONDecodeError as e:
            messagebox.showerror("Error Loading Queue", f"Could not parse queue file. It might be corrupted. Error: {e}")
            logger.exception(f"Error loading queue from {QUEUE_FILE}: JSON decode error.")
        except Exception as e:
            messagebox.showerror("Error Loading Queue", f"An unexpected error occurred while loading queue: {e}")
            logger.exception(f"An unexpected error occurred while loading queue from {QUEUE_FILE}.")
        self.on_job_select(None)

    def show_about(self):
        messagebox.showinfo("About YouTube Downloader",
                             "YouTube Downloader\n\n"
                             "Built with yt-dlp and Tkinter (ttkbootstrap).\n"
                             "Features:\n"
                             "- Fetch video/audio formats\n"
                             "- Download selected formats immediately or add to queue\n"
                             "- Manage multiple downloads: Pause/Resume, Cancel, Restart\n"
                             "- Download subtitles\n"
                             "- Persistent queue: Downloads are saved/loaded on app restart.\n\n"
                             "**IMPORTANT:** Ensure 'yt-dlp' and 'ffmpeg' (for merging/conversion) are installed and in your system's PATH. If not, please install them for full functionality.\n"
                             "For FFmpeg, visit: https://ffmpeg.org/download.html"
                            )
        logger.info("About dialog displayed.")

    def on_close(self):
        self.save_queue() # Save queue before closing

        active_jobs = [job for job in self.jobs if job.thread and job.thread.is_alive() and not job.stop_event.is_set()]
        if active_jobs:
            if not messagebox.askyesno("Exit Application", "There are active downloads. Exiting will stop them and attempt to clean up partial files. Are you sure you want to exit?"):
                return
        
        logger.info("Application closing. Attempting to terminate active downloads.")
        with self.jobs_lock:
            for job in self.jobs:
                if job.thread and job.thread.is_alive():
                    job.stop_event.set() # Signal all threads to stop
                    logger.debug(f"Signaling stop for '{job.title}' on application close.")
            
            for job in self.jobs:
                if job.thread and job.thread.is_alive():
                    job.thread.join(timeout=5) # Wait with timeout
                    if job.thread.is_alive():
                        logger.warning(f"Thread for '{job.title}' did not terminate gracefully within timeout on exit.")
                    for temp_f in job.temp_files:
                        if os.path.exists(temp_f):
                            try:
                                os.remove(temp_f)
                                logger.debug(f"Cleaned up temp file on exit: {temp_f}")
                            except Exception as e:
                                logger.error(f"Error cleaning up temp file {temp_f} on exit: {e}")
                    job.temp_files.clear()
        
        logger.info("All active download threads terminated (or timed out). Exiting application.")
        self.destroy() # Close the Tkinter window

if __name__ == "__main__":
    app = YTDownloaderApp()
    app.mainloop()