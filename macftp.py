import os
import ftplib
import json
import argparse

# https://macintoshgarden.org/forum/public-access-file-repository
FTP_URL = 'repo1.macintoshgarden.org'
FTP_USER = 'macgarden'
FTP_PASS = 'publicdl'

DIR = 'Garden/apps'

OUTPUT_DIR = os.path.join('./macdl', DIR)

ALLOWED_EXTENSIONS = set(('.sit', '.dsk'))
MAX_SIZE = 1024 * 1024 * 10  # 10 MB

JSON_LIST_FILENAME = 'items.json'


argparser = argparse.ArgumentParser()
argparser.add_argument('--cached-list', help='Use cached listing of files', action='store_true')


def parse_item(line):
    parts = [x for x in line.split(' ') if len(x) > 0]
    size = parts[4]
    name = parts[8]
    return int(size), name


def should_include(size, name):
    _, ext = os.path.splitext(name)
    return size <= MAX_SIZE and ext in ALLOWED_EXTENSIONS


def exists(local_path, size, name):
    local_path = os.path.join(OUTPUT_DIR, name)
    if not os.path.isfile(local_path):
        return False
    return os.path.getsize(local_path) == size

def download(ftp, size, name):
    local_path = os.path.join(OUTPUT_DIR, name)
    
    if exists(local_path, size, name):
        print(f'File already downloaded: {name} ({size})')
        return

    print(f'Downloading {name} ({size})')
    with open(local_path, 'wb') as f:
        result = ftp.retrbinary('RETR ' + name, f.write)
    print(f'  result: {result}')


args = argparser.parse_args()

try:
    print('Connecting to FTP...')
    ftp = ftplib.FTP(FTP_URL, user=FTP_USER, passwd=FTP_PASS)
    print(f'Setting FTP directory to {DIR}')
    ftp.cwd(DIR)

    items = []
    all_items = []

    def add_item(item):
        size, name = item
        all_items.append(item)

        will_include = should_include(size, name)
        print(f'{"+" if will_include else "-"} {name} ({size})')
        
        if will_include:
            items.append(item)

    def item_callback(line):
        item = parse_item(line)
        add_item(item)

    if not args.cached_list:
        ftp.retrlines('LIST', callback=item_callback)

        print('Saving all_items as JSON...')
        with open(JSON_LIST_FILENAME, 'w') as f:
            json.dump(all_items, f)
    else:
        print('Loading items from JSON...')
        with open(JSON_LIST_FILENAME) as f:
            cached_items = json.load(f)

        for item in cached_items:
            add_item(item)

    print('Downloading items')

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for size, name in items:
        download(ftp, size, name)

    print('done!')

finally:
    print('Calling ftp.quit()')
    ftp.quit()
