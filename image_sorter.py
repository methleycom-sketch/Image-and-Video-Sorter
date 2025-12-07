#!/usr/bin/env python3

import shutil
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
import random

import cv2
from PIL import Image, ImageTk  # pip install pillow opencv-python


SOURCE_DIR = Path("/Volumes/External 2TB drive/OBS/PhotoPrism/Photos").expanduser()

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mpeg4"}
VALID_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Computing pairwise histogram similarity is O(n^2). Large folders were making startup
# extremely slow and occasionally exhausting memory. Keep the similarity ordering fast
# by switching to a simple alphabetical sort once the photo set exceeds this threshold.
MAX_SIMILARITY_IMAGES = 250


class ImageSorterApp:
    def __init__(self, root, source_dir):
        self.root = root
        self.source_dir = Path(source_dir)

        # file list, only direct children of Photos
        self.files = self._get_files()

        self.index = 0
        self.current_path = None
        self.current_image_tk = None
        self.current_image_pil = None

        # main window video state
        self.video_cap = None
        self.video_playing = False
        self.video_frame_job = None

        self.actions = []
        self.last_folder_name = None

        # shuffle window state (shared by Shuffle Sorted and Shuffle Selected)
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

        # shuffle selected config window
        self.shuffle_sel_config_window = None
        self.shuffle_sel_vars = {}

        root.title("Image and Video Sorter")
        root.bind("<Key>", self.on_keypress)

        self.filename_label = tk.Label(root, text="", font=("Arial", 12))
        self.filename_label.pack(pady=5)

        # MAIN AREA: buttons on the left, preview on the right
        main_frame = tk.Frame(root)
        main_frame.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)

        # left column: controls
        buttons = tk.Frame(main_frame)
        buttons.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # right area: image only
        self.preview_frame = tk.Frame(main_frame)
        self.preview_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        self.preview_label = tk.Label(self.preview_frame)
        self.preview_label.pack(padx=10, fill=tk.BOTH, expand=True)

        # re render image on resize
        self.preview_frame.bind("<Configure>", self.on_preview_resize)

        # entry field in left column
        entry_frame = tk.Frame(buttons)
        entry_frame.pack(pady=5, fill=tk.X)

        tk.Label(entry_frame, text="Folder name:").pack(anchor="w")
        self.folder_var = tk.StringVar()
        self.folder_combobox = ttk.Combobox(
            entry_frame,
            textvariable=self.folder_var,
            width=18,
            values=[],
        )
        self.folder_combobox.pack(fill=tk.X, pady=2)
        self.folder_combobox.bind("<Return>", self.move_current_file)
        self.folder_combobox.bind("<KeyRelease>", self.on_folder_typed)

        # main buttons in left column
        tk.Button(buttons, text="Move file", command=self.move_current_file).pack(fill=tk.X, pady=2)
        tk.Button(buttons, text="Skip", command=self.skip_file).pack(fill=tk.X, pady=2)

        self.rotate_button = tk.Button(buttons, text="Rotate image 90°",
                                       command=self.rotate_image)
        self.rotate_button.pack(fill=tk.X, pady=2)

        self.add_prev_button = tk.Button(buttons, text="Add to (none)",
                                         state=tk.DISABLED,
                                         command=self.add_to_previous_folder)
        self.add_prev_button.pack(fill=tk.X, pady=2)

        tk.Button(buttons, text="Send to Delete folder", fg="red",
                  command=self.send_to_delete).pack(fill=tk.X, pady=2)

        tk.Button(buttons, text="Undo last move",
                  command=self.undo_last_action).pack(fill=tk.X, pady=2)

        # Shuffle buttons
        tk.Button(buttons, text="Shuffle Sorted",
                  command=self.open_shuffle_sorted).pack(fill=tk.X, pady=2)

        tk.Button(buttons, text="Shuffle Selected",
                  command=self.open_shuffle_selected_config).pack(fill=tk.X, pady=2)

        tk.Button(buttons, text="Quit", command=root.destroy).pack(fill=tk.X, pady=2)

        # info + open + status, under the buttons
        self.info_label = tk.Label(buttons, text="", font=("Arial", 10),
                                   justify="left", wraplength=180)
        self.info_label.pack(fill=tk.X, pady=(10, 2))

        self.open_video_button = tk.Button(buttons, text="Open externally",
                                           command=self.open_video)

        self.status_label = tk.Label(buttons, anchor="w", justify="left")
        self.status_label.pack(fill=tk.X, pady=(10, 0))

        if not self.files:
            messagebox.showinfo("No files", "No files found in Photos.")
            root.destroy()
            return

        self.refresh_folder_options()

        self.load_next_file()

    # ---------------- KEYBOARD SHORTCUTS ----------------

    def on_keypress(self, event):
        if event.char == "§":
            self.send_to_delete()
        elif event.char == "=":
            self.add_to_previous_folder()
        elif event.char == "-":
            self.send_to_misc()

    # ---------------- FILE LIST ----------------
    # Only files directly in SOURCE_DIR (Photos), videos first then images

    def _get_files(self):
        images, videos = [], []
        if self.source_dir.exists():
            for p in self.source_dir.iterdir():
                if not p.is_file():
                    continue
                ext = p.suffix.lower()
                if ext in IMAGE_EXTS:
                    images.append(p)
                elif ext in VIDEO_EXTS:
                    videos.append(p)

        ordered_images = self._order_images_by_similarity(images)
        return sorted(videos) + ordered_images

    def _compute_image_signature(self, path):
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((64, 64))
            hist = img.histogram()
            total = sum(hist)
            if not total:
                return None
            return [h / total for h in hist]
        except Exception:
            return None

    def _hist_distance(self, hist_a, hist_b):
        return sum((a - b) ** 2 for a, b in zip(hist_a, hist_b))

    def _order_images_by_similarity(self, image_paths):
        if not image_paths:
            return []

        # Avoid the O(n^2) similarity ordering for very large folders to keep startup fast
        # and memory usage predictable.
        if len(image_paths) > MAX_SIMILARITY_IMAGES:
            return sorted(image_paths, key=lambda p: p.name.lower())

        features = []
        fallbacks = []

        for path in image_paths:
            signature = self._compute_image_signature(path)
            if signature is None:
                fallbacks.append(path)
            else:
                features.append((path, signature))

        ordered = []
        if features:
            features.sort(key=lambda item: item[0].name.lower())
            current_path, current_sig = features.pop(0)
            ordered.append(current_path)

            while features:
                next_idx, (next_path, next_sig) = min(
                    enumerate(features),
                    key=lambda item: self._hist_distance(current_sig, item[1][1]),
                )
                features.pop(next_idx)
                ordered.append(next_path)
                current_sig = next_sig

        ordered.extend(sorted(fallbacks, key=lambda p: p.name.lower()))
        return ordered

    # ---------------- SHUFFLE SORTED ----------------

    def open_shuffle_sorted(self):
        if self.shuffle_window is not None and tk.Toplevel.winfo_exists(self.shuffle_window):
            self.close_shuffle_window()

        candidates = []
        if self.source_dir.exists():
            for f in self.source_dir.iterdir():
                if f.is_file() and f.suffix.lower() in VALID_EXTS:
                    candidates.append((f, self.source_dir.name))

        if not candidates:
            messagebox.showinfo("Shuffle Sorted", "No images or videos found in Photos.")
            return

        self._open_shuffle_window(candidates, "Shuffle Sorted")

    # ---------------- SHUFFLE SELECTED: CONFIG WINDOW ----------------

    def open_shuffle_selected_config(self):
        if self.shuffle_sel_config_window is not None and tk.Toplevel.winfo_exists(self.shuffle_sel_config_window):
            self.shuffle_sel_config_window.lift()
            return

        folders = []
        if self.source_dir.exists():
            for entry in self.source_dir.iterdir():
                if entry.is_dir() and not entry.name.startswith(".") and entry.name != "Delete":
                    folders.append(entry.name)

        if not folders:
            messagebox.showinfo("Shuffle Selected", "No subfolders found in Photos.")
            return

        win = tk.Toplevel(self.root)
        win.title("Select folders to shuffle")
        self.shuffle_sel_config_window = win
        self.shuffle_sel_vars = {}

        tk.Label(win, text="Select folders to include:").pack(anchor="w", padx=10, pady=(10, 5))

        list_frame = tk.Frame(win)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # layout folders in a grid with up to 10 per row
        sorted_folders = sorted(folders, key=str.lower)
        max_per_row = 10

        for col in range(max_per_row):
            list_frame.grid_columnconfigure(col, weight=1)

        for idx, name in enumerate(sorted_folders):
            var = tk.BooleanVar(value=True)
            row = idx // max_per_row
            col = idx % max_per_row
            cb = tk.Checkbutton(list_frame, text=name, variable=var, anchor="w", justify="left")
            cb.grid(row=row, column=col, sticky="w", padx=5, pady=2)
            self.shuffle_sel_vars[name] = var

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=(0, 10))

        # new: select / deselect all
        def select_all():
            for v in self.shuffle_sel_vars.values():
                v.set(True)

        def deselect_all():
            for v in self.shuffle_sel_vars.values():
                v.set(False)

        tk.Button(btn_frame, text="Select all", command=select_all).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Deselect all", command=deselect_all).pack(side=tk.LEFT, padx=5)

        tk.Button(btn_frame, text="Go", command=self.start_shuffle_selected).pack(side=tk.LEFT, padx=5)

        def close_config():
            self.shuffle_sel_config_window = None
            win.destroy()

        tk.Button(btn_frame, text="Cancel", command=close_config).pack(side=tk.LEFT, padx=5)

        win.protocol("WM_DELETE_WINDOW", close_config)

    def start_shuffle_selected(self):
        if self.shuffle_sel_config_window is None:
            return

        selected_folders = [name for name, var in self.shuffle_sel_vars.items() if var.get()]

        if not selected_folders:
            messagebox.showwarning("Shuffle Selected", "Select at least one folder.")
            return

        candidates = []
        for folder_name in selected_folders:
            folder_path = self.source_dir / folder_name
            if not folder_path.is_dir():
                continue
            try:
                for f in folder_path.iterdir():
                    if f.is_file() and f.suffix.lower() in VALID_EXTS:
                        candidates.append((f, folder_name))
            except Exception:
                pass

        if not candidates:
            messagebox.showinfo("Shuffle Selected", "No matching files found in selected folders.")
            return

        self.shuffle_sel_config_window.destroy()
        self.shuffle_sel_config_window = None

        self._open_shuffle_window(candidates, "Shuffle Selected")

    # ---------------- SHUFFLE WINDOW (SHARED) ----------------

    def _open_shuffle_window(self, candidates, title):
        if self.shuffle_window is not None and tk.Toplevel.winfo_exists(self.shuffle_window):
            self.close_shuffle_window()

        self.shuffle_candidates = candidates
        self.shuffle_order = list(range(len(self.shuffle_candidates)))
        random.shuffle(self.shuffle_order)
        self.shuffle_index = 0
        self.shuffle_image_pil = None

        win = tk.Toplevel(self.root)
        win.title(title)
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

        win.bind("<Configure>", self.on_shuffle_resize)
        win.bind("<Right>", self.shuffle_next_image)
        win.bind("<Left>", self.shuffle_prev_image)

        self.shuffle_current_image_tk = None
        self.shuffle_show_current()

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
        idx = self.shuffle_order[self.shuffle_index]
        path, folder = self.shuffle_candidates[idx]
        ext = path.suffix.lower()

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

    def shuffle_next_image(self, event=None):
        if not self.shuffle_candidates:
            return
        self.shuffle_index = (self.shuffle_index + 1) % len(self.shuffle_order)
        self.shuffle_show_current()

    def shuffle_prev_image(self, event=None):
        if not self.shuffle_candidates:
            return
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

    def shuffle_start_video(self, path):
        self.shuffle_stop_video()
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self.shuffle_image_label.config(text="Could not open video")
            return
        self.shuffle_video_cap = cap
        self.shuffle_video_playing = True
        self.shuffle_show_video_frame()

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

    def start_video_preview(self, path):
        self.stop_video_preview()
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self.preview_label.config(text="Could not open video")
            return
        self.video_cap = cap
        self.video_playing = True
        self.show_video_frame()

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
            self.current_image_pil = self.current_image_pil.rotate(-90, expand=True)
            self.render_current_image()

    # ---------------- UI (MAIN WINDOW) ----------------

    def load_next_file(self):
        self.stop_video_preview()
        self.current_image_pil = None

        if self.index >= len(self.files):
            self.current_path = None
            self.filename_label.config(text="All done")
            self.preview_label.config(image="", text="")
            self.info_label.config(text="")
            self.status_label.config(text="Finished.")
            return

        self.current_path = self.files[self.index]
        self.filename_label.config(text=self.current_path.name)
        self.folder_var.set("")

        ext = self.current_path.suffix.lower()
        self.info_label.config(text="")
        self.open_video_button.pack_forget()

        if ext in IMAGE_EXTS:
            img = Image.open(self.current_path)
            self.current_image_pil = img
            self.render_current_image()
            self.rotate_button.config(state=tk.NORMAL)
        else:
            self.start_video_preview(self.current_path)
            self.rotate_button.config(state=tk.DISABLED)
            self.info_label.config(text="Video playing in preview")
            self.open_video_button.pack(fill=tk.X, pady=2)

        self.update_previous_button()
        self.update_status()

    def update_previous_button(self):
        if self.last_folder_name:
            self.add_prev_button.config(
                text=f"Add to {self.last_folder_name}",
                state=tk.NORMAL
            )
        else:
            self.add_prev_button.config(text="Add to (none)", state=tk.DISABLED)

    def open_video(self):
        if self.current_path:
            subprocess.Popen(["open", str(self.current_path)])

    # ---------------- ACTIONS (MAIN WINDOW) ----------------

    def _move_to(self, folder):
        dest_dir = self.source_dir / folder
        dest_dir.mkdir(exist_ok=True)

        self.refresh_folder_options()

        dest = dest_dir / self.current_path.name
        shutil.move(self.current_path, dest)

        self.actions.append({"name": self.current_path.name, "dest": dest})

        self.last_folder_name = folder

        self.index += 1
        self.load_next_file()

    def move_current_file(self, event=None):
        if not self.current_path:
            return
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showwarning("Missing folder name", "Enter a folder name.")
            return
        folder_path = self.source_dir / folder
        if not folder_path.exists():
            if not self.confirm_create_folder(folder):
                return
        self._move_to(folder)

    def confirm_create_folder(self, folder_name):
        win = tk.Toplevel(self.root)
        win.title("Create folder")
        win.transient(self.root)
        win.grab_set()

        msg = (
            f"The folder {folder_name} does not exist. "
            f"Would you like to create a new folder titled {folder_name}?"
        )

        tk.Label(win, text=msg, wraplength=320, justify="left").pack(
            padx=15, pady=(15, 10)
        )

        response = {"value": False}

        def choose(should_create):
            response["value"] = should_create
            win.destroy()

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=(0, 15))
        tk.Button(btn_frame, text="Yes", width=18, command=lambda: choose(True)).pack(
            side=tk.LEFT, padx=5
        )
        tk.Button(
            btn_frame,
            text="No, Choose different folder",
            width=22,
            command=lambda: choose(False),
        ).pack(side=tk.LEFT, padx=5)

        win.protocol("WM_DELETE_WINDOW", lambda: choose(False))
        self.root.wait_window(win)
        return response["value"]

    def _list_available_folders(self):
        folders = []
        if self.source_dir.exists():
            for entry in self.source_dir.iterdir():
                if entry.is_dir() and not entry.name.startswith("."):
                    folders.append(entry.name)
        folders.sort(key=str.lower)
        return folders

    def refresh_folder_options(self):
        self.folder_combobox["values"] = self._list_available_folders()

    def on_folder_typed(self, event=None):
        # simple autocomplete: update dropdown suggestions to matching entries
        text = self.folder_var.get().strip().lower()
        all_folders = self._list_available_folders()
        if not text:
            filtered = all_folders
        else:
            filtered = [name for name in all_folders if name.lower().startswith(text)]
        if filtered != list(self.folder_combobox["values"]):
            self.folder_combobox["values"] = filtered
        # reopen full list when cleared
        if not text:
            self.refresh_folder_options()

    def add_to_previous_folder(self):
        if self.current_path and self.last_folder_name:
            self._move_to(self.last_folder_name)

    def send_to_delete(self):
        if self.current_path:
            self._move_to("Delete")

    def send_to_misc(self):
        if self.current_path:
            self._move_to("Misc")

    def skip_file(self):
        if self.current_path:
            self.index += 1
            self.load_next_file()

    def undo_last_action(self):
        if not self.actions:
            messagebox.showinfo("Undo", "Nothing to undo.")
            return

        last = self.actions.pop()
        name = last["name"]
        src = last["dest"]
        dest = self.source_dir / name

        if not src.exists():
            messagebox.showerror("Undo error", f"File missing at {src}")
            return

        shutil.move(src, dest)

        if self.index > 0:
            self.index -= 1
        self.load_next_file()

    def update_status(self):
        remaining = len(self.files) - self.index
        self.status_label.config(
            text=f"File {self.index + 1} of {len(self.files)} | Remaining: {remaining}"
        )


def main():
    root = tk.Tk()
    ImageSorterApp(root, SOURCE_DIR)
    root.mainloop()


if __name__ == "__main__":
    main()
