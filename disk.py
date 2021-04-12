# https://developer.apple.com/library/archive/documentation/mac/pdf/Devices/SCSI_Manager.pdf

import configparser
from ctypes import BigEndianStructure, c_uint16, c_uint32, c_char
from typing import BinaryIO, List

import machfs


class DriverIni(object):
    def __init__(self):
        self.partition_type = b'Apple_Driver43'
        self.partition_flags = 0
        self.booter = 0
        self.bytes = 0
        self.load_address_0 = 0
        self.load_address_1 = 0
        self.goto_address_0 = 0
        self.goto_address_1 = 0
        self.checksum = 0
        self.processor = b'68000'
        self.boot_args: List[int] = []


def driver_from_ini(section) -> DriverIni:
    ini = DriverIni()
    ini.partition_type = bytes(section['partition_type'], encoding='ascii')
    ini.partition_flags = int(section['partition_flags'])
    ini.booter = int(section['booter'])
    ini.bytes = int(section['bytes'])
    ini.load_address_0 = int(section['load_address_0'], 16)
    ini.load_address_1 = int(section['load_address_1'], 16)
    ini.goto_address_0 = int(section['goto_address_0'], 16)
    ini.goto_address_1 = int(section['goto_address_1'], 16)
    ini.checksum = int(section['checksum'], 16)
    ini.processor = bytes(section['processor'], encoding='ascii')
    ini.boot_args = [int(x, 0) for x in section['boot_args'].split(',')]
    return ini


class DriverDescriptor(BigEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('ddBlock', c_uint32),
        ('ddSize',  c_uint16),
        ('ddType',  c_uint16),
    ]


class Block0(BigEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('sbSig',       c_uint16),
        ('sbBlkSize',   c_uint16),
        ('sbBlkCount',  c_uint32),

        # Dev Type and Dev Id both have no information from apple.
        # Apple's hfdisk utility assigns zero for both, but System 6's disk utility sets these both to 1.
        # I have no idea if these fields are used at all.
        ('sbDevType',   c_uint16),
        ('sbDevId',     c_uint16),

        # Reserved. Seems to be unused by anything.
        ('sbData',      c_uint32),

        ('sbDrvrCount', c_uint16),
        ('ddDrivers',   DriverDescriptor * 61),

        ('_pad1', c_uint32),
        ('_pad2', c_uint16)
    ]

    def __init__(self):
        super().__init__()
        self.sbSig = 0x4552  # sbSIGWord magic number.


class PartitionMapBlock(BigEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('dpme_signature',       c_uint16),
        ('dpme_sigPad',          c_uint16),
        ('dpme_map_entries',     c_uint32),
        ('dpme_pblock_start',    c_uint32),
        ('dpme_pblocks',         c_uint32),
        ('dpme_name',            c_char * 32),
        ('dpme_type',            c_char * 32),
        ('dpme_lblock_start',    c_uint32),
        ('dpme_lblocks',         c_uint32),

        # Apple Docs say this is only used by A/UX. That is not 100% true.
        ('dpme_flags',           c_uint32),

        # Note: Below data appears to only be used for SCSI Driver partitions
        ('dpme_boot_block',      c_uint32),
        ('dpme_boot_bytes',      c_uint32),
        ('dpme_load_addr',       c_uint32),
        ('dpme_load_addr_2',     c_uint32),
        ('dpme_goto_addr',       c_uint32),
        ('dpme_goto_addr_2',     c_uint32),
        ('dpme_checksum',        c_uint32),
        ('dpme_process_id',      c_char * 16),
        ('dpme_boot_args',       c_uint32 * 32),
        ('dpme_reserved_3',      c_uint32 * 62)
    ]

    def __init__(self):
        super().__init__()
        self.dpme_signature = 0x504d  # "PM"


def create_basic_partition(name, type, start_block, block_count, flags) -> PartitionMapBlock:
    block = PartitionMapBlock()
    block.dpme_pblock_start = start_block
    block.dpme_pblocks = block_count
    block.dpme_name = name
    block.dpme_type = type
    block.dpme_lblocks = block_count
    block.dpme_flags = flags
    return block


def create_partition_map_partition() -> PartitionMapBlock:
    block = PartitionMapBlock()
    block.dpme_pblock_start = 1
    block.dpme_pblocks = 63
    block.dpme_name = b'Apple'
    block.dpme_type = b'Apple_partition_map'
    block.dpme_lblocks = 63
    return block


def create_driver_partition_block(info: DriverIni, start_block: int, block_count: int) -> PartitionMapBlock:
    block = PartitionMapBlock()
    block.dpme_pblock_start = start_block
    block.dpme_pblocks = block_count
    block.dpme_name = b'Macintosh'
    block.dpme_type = info.partition_type
    block.dpme_lblocks = block_count
    block.dpme_flags = info.partition_flags
    block.dpme_boot_block = info.booter
    block.dpme_boot_bytes = info.bytes
    block.dpme_load_addr = info.load_address_0
    block.dpme_load_addr_2 = info.load_address_1
    block.dpme_goto_addr = info.goto_address_0
    block.dpme_goto_addr_2 = info.goto_address_1
    block.dpme_checksum = info.checksum
    block.dpme_process_id = info.processor
    for i, value in enumerate(info.boot_args):
        block.dpme_boot_args[i] = value
    return block


def create_bootable_disk(of: BinaryIO, volume: machfs.Volume, block_count: int):
    """

    :param of:
    :param volume:
    :param block_count: Total blocks in the disk, including blocks used by block0, partition map, and all partitions.
    :return:
    """

    driver_ini = configparser.ConfigParser()
    driver_ini.read('driver.ini')
    driver_info = driver_from_ini(driver_ini['Driver'])

    block0 = Block0()
    block0.sbBlkSize = 512
    block0.sbBlkCount = block_count

    block0.sbDrvrCount = 1
    descriptor = DriverDescriptor()
    descriptor.ddBlock = 64
    descriptor.ddSize = int(driver_info.bytes / int(512) + (1 if driver_info.bytes % int(512) != 0 else 0))
    descriptor.ddType = 1  # Always 1
    block0.ddDrivers[0] = descriptor

    # Set these both to 1, just in case. See comment in Block0 class.
    # TODO: Once we get a booting disk on a real MacPlus, try removing and see if it still works.
    block0.sbDevType = 1
    block0.sbDevId = 1

    block0_bytes = bytes(block0)
    if len(block0_bytes) != 512:
        raise ValueError('ASSERTION FAILED! sizeof(Block0) != 512')
    of.write(block0_bytes)

    def write_partition_map_block(block: PartitionMapBlock):
        block_bytes = bytes(block)
        if len(block_bytes) != 512:
            raise ValueError('ASSERTION FAILED! sizeof(PartitionMapBlock) != 512')
        of.write(block_bytes)

    volume_offset = 64 + 32  # Block0 + Partition Map + Driver
    volume_block_count = block_count - volume_offset

    partition_map_0 = create_basic_partition(
        name=b'MacOS',
        type=b'Apple_HFS',
        start_block=volume_offset,
        block_count=volume_block_count,
        flags=0)
    partition_map_0.dpme_map_entries = 3
    write_partition_map_block(partition_map_0)

    partition_map_1 = create_partition_map_partition()
    partition_map_1.dpme_map_entries = 3
    write_partition_map_block(partition_map_1)

    partition_map_2 = create_driver_partition_block(driver_info, 64, 32)
    partition_map_2.dpme_map_entries = 3
    write_partition_map_block(partition_map_2)

    # Write empty partition map entries
    empty_block = b'\0' * 512
    for i in range(1 + 3, 64):  # 3 is partition map block count TODO: Kill all magic numbers
        of.write(empty_block)

    # Write Driver
    with open('driver.bin', 'rb') as f:
        for i in range(32):

            of.write(f.read(512))

    # Write HFS Volume
    volume_data = volume.write(
        size=volume_block_count * 512,
        # desktopdb=False,
        bootable=False
    )

    if len(volume_data) != volume_block_count * 512:
        raise ValueError('ASSERTION FAILED! len(volume_data) != volume_block_count * 512')
    of.write(volume_data)

    if of.tell() != block_count * 512:
        raise ValueError('Error! Output file is not expected size!')


def mb_block_count(mb: int) -> int:
    kb = mb * 1024
    return kb * 2  # 2 512-byte blocks = 1 kb.
