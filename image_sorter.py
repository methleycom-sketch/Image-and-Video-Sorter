#!/usr/bin/env python3
import shutil
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
import random
from datetime import datetime
from time import perf_counter

import cv2
from PIL import Image, ImageTk  # pip install pillow opencv-python


SOURCE_DIR = Path("/Volumes/External 2TB drive/OBS/PhotoPrism/Photos").expanduser()

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mpeg4"}
VALID_EXTS = IMAGE_EXTS | VIDEO_EXTS

STATE_FILE = SOURCE_DIR / ".sorted_history.txt"  # records processed files


class ImageSorterApp:
    def __init__(self, root, source_dir):
        self.root = root
        self.source_dir = Path(source_dir)

        # logging state
        self.log_history = []
        self.max_log_entries = 500
        self.auto_raise_log = tk.BooleanVar(value=False)

        self.sorted_names = self._load_sorted_history()
        self.files = self._get_files()

        self.index = 0
        self.current_path = None
        self.current_image_tk = None
        self.current_image_pil = None

        # main window video state
        self.video_cap = None
        self.video_playing = False
        self.video_frame_job = None
        self.load_generation = 0
        self.loading_thread = None

        self.actions = []
        self.last_folder_name = None
        self.moving_file = False
        self.control_widgets = []

        # shuffle window state
        self.shuffle_window = None
        self.shuffle_candidates = []
        self.shuffle_image_label = None
        self.shuffle_info_label = None
        self.shuffle_current_image_tk = None
        self.shuffle_image_pil = None
        self.shuffle_order = []
        self.shuffle_index = 0

        # shuffle video state
        self.shuffle_video_cap = None
        self.shuffle_video_playing = False
        self.shuffle_video_frame_job = None

        # folder counts background job
        self.folder_count_thread = None

        root.title("Image and Video Sorter")
        root.bind("<Key>", self.on_keypress)

        self.filename_label = tk.Label(root, text="", font=("Arial", 12))
        self.filename_label.pack(pady=5)

        # MAIN AREA: buttons on the left, preview on the right
        main_frame = tk.Frame(root)
        main_frame.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)
        self.main_frame = main_frame

        # left column: controls
        buttons = tk.Frame(main_frame)
        buttons.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # right area: image + counts
        preview_and_log = tk.Frame(main_frame)
        preview_and_log.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        self.preview_frame = tk.Frame(preview_and_log)
        self.preview_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        self.preview_label = tk.Label(self.preview_frame)
        self.preview_label.pack(side=tk.LEFT, padx=10, fill=tk.BOTH, expand=True)

        self.counts_label = tk.Label(
            self.preview_frame, text="", font=("Arial", 10),
            justify=tk.LEFT, anchor="nw"
        )
        self.counts_label.pack(side=tk.LEFT, padx=10, anchor="n")

        # re render image on resize
        self.preview_frame.bind("<Configure>", self.on_preview_resize)

        # log pane
        self.log_frame = tk.Frame(preview_and_log, width=260)
        self.log_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))

        log_header = tk.Frame(self.log_frame)
        log_header.pack(fill=tk.X)
        tk.Label(log_header, text="Log", font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        tk.Checkbutton(
            log_header,
            text="Auto-raise",
            variable=self.auto_raise_log,
            command=self._raise_log_pane
        ).pack(side=tk.RIGHT)
        tk.Button(log_header, text="Clear", command=self.clear_log).pack(side=tk.RIGHT, padx=5)

        log_text_frame = tk.Frame(self.log_frame)
        log_text_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        scrollbar = tk.Scrollbar(log_text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = tk.Text(
            log_text_frame,
            height=10,
            width=40,
            wrap="word",
            state=tk.DISABLED
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.configure(command=self.log_text.yview)

        # entry field in left column
        entry_frame = tk.Frame(buttons)
        entry_frame.pack(pady=5, fill=tk.X)

        tk.Label(entry_frame, text="Folder name:").pack(anchor="w")
        self.folder_entry = tk.Entry(entry_frame, width=18)
        self.folder_entry.pack(fill=tk.X, pady=2)
        self.folder_entry.bind("<Return>", self.move_current_file)
        self.control_widgets.append(self.folder_entry)

        # main buttons in left column
        move_btn = tk.Button(buttons, text="Move file", command=self.move_current_file)
        move_btn.pack(fill=tk.X, pady=2)
        self.control_widgets.append(move_btn)

        skip_btn = tk.Button(buttons, text="Skip", command=self.skip_file)
        skip_btn.pack(fill=tk.X, pady=2)
        self.control_widgets.append(skip_btn)

        self.rotate_button = tk.Button(buttons, text="Rotate image 90°",
                                       command=self.rotate_image)
        self.rotate_button.pack(fill=tk.X, pady=2)
        self.control_widgets.append(self.rotate_button)

        self.add_prev_button = tk.Button(buttons, text="Add to (none)",
                                         state=tk.DISABLED,
                                         command=self.add_to_previous_folder)
        self.add_prev_button.pack(fill=tk.X, pady=2)
        self.control_widgets.append(self.add_prev_button)

        delete_btn = tk.Button(buttons, text="Send to Delete folder", fg="red",
                               command=self.send_to_delete)
        delete_btn.pack(fill=tk.X, pady=2)
        self.control_widgets.append(delete_btn)

        undo_btn = tk.Button(buttons, text="Undo last move",
                             command=self.undo_last_action)
        undo_btn.pack(fill=tk.X, pady=2)
        self.control_widgets.append(undo_btn)

        shuffle_btn = tk.Button(buttons, text="Shuffle Sorted",
                                command=self.open_shuffle_sorted)
        shuffle_btn.pack(fill=tk.X, pady=2)
        self.control_widgets.append(shuffle_btn)

        tk.Button(buttons, text="Quit", command=root.destroy).pack(fill=tk.X, pady=2)

        # info + open + status, all under the buttons in the left column
        self.info_label = tk.Label(buttons, text="", font=("Arial", 10),
                                   justify="left", wraplength=180)
        self.info_label.pack(fill=tk.X, pady=(10, 2))

        self.open_video_button = tk.Button(buttons, text="Open externally",
                                           command=self.open_video)
        self.control_widgets.append(self.open_video_button)

        self.status_label = tk.Label(buttons, anchor="w", justify="left")
        self.status_label.pack(fill=tk.X, pady=(10, 0))

        if not self.files:
            messagebox.showinfo("No files", "No unsorted files found.")
            root.destroy()
            return

        self.update_folder_counts()
        self.load_next_file()
        self._refresh_log_widget()

    # ---------------- KEYBOARD SHORTCUTS ----------------

    def on_keypress(self, event):
        if event.char == "§":
            self.send_to_delete()
        elif event.char == "=":
            self.add_to_previous_folder()
        elif event.char == "-":
            self.send_to_misc()

    # ---------------- FILE HISTORY ----------------

    def _load_sorted_history(self):
        if not STATE_FILE.exists():
            return set()
        return {line.strip() for line in STATE_FILE.read_text().splitlines() if line.strip()}

    def _mark_sorted(self, path):
        name = path.name
        if name not in self.sorted_names:
            self.sorted_names.add(name)
            STATE_FILE.write_text("\n".join(sorted(self.sorted_names)))

    def _unmark_sorted_by_name(self, name):
        if name in self.sorted_names:
            self.sorted_names.remove(name)
            STATE_FILE.write_text("\n".join(sorted(self.sorted_names)))

    # ---------------- FILE LIST ----------------
    # Videos first, then images

    def _get_files(self):
        start_time = perf_counter()
        self.log_event("Scanning source folder for unsorted files...")
        images, videos = [], []
        for p in SOURCE_DIR.iterdir():
            if p.is_file() and p.name not in self.sorted_names:
                ext = p.suffix.lower()
                if ext in IMAGE_EXTS:
                    images.append(p)
                elif ext in VIDEO_EXTS:
                    videos.append(p)
        files = sorted(videos) + sorted(images)
        self.log_event(f"Found {len(files)} unsorted files", perf_counter() - start_time)
        return files

    # ---------------- FOLDER COUNTS ----------------

    def _collect_folder_counts(self):
        counts = []

        if SOURCE_DIR.exists():
            for entry in SOURCE_DIR.iterdir():
                if not entry.is_dir():
                    continue

                if entry.name.startswith("."):
                    continue

                if entry.name == "Delete":
                    continue

                count = 0
                try:
                    for f in entry.iterdir():
                        if f.is_file() and f.suffix.lower() in VALID_EXTS:
                            count += 1
                except Exception:
                    pass

                if count < 10:
                    continue

                counts.append((entry.name, count))

        counts.sort(key=lambda x: (-x[1], x[0].lower()))
        return "\n".join(f"{name}: {count}" for name, count in counts)

    def update_folder_counts(self):
        if self.folder_count_thread and self.folder_count_thread.is_alive():
            return

        start_time = perf_counter()
        self.log_event("Refreshing folder counts...")

        def worker():
            text = self._collect_folder_counts()
            duration = perf_counter() - start_time
            self.root.after(0, lambda: self.counts_label.config(text=text))
            self.log_event("Folder counts updated", duration)

        thread = threading.Thread(target=worker, daemon=True)
        self.folder_count_thread = thread
        thread.start()

    # ---------------- SHUFFLE SORTED WINDOW ----------------

    def open_shuffle_sorted(self):
        if self.shuffle_window is not None and tk.Toplevel.winfo_exists(self.shuffle_window):
            self.shuffle_window.lift()
            return

        start_time = perf_counter()
        self.log_event("Opening Shuffle Sorted window...")
        candidates = []
        if SOURCE_DIR.exists():
            for folder in SOURCE_DIR.iterdir():
                if not folder.is_dir():
                    continue
                if folder.name == "Delete" or folder.name.startswith("."):
                    continue
                try:
                    for f in folder.iterdir():
                        if f.is_file() and f.suffix.lower() in VALID_EXTS:
                            candidates.append((f, folder.name))
                except Exception:
                    pass

        if not candidates:
            messagebox.showinfo("Shuffle Sorted", "No sorted images or videos found.")
            self.log_event("Shuffle Sorted cancelled: no candidates found", perf_counter() - start_time)
            return

        self.shuffle_candidates = candidates
        self.shuffle_order = list(range(len(self.shuffle_candidates)))
        random.shuffle(self.shuffle_order)
        self.shuffle_index = 0
        self.shuffle_image_pil = None

        win = tk.Toplevel(self.root)
        win.title("Shuffle Sorted")
        self.shuffle_window = win

        img_label = tk.Label(win)
        img_label.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
        self.shuffle_image_label = img_label

        info_label = tk.Label(win, text="", font=("Arial", 10))
        info_label.pack(pady=5)
        self.shuffle_info_label = info_label

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=10)

        tk.Button(btn_frame, text="Next",
                  command=self.shuffle_next_image).pack(side=tk.LEFT, padx=5)

        tk.Button(btn_frame, text="Back",
                  command=self.close_shuffle_window).pack(side=tk.LEFT, padx=5)

        win.protocol("WM_DELETE_WINDOW", self.close_shuffle_window)

        # bind resize and arrow keys for navigation
        win.bind("<Configure>", self.on_shuffle_resize)
        win.bind("<Right>", self.shuffle_next_image)
        win.bind("<Left>", self.shuffle_prev_image)

        self.shuffle_current_image_tk = None
        self.shuffle_show_current()
        self.log_event(
            f"Shuffle Sorted ready with {len(self.shuffle_candidates)} candidates",
            perf_counter() - start_time,
        )

    def close_shuffle_window(self):
        self.shuffle_stop_video()
        if self.shuffle_window is not None:
            self.shuffle_window.destroy()
        self.shuffle_window = None
        self.shuffle_image_label = None
        self.shuffle_info_label = None
        self.shuffle_current_image_tk = None
        self.shuffle_image_pil = None

    def shuffle_show_current(self):
        if not self.shuffle_candidates or self.shuffle_window is None:
            return
        start_time = perf_counter()
        idx = self.shuffle_order[self.shuffle_index]
        path, folder = self.shuffle_candidates[idx]
        ext = path.suffix.lower()

        # stop any previous video playback
        self.shuffle_stop_video()

        try:
            if ext in IMAGE_EXTS:
                img = Image.open(path)
                self.shuffle_image_pil = img
                self.render_shuffle_image()
                kind = "IMAGE"
            elif ext in VIDEO_EXTS:
                self.shuffle_image_pil = None
                self.shuffle_start_video(path)
                kind = "VIDEO (playing)"
            else:
                self.shuffle_image_pil = None
                self.shuffle_image_label.config(image="")
                self.shuffle_info_label.config(
                    text=f"Unsupported file type:\n{path.name}"
                )
                return

            self.shuffle_info_label.config(
                text=f"[{kind}]\nFolder: {folder}\nFile: {path.name}"
            )
        except Exception as e:
            self.shuffle_image_pil = None
            self.shuffle_image_label.config(image="")
            self.shuffle_info_label.config(
                text=f"Could not open file:\n{path}\n{e}"
            )
        finally:
            self.log_event(
                f"Loaded shuffle item {self.shuffle_index + 1}/{len(self.shuffle_order)}: {path.name}",
                perf_counter() - start_time,
            )

    def shuffle_next_image(self, event=None):
        if not self.shuffle_candidates:
            return
        self.log_event("Shuffle: next image")
        self.shuffle_index = (self.shuffle_index + 1) % len(self.shuffle_order)
        self.shuffle_show_current()

    def shuffle_prev_image(self, event=None):
        if not self.shuffle_candidates:
            return
        self.log_event("Shuffle: previous image")
        self.shuffle_index = (self.shuffle_index - 1) % len(self.shuffle_order)
        self.shuffle_show_current()

    def _get_shuffle_preview_size(self):
        if self.shuffle_window is None:
            return (800, 600)
        w = self.shuffle_window.winfo_width()
        h = self.shuffle_window.winfo_height()
        w = max(100, w - 40)
        h = max(100, h - 120)
        return w, h

    def render_shuffle_image(self):
        if self.shuffle_image_pil is None or self.shuffle_image_label is None:
            return
        img = self.shuffle_image_pil.copy()
        max_w, max_h = self._get_shuffle_preview_size()
        img.thumbnail((max_w, max_h))
        self.shuffle_current_image_tk = ImageTk.PhotoImage(img)
        self.shuffle_image_label.config(image=self.shuffle_current_image_tk)

    def on_shuffle_resize(self, event):
        if self.shuffle_image_pil is not None:
            self.render_shuffle_image()
        # for video, next frame will adapt to new size

    # shuffle video handling

    def shuffle_stop_video(self):
        self.shuffle_video_playing = False
        if self.shuffle_video_frame_job and self.shuffle_window:
            try:
                self.shuffle_window.after_cancel(self.shuffle_video_frame_job)
            except Exception:
                pass
        self.shuffle_video_frame_job = None
        if self.shuffle_video_cap:
            self.shuffle_video_cap.release()
            self.shuffle_video_cap = None
        self.log_event("Stopped shuffle video preview")

    def shuffle_start_video(self, path):
        start_time = perf_counter()
        self.shuffle_stop_video()
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self.shuffle_image_label.config(text="Could not open video")
            self.log_event("Failed to start shuffle video preview")
            return
        self.shuffle_video_cap = cap
        self.shuffle_video_playing = True
        self.shuffle_show_video_frame()
        self.log_event(f"Started shuffle video preview for {path.name}", perf_counter() - start_time)

    def shuffle_show_video_frame(self):
        if not self.shuffle_video_playing or self.shuffle_video_cap is None or self.shuffle_window is None:
            return

        ret, frame = self.shuffle_video_cap.read()
        if not ret:
            self.shuffle_video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self.shuffle_video_cap.read()
            if not ret:
                return

        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame)

        max_w, max_h = self._get_shuffle_preview_size()
        img.thumbnail((max_w, max_h))
        self.shuffle_current_image_tk = ImageTk.PhotoImage(img)
        self.shuffle_image_label.config(image=self.shuffle_current_image_tk)

        # schedule next frame
        self.shuffle_video_frame_job = self.shuffle_window.after(40, self.shuffle_show_video_frame)

    # ---------------- PREVIEW SIZE / RESIZE (MAIN WINDOW) ----------------

    def _get_preview_size(self):
        w = self.preview_label.winfo_width()
        h = self.preview_frame.winfo_height()
        if w <= 1 or h <= 1:
            w, h = 800, 600
        return w, h

    def on_preview_resize(self, event):
        if self.current_image_pil is not None and self.current_path is not None:
            ext = self.current_path.suffix.lower()
            if ext in IMAGE_EXTS:
                self.render_current_image()
        # videos adjust on next frame

    # ---------------- VIDEO HANDLING (MAIN WINDOW) ----------------

    def stop_video_preview(self):
        self.video_playing = False
        if self.video_frame_job:
            try:
                self.root.after_cancel(self.video_frame_job)
            except Exception:
                pass
        self.video_frame_job = None
        if self.video_cap:
            self.video_cap.release()
            self.video_cap = None
        self.log_event("Stopped main video preview")

    def start_video_preview(self, path, preloaded_cap=None):
        start_time = perf_counter()
        self.stop_video_preview()
        cap = preloaded_cap or cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self.preview_label.config(text="Could not open video")
            self.log_event(f"Failed to start video preview for {path.name}")
            return
        self.video_cap = cap
        self.video_playing = True
        self.show_video_frame()
        self.log_event(f"Started video preview for {path.name}", perf_counter() - start_time)

    def show_video_frame(self):
        if not self.video_playing or self.video_cap is None:
            return

        ret, frame = self.video_cap.read()
        if not ret:
            self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self.video_cap.read()
            if not ret:
                return

        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame)

        max_w, max_h = self._get_preview_size()
        img.thumbnail((max_w, max_h))
        self.current_image_tk = ImageTk.PhotoImage(img)
        self.preview_label.config(image=self.current_image_tk)

        self.video_frame_job = self.root.after(40, self.show_video_frame)

    # ---------------- IMAGE PREVIEW (MAIN WINDOW) ----------------

    def render_current_image(self):
        if self.current_image_pil:
            img = self.current_image_pil.copy()
            max_w, max_h = self._get_preview_size()
            img.thumbnail((max_w, max_h))
            self.current_image_tk = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self.current_image_tk)

    def rotate_image(self):
        if self.current_image_pil:
            start_time = perf_counter()
            self.current_image_pil = self.current_image_pil.rotate(-90, expand=True)
            self.render_current_image()
            self.log_event("Rotated image 90°", perf_counter() - start_time)

    # ---------------- UI (MAIN WINDOW) ----------------

    def load_next_file(self):
        start_time = perf_counter()
        self.log_event("Loading next file...")
        self.stop_video_preview()
        self.current_image_pil = None

        if self.loading_thread and self.loading_thread.is_alive():
            self.load_generation += 1

        if self.moving_file:
            return

        if self.index >= len(self.files):
            self.current_path = None
            self.filename_label.config(text="All done")
            self.preview_label.config(image="", text="")
            self.info_label.config(text="")
            self.status_label.config(text="Finished.")
            self.update_folder_counts()
            self.log_event("All files processed", perf_counter() - start_time)
            return

        self.current_path = self.files[self.index]
        self.filename_label.config(text=self.current_path.name)
        self.folder_entry.delete(0, tk.END)

        ext = self.current_path.suffix.lower()
        self.info_label.config(text="Loading...")
        self.open_video_button.pack_forget()
        self.rotate_button.config(state=tk.DISABLED)

        generation = self.load_generation + 1
        self.load_generation = generation

        def worker():
            try:
                if ext in IMAGE_EXTS:
                    img = Image.open(self.current_path)
                    img.load()
                    return generation, self.current_path, img, None, None
                if ext in VIDEO_EXTS:
                    cap = cv2.VideoCapture(str(self.current_path))
                    if not cap.isOpened():
                        return generation, self.current_path, None, None, "Could not open video"
                    return generation, self.current_path, None, cap, None
                return generation, self.current_path, None, None, "Unsupported file type"
            except Exception as exc:
                return generation, self.current_path, None, None, str(exc)

        def on_complete(result):
            gen, path, img, cap, error = result
            if gen != self.load_generation:
                if cap:
                    cap.release()
                return

            self.loading_thread = None
            self.current_path = path
            self.info_label.config(text="")

            if error:
                self.preview_label.config(text=error, image="")
                self.status_label.config(text="Error loading file")
                self.update_status()
                self.log_event(f"Failed to load {path.name}: {error}", perf_counter() - start_time)
                return

            if img:
                self.current_image_pil = img
                self.render_current_image()
                self.rotate_button.config(state=tk.NORMAL)
            elif cap:
                self.start_video_preview(path, preloaded_cap=cap)
                self.rotate_button.config(state=tk.DISABLED)
                self.info_label.config(text="Video playing in preview")
                self.open_video_button.pack(fill=tk.X, pady=2)

            self.update_previous_button()
            self.update_folder_counts()
            self.update_status()
            self.log_event(f"Loaded {path.name}", perf_counter() - start_time)

        def runner():
            result = worker()
            self.root.after(0, lambda: on_complete(result))

        thread = threading.Thread(target=runner, daemon=True)
        self.loading_thread = thread
        thread.start()

    def update_previous_button(self):
        if self.last_folder_name:
            self.add_prev_button.config(
                text=f"Add to {self.last_folder_name}",
                state=tk.NORMAL
            )
        else:
            self.add_prev_button.config(text="Add to (none)", state=tk.DISABLED)

    def _set_controls_state(self, state):
        for widget in self.control_widgets:
            try:
                widget.config(state=state)
            except tk.TclError:
                pass

    def open_video(self):
        if self.current_path:
            subprocess.Popen(["open", str(self.current_path)])

    # ---------------- ACTIONS (MAIN WINDOW) ----------------

    def _move_to(self, folder):
        if not self.current_path or self.moving_file:
            return

        start_time = perf_counter()
        self.log_event(f"Moving file to {folder}...")

        self.moving_file = True
        self._set_controls_state(tk.DISABLED)
        self.status_label.config(text="Moving file...")
        path = self.current_path

        self.stop_video_preview()

        def worker():
            try:
                dest_dir = SOURCE_DIR / folder
                dest_dir.mkdir(exist_ok=True)
                dest = dest_dir / path.name
                shutil.move(path, dest)
                return dest, None
            except Exception as exc:
                return None, exc

        def on_complete(result):
            dest, error = result
            self.moving_file = False
            self._set_controls_state(tk.NORMAL)

            if error:
                messagebox.showerror("Move error", f"Could not move file:\n{error}")
                self.update_status()
                self.log_event(f"Failed to move file: {error}", perf_counter() - start_time)
                return

            self.actions.append({"name": path.name, "dest": dest})
            self._mark_sorted(path)
            self.index += 1
            self.last_folder_name = folder
            self.load_next_file()
            self.update_previous_button()
            self.update_status()
            self.log_event(f"Moved {path.name} to {folder}", perf_counter() - start_time)

        def runner():
            result = worker()
            self.root.after(0, lambda: on_complete(result))

        threading.Thread(target=runner, daemon=True).start()

    def move_current_file(self, event=None):
        if not self.current_path or self.moving_file:
            return
        folder = self.folder_entry.get().strip()
        if not folder:
            messagebox.showwarning("Missing folder name", "Enter a folder name.")
            return
        self._move_to(folder)

    def add_to_previous_folder(self):
        if self.current_path and self.last_folder_name and not self.moving_file:
            self._move_to(self.last_folder_name)

    def send_to_delete(self):
        if self.current_path and not self.moving_file:
            self._move_to("Delete")

    def send_to_misc(self):
        if self.current_path and not self.moving_file:
            self._move_to("Misc")

    def skip_file(self):
        if self.current_path and not self.moving_file:
            self.index += 1
            self.log_event("Skipped current file")
            self.load_next_file()

    def undo_last_action(self):
        if self.moving_file:
            return
        if not self.actions:
            messagebox.showinfo("Undo", "Nothing to undo.")
            return

        last = self.actions.pop()
        name = last["name"]
        src = last["dest"]
        dest = SOURCE_DIR / name

        if not src.exists():
            messagebox.showerror("Undo error", f"File missing at {src}")
            return

        shutil.move(src, dest)
        self._unmark_sorted_by_name(name)

        if self.index > 0:
            self.index -= 1
        self.load_next_file()
        self.log_event(f"Undid move for {name}")

    def update_status(self):
        remaining = len(self.files) - self.index
        self.status_label.config(
            text=f"File {self.index + 1} of {len(self.files)} | Remaining: {remaining}"
        )

    # ---------------- LOGGING ----------------

    def log_event(self, message, duration=None):
        timestamp = datetime.now().strftime("%H:%M:%S")
        suffix = f" ({duration:.3f}s)" if duration is not None else ""
        entry = f"[{timestamp}] {message}{suffix}"

        def append():
            self.log_history.append(entry)
            if len(self.log_history) > self.max_log_entries:
                self.log_history = self.log_history[-self.max_log_entries:]
            self._append_to_log_widget(entry, refresh=len(self.log_history) == self.max_log_entries)
            if self.auto_raise_log.get():
                self._raise_log_pane()

        if threading.current_thread() is threading.main_thread():
            append()
        else:
            self.root.after(0, append)

    def _append_to_log_widget(self, entry, refresh=False):
        if not hasattr(self, "log_text") or self.log_text is None:
            return
        if refresh:
            self._refresh_log_widget()
            return
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, entry + "\n")
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def _refresh_log_widget(self):
        if not hasattr(self, "log_text") or self.log_text is None:
            return
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        for entry in self.log_history:
            self.log_text.insert(tk.END, entry + "\n")
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_history.clear()
        self._refresh_log_widget()

    def _raise_log_pane(self):
        if hasattr(self, "log_frame") and self.log_frame.winfo_exists():
            self.log_frame.lift()
        if hasattr(self, "log_text") and self.log_text.winfo_exists():
            try:
                self.log_text.see(tk.END)
                self.log_text.focus_set()
            except tk.TclError:
                pass


def main():
    root = tk.Tk()
    ImageSorterApp(root, SOURCE_DIR)
    root.mainloop()


if __name__ == "__main__":
    main()
