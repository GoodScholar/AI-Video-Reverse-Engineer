"""Stage history files so failed metadata writes can restore their locations."""
import shutil
import stat
from uuid import uuid4


def inventory(paths):
    size = 0
    count = 0
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        entries = [path] + list(path.rglob('*')) if path.is_dir() and not path.is_symlink() else [path]
        for entry in entries:
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError('历史文件包含不安全的路径，不能自动清理。')
            if stat.S_ISREG(mode):
                size += entry.stat().st_size
                count += 1
    return size, count


def remove_history_files(paths, parent, commit):
    inventory(paths)
    stage = parent / ('cleanup-' + uuid4().hex)
    moved = []
    stage.mkdir()
    try:
        for index, path in enumerate(paths):
            if path.exists():
                target = stage / str(index)
                path.rename(target)
                moved.append((path, target))
        commit()
    except BaseException:
        for original, target in reversed(moved):
            target.rename(original)
        stage.rmdir()
        raise
    try:
        shutil.rmtree(stage)
    except OSError:
        return '记录已移除，但部分暂存文件未能释放；请检查本地存储权限。'
    return None
