def _move_to(self, folder):
    dest_dir = self.source_dir / folder
    dest_dir.mkdir(exist_ok=True)

    dest = dest_dir / self.current_path.name
    shutil.move(self.current_path, dest)

    self.actions.append({"name": self.current_path.name, "dest": dest})

    # update last folder BEFORE loading next file
    self.last_folder_name = folder

    self.index += 1
    self.load_next_file()
