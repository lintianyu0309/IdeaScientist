"""显示名称与可撤销删除；不修改科研输入、manifest 或检查点。"""
import json
import stat
from pathlib import Path
from uuid import uuid4

META_FILE = "ui_meta.json"
LOCK_FILE = ".running.lock"


def _is_link(path):
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _run_path(results_dir, run_dir):
    root = Path(results_dir).resolve()
    original = Path(run_dir)
    if not original.is_dir() or _is_link(original):
        raise ValueError("Select an existing run directory, not a linked directory.")
    target = original.resolve(strict=True)
    if target.parent != root or target.name.startswith("."):
        raise ValueError("The run must be directly inside the results directory.")
    return root, target


def _trash_path(root):
    trash = root / ".trash"
    if trash.exists() and (not trash.is_dir() or _is_link(trash)):
        raise ValueError("The trash directory is not a regular directory.")
    if trash.resolve().parent != root:
        raise ValueError("The trash directory must remain inside results.")
    trash.mkdir(exist_ok=True)
    return trash.resolve(strict=True)


def get_run_name(run_dir, default=None):
    path = Path(run_dir)
    try:
        value = json.loads((path / META_FILE).read_text(encoding="utf-8-sig"))
        name = value.get("display_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, ValueError, AttributeError):
        pass
    return default or path.name


def set_run_name(results_dir, run_dir, name):
    _, target = _run_path(results_dir, run_dir)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Enter a run name.")
    name = name.strip()
    if len(name) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("Use at most 80 characters without control characters.")

    metadata = target / META_FILE
    if metadata.resolve().parent != target or metadata.is_symlink():
        raise ValueError("The metadata file must remain inside the run directory.")
    temporary = target / f".ui_meta_{uuid4().hex}.tmp"
    created = False
    try:
        with temporary.open("x", encoding="utf-8") as file:
            created = True
            json.dump({"display_name": name}, file, ensure_ascii=False, indent=2)
        temporary.replace(metadata)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def trash_run(results_dir, run_dir, active_run=None):
    root, target = _run_path(results_dir, run_dir)
    if active_run and Path(active_run).resolve() == target:
        raise ValueError("A running task cannot be deleted.")

    # 与工作流共用同一个互斥锁，避免检查后、移动前任务恰好启动。
    token = "ui-delete:" + uuid4().hex
    lock = target / LOCK_FILE
    try:
        with lock.open("x", encoding="utf-8") as file:
            file.write(token)
    except FileExistsError:
        raise ValueError("This run is active or locked and cannot be deleted.") from None

    moved = False
    destination = None
    try:
        trash = _trash_path(root)
        destination = trash / f"{target.name}__{uuid4().hex[:12]}"
        # 移动前核对源目录和目标目录均处于明确的 results 范围内。
        if target.parent != root or destination.resolve().parent != trash:
            raise ValueError("The move would leave the results directory.")
        target.rename(destination)
        moved = True
        return {"original_name": target.name, "trash_path": str(destination)}
    finally:
        owned_lock = (destination if moved else target) / LOCK_FILE
        if owned_lock.exists() and owned_lock.read_text(encoding="utf-8") == token:
            owned_lock.unlink()


def restore_run(results_dir, record):
    root = Path(results_dir).resolve()
    trash = _trash_path(root)
    source = Path(record["trash_path"])
    name = record["original_name"]
    if not isinstance(name, str) or Path(name).name != name or name.startswith("."):
        raise ValueError("The original directory name is invalid.")
    if not source.is_dir() or _is_link(source) or source.resolve().parent != trash:
        raise ValueError("The deleted run was not found in this results directory.")
    destination = (root / name).resolve()
    if destination.parent != root or destination.exists():
        raise ValueError("The original location is unavailable; no files were overwritten.")
    if (source / LOCK_FILE).exists():
        raise ValueError("The deleted directory is locked.")
    source.rename(destination)
    return destination
