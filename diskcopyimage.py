# https://www.discferret.com/wiki/Apple_DiskCopy_4.2

from ctypes import BigEndianStructure, c_byte, c_char, c_uint32, c_uint16
from typing import BinaryIO


NAME_SIZE = 63


class DiskCopyImageHeader(BigEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('name_length',  c_byte),
        ('name',         c_char * NAME_SIZE),
        ('data_size',    c_uint32),
        ('tag_size',     c_uint32),
        ('data_checksum', c_uint32),
        ('tag_checksum', c_uint32),
        ('disk_type',    c_byte),
        ('format',       c_byte),
        ('magic_number', c_uint16),
    ]

    def read_data(self, f: BinaryIO) -> bytes:
        if self.magic_number != 0x0100:
            raise ValueError('Invalid Magic Number')

        data = f.read(self.data_size)
        if len(data) != self.data_size:
            raise ValueError('Unexpected EOF')

        # TODO: Checksum verification?
        return data

    @property
    def image_name(self) -> bytes:
        return self.name[:min(self.name_length, NAME_SIZE)]
