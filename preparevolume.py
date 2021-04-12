
# How to get HFS file type and creator data from the rsrc:
# http://bitsavers.org/pdf/apple/mac/Inside_Macintosh_Promotional_Edition_1985.pdf
# http://mirror.informatimago.com/next/developer.apple.com/documentation/mac/MoreToolbox/MoreToolbox-9.html
# https://developer.apple.com/library/archive/documentation/mac/pdf/MacintoshToolboxEssentials.pdf
# ^ See FInfo and FXInfo

import argparse
import os
import subprocess
import traceback
from typing import Dict, Tuple, Union, Optional

import machfs
import rsrcfork
from machfs.directory import AbstractFolder
from progress.bar import Bar

import appledouble
import applicationutil
import diskcopyimage
import disk

DEFAULT_BLOCK_TARGET = int((1024 * 1024 * 1024 * 1) / 512)

argparser = argparse.ArgumentParser()
argparser.add_argument('dl_folder')
argparser.add_argument('sit_dir')
argparser.add_argument('--target-blocks', type=int, default=DEFAULT_BLOCK_TARGET, help=f'Target size in 512 byte blocks. Default is {DEFAULT_BLOCK_TARGET} (1GiB)')
argparser.add_argument('--volume-start-index', type=int, default=0)
argparser.add_argument('--hfs-internals-ratio', type=float, default=0.85)
argparser.add_argument('--verbose', '-v', action='store_true')

args = argparser.parse_args()


class FilterException(Exception):
    pass


class PreparationIssue(Exception):
    pass


def sanitize_hfs_name(name: bytes, is_folder: bool) -> bytes:
    if len(name) < 1:
        raise ValueError('Invalid empty hfs name')
        # name = b' '

    val = name.replace(b':', b'?')
    if is_folder:
        return val[:17]
    else:
        return val[:31]


def sanitize_hfs_name_str(name: str, is_folder: bool) -> bytes:
    return sanitize_hfs_name(name.encode('mac_roman', errors='replace'), is_folder=is_folder)


def get_hfs_file_size(file: machfs.File) -> int:
    def block_align(size: int) -> int:
        return (int(size / 512) + (1 if size % 512 != 0 else 0)) * 512

    # Guessing 1K of Filesystem Junk for each file
    return block_align(len(file.data)) + block_align(len(file.rsrc))  # + 1024


def add_disk_data(containing_folder: AbstractFolder, path: str, data: bytes) -> int:
    dsk_volume = machfs.Volume()

    try:
        dsk_volume.read(data)
    except Exception:
        traceback.print_exc()
        print(f'Issue reading file system from disk image at path: {path}. Skipping.')
        return 0

    for name, child in dsk_volume.items():
        containing_folder[name] = child

    total_bytes = 0

    for path_tuple, dirnames, filenames in containing_folder.walk():
        current_folder = containing_folder[path_tuple] if len(path_tuple) > 0 else containing_folder
        for file in filenames:
            current_file = current_folder[file]
            total_bytes += get_hfs_file_size(current_file)

    return total_bytes


def add_dsk(path: str) -> Tuple[machfs.Folder, bytes, int]:
    if args.verbose:
        print(f'* Adding DSK image at {path}')

    base_path, dsk_filname = os.path.split(path)
    folder_name, _ = os.path.splitext(dsk_filname)
    dsk_folder = machfs.Folder()
    sanitized_folder_name = sanitize_hfs_name_str(folder_name, is_folder=True)

    with open(path, 'rb') as f:
        flat = f.read()

    return dsk_folder, sanitized_folder_name, add_disk_data(dsk_folder, path, flat)


def add_img(path: str) -> Tuple[machfs.Folder, bytes, int]:
    if args.verbose:
        print(f'* Adding DiskCopy image at {path}')

    header = diskcopyimage.DiskCopyImageHeader()
    with open(path, 'rb') as f:
        f.readinto(header)
        try:
            data = header.read_data(f)
        except ValueError as e:
            raise PreparationIssue(f'Error reading DiskCopy file at {path}: {e}')

    disk_folder = machfs.Folder()
    folder_name = header.image_name

    if len(folder_name) < 1:
        _, filename = os.path.split(path)
        folder_name = sanitize_hfs_name_str(os.path.splitext(filename)[0], is_folder=True)
    else:
        folder_name = sanitize_hfs_name(folder_name, is_folder=True)

    return disk_folder, folder_name, add_disk_data(disk_folder, path, data)


def add_file(path: str) -> Optional[Tuple[Union[machfs.Folder, machfs.File], bytes, int]]:
    base_path, filename = os.path.split(path)
    base_filename, ext = os.path.splitext(filename)

    has_data_file = True

    if filename == '.DS_Store':
        return None

    if ext == '.dmg':
        raise FilterException('Contains an OSX DMG')

    if ext == '.rsrc':
        if not os.path.isfile(os.path.join(base_path, base_filename)):
            has_data_file = False
        else:
            # Skip .rsrc files, we handle resource forks while adding each normal file.
            return None

    # Try to Mount DiskCopy images
    if ext == '.img' or ext == '.image':
        return add_img(path)

    # Expand sit files
    if ext == '.sit':
        return add_sit(path)

    # Expand dsk files
    if ext == '.dsk':
        return add_dsk(path)

    file = machfs.File()

    if has_data_file:
        with open(path, 'rb') as f:
            size = f.seek(0, 2)
            if size > 1024 * 1024 * 5:  # >5 MiB, TODO: MAKE THIS TUNABLE
                raise FilterException('Contains a file that is greater than 5 MiB')
            f.seek(0)
            file.data = f.read()
        rsrc_path = path + '.rsrc'
    else:
        rsrc_path = path

    if os.path.isfile(rsrc_path):
        with open(rsrc_path, 'rb') as f:
            try:
                double = appledouble.parse(f)
            except ValueError:
                double = None

        if double:
            rsrc_entry = double.get_entry(appledouble.EntryType.resource_fork)
            if rsrc_entry:
                file.rsrc = rsrc_entry.data

            finder_entry = double.get_entry(appledouble.EntryType.finder_info)
            if finder_entry:
                file.type = bytes(finder_entry.data.fdType)
                file.creator = bytes(finder_entry.data.fdCreator)
                file.flags = finder_entry.data.fdFlags
                file.x = finder_entry.data.fdLocation.x
                file.y = finder_entry.data.fdLocation.y

            try:
                supported_archs = applicationutil.get_supported_archs(file.rsrc)
            except rsrcfork.api.InvalidResourceFileError:
                print(f'Warning: Unable to parse resource fork from AppleDouble file at {rsrc_path}')
                supported_archs = []

            if len(supported_archs) > 0 and applicationutil.ARCH_68K not in supported_archs:
                raise FilterException('Found a non-68k executable.')

    if args.verbose:
        print(f'* Adding file at path {path}')

    hfs_filename = filename if has_data_file else base_filename
    sanitized_name = sanitize_hfs_name_str(hfs_filename, is_folder=False)
    return file, sanitized_name, get_hfs_file_size(file)


def add_files(root: AbstractFolder, path: str) -> int:
    path_map: Dict[str, AbstractFolder] = {path: root}

    total_bytes = 0

    for dirpath, dirnames, filenames in os.walk(path):
        containing_folder: AbstractFolder = path_map[dirpath]

        for dirname in dirnames:
            _, dirname_ext = os.path.splitext(dirname)
            if dirname_ext == '.app':
                raise FilterException(".app directory detected")

            dirname_path = os.path.join(dirpath, dirname)
            hfs_dir = machfs.Folder()
            clean_dirname = sanitize_hfs_name_str(dirname, is_folder=True)
            containing_folder[clean_dirname] = hfs_dir
            path_map[dirname_path] = hfs_dir

        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            result = add_file(filepath)
            if not result:
                continue
            file, hfs_filename, file_bytes = result
            containing_folder[hfs_filename] = file
            total_bytes += file_bytes

    return total_bytes


def add_sit(path: str) -> Tuple[machfs.Folder, bytes, int]:
    _, filename = os.path.split(path)
    folder_name, _ = os.path.splitext(filename)
    output_dir = os.path.join(args.sit_dir, folder_name)
    result = subprocess.run([
        'unar',
        '-o', args.sit_dir,
        '-s',  # Skip files which exist
        '-d',  # Force directory,
        '-p', '',  # Always use blank password
        '-q',  # Quiet
        '-forks', 'visible',
        path
    ])
    if result.returncode != 0:
        raise PreparationIssue(f'There was an error extracting {path}')

    folder = machfs.Folder()
    folder_name = sanitize_hfs_name_str(folder_name, is_folder=True)
    return folder, folder_name, add_files(folder, output_dir)


def sizeof_fmt(num, suffix='B'):
    """https://stackoverflow.com/a/1094933/594760"""
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


class CoolBar(Bar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bytes_taken = 0

    @property
    def human_readable_bytes(self):
        return sizeof_fmt(self.bytes_taken)


def to_blocks(byte_count: int) -> int:
    return int(byte_count / 512) + (1 if byte_count % 512 != 0 else 0)


class VolumeManager:
    def __init__(self, start_index: int):
        self.volume_index = start_index
        self.bytes_taken = 0
        self.volume = machfs.Volume()

    def write_volume(self):
        self.volume.name = f'Pimp My Plus #{self.volume_index}'

        volume_output_path = f'collection.{self.volume_index}.scsi'
        print(f'Writing Volume to {volume_output_path} with {args.target_blocks} blocks')
        with open(volume_output_path, 'wb') as f:
            disk.create_bootable_disk(f, self.volume, args.target_blocks)

        self.volume_index += 1
        self.volume = machfs.Volume()
        self.bytes_taken = 0


files = os.listdir(args.dl_folder)
files.sort(key=str.casefold)
volume_manager = VolumeManager(args.volume_start_index)

# Extra blocks are taken by the filesystem when writing the volume.
# In the future, we could be more smart about this (Do bookkeeping when adding files to the volume, might require modifying machfs library)
# For now, just cheese it and calculate usable space using a ratio of file data to filesystem data.
target_blocks_per_volume = int(args.target_blocks * args.hfs_internals_ratio) - 96
target_size = target_blocks_per_volume * 512


with CoolBar(max=len(files), suffix='%(percent)d%% -- %(index)d / %(max)d -- ~%(human_readable_bytes)s') as progress:
    progress.start()

    for file in files:
        path = os.path.join(args.dl_folder, file)
        result = None
        try:
            result = add_file(path)
        except (PreparationIssue, FilterException) as e:
            print(e)

        if result:
            result_file, result_filename, bytes_taken = result

            # If this new entry would cause us to go over, write the current volume out and start a new volume.
            if volume_manager.bytes_taken + bytes_taken > target_size:
                print('Reached target size, writing a volume.')
                volume_manager.write_volume()
                progress.bytes_taken = 0

            volume_manager.volume[result_filename] = result_file
            print(f'\n* Added {file} (~{sizeof_fmt(bytes_taken)})')
            progress.bytes_taken += bytes_taken
            volume_manager.bytes_taken += bytes_taken

        progress.next()

    volume_manager.write_volume()

print('Done!')
