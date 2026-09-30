"""Machinedrum OS update SysEx: 64-byte packets, 2+7+7 packing, 0x4000 origin.

The payload starts at flash 0x4000; it is not a whole-device dump. See
docs/05-sysex-updates.md. Protocol cross-checked against
mischa85/elektron-firmware-tool (transport.c, commit a5bce9a6); independent code.
"""
import hashlib

from .image import discover_blobs


class SysexError(ValueError):
    pass


def sha(data):return hashlib.sha256(data).hexdigest()


PREFIX=b'\xf0\x00\x20\x3c\x02\x00'
START=0x4000
END=0x100000
IMAGE_SIZE=0x800000
PACKET=64
EMPTY=PREFIX+b'\x7e\xf7'


def sha(data):return hashlib.sha256(data).hexdigest()


def nibbles(value):
    if not 0<=value<1<<24:raise SysexError('Value outside 24-bit transport range')
    return bytes((value>>shift)&15 for shift in range(20,-1,-4))


def from_nibbles(data):
    if len(data)!=6 or any(v>15 for v in data):raise SysexError('Invalid six-nibble field')
    value=0
    for v in data:value=(value<<4)|v
    return value


def checksum(address,data):
    n=nibbles(address)
    return (sum(data)+n[1]+(n[2]<<4)+n[3]+((n[4]&12)<<4))&255


def pack_words(data):
    result=bytearray()
    for i in range(0,len(data),2):
        word=data[i]<<8 | (data[i+1] if i+1<len(data) else 0)
        result.extend((word>>14,(word>>7)&127,word&127))
    return bytes(result)


def unpack_words(data):
    if not data or len(data)%3:raise SysexError('Invalid 2+7+7 payload length')
    result=bytearray()
    for i in range(0,len(data),3):
        a,b,c=data[i:i+3]
        if a>3 or b>127 or c>127:raise SysexError('Invalid 2+7+7 word')
        result.extend(((a<<14)|(b<<7)|c).to_bytes(2,'big'))
    return bytes(result)


def encode_payload(payload):
    if not 0<len(payload)<=END-START:raise SysexError('Update payload must fit flash 0x4000..0xFFFFF')
    result=bytearray()
    for offset in range(0,len(payload),PACKET):
        chunk=payload[offset:offset+PACKET];address=START+offset;check=checksum(address,chunk)
        result.extend(PREFIX+b'\x7e'+bytes((check>>4,check&15))+nibbles(address)+pack_words(chunk)+b'\xf7')
    result.extend(EMPTY)
    result.extend(PREFIX+b'\x7f'+nibbles(len(payload))+b'\xf7')
    return bytes(result)


def decode_sysex(data):
    """Strict OS-file parser: ordered packets, checksums and exactly two end records."""
    # Maximum supported payload occupies 16128 full packets plus the two markers.
    if not data or len(data)>((END-START+63)//64)*112+22:
        raise SysexError('Empty or oversized firmware SysEx file')
    pos=0;decoded=bytearray();packets=0;empty=False;finished=False;last_size=0
    while pos<len(data):
        stop=data.find(b'\xf7',pos)
        if stop<0:raise SysexError('Truncated SysEx message')
        msg=data[pos:stop+1];pos=stop+1
        if not msg.startswith(PREFIX) or len(msg)<8 or any(v>127 for v in msg[1:-1]):
            raise SysexError('Invalid framing, device, manufacturer or MIDI data byte')
        command=msg[6]
        if finished:raise SysexError('Trailing data after end marker')
        if msg==EMPTY:
            if empty or not packets:raise SysexError('Unexpected empty data terminator')
            empty=True
        elif command==0x7f:
            if len(msg)!=14 or not empty:raise SysexError('Invalid/misordered final length marker')
            length=from_nibbles(msg[7:-1])
            # Word packing can add one zero byte, whose checksum contribution is zero.
            if length!=len(decoded):
                if length!=len(decoded)-1 or not length%2 or decoded[-1]!=0:
                    raise SysexError('Final payload length mismatch')
                decoded.pop()
            if not 0<len(decoded)<=END-START:raise SysexError('Invalid update length')
            finished=True
        elif command==0x7e:
            if empty or len(msg)<19 or len(msg)>112 or (packets and last_size!=PACKET):
                raise SysexError('Invalid data packet size/order')
            if msg[7]>15 or msg[8]>15:raise SysexError('Invalid checksum nibbles')
            address=from_nibbles(msg[9:15])
            if address!=START+len(decoded):raise SysexError('Out-of-order, duplicate or missing packet address')
            chunk=unpack_words(msg[15:-1])
            if len(chunk)>PACKET or address+len(chunk)>END:raise SysexError('Packet outside supported update region')
            if (msg[7]<<4|msg[8])!=checksum(address,chunk):raise SysexError(f'Packet checksum mismatch at 0x{address:x}')
            decoded.extend(chunk);packets+=1;last_size=len(chunk)
        else:raise SysexError(f'Unsupported firmware command 0x{command:02x}')
    if not finished:raise SysexError('Missing empty terminator/final length marker')
    payload=bytes(decoded)
    return payload,{'packets':packets,'messages':packets+2,'payload_bytes':len(payload),
                    'start':START,'end_exclusive':START+len(payload),'payload_sha256':sha(payload),
                    'sysex_sha256':sha(data),'packet_checksums_valid':True}


def image_payload(image):
    if len(image)!=IMAGE_SIZE:raise SysexError('Expected an 8 MiB firmware image')
    blobs,_=discover_blobs(image)
    chain_end=blobs['OS_B']['end']
    if chain_end>END:raise SysexError('Blob chain exceeds OS flash region')
    # Include non-FF custom structures after the compressed chain, e.g. FF000.
    # Do not strip a trailing FF byte that belongs to the final compressed blob.
    used=len(image[START:END].rstrip(b'\xff'))+START
    return image[START:max(chain_end,used)]


def apply_payload(payload,base):
    if len(base)!=IMAGE_SIZE:raise SysexError('Base image must be exactly 8 MiB')
    if not 0<len(payload)<=END-START:raise SysexError('Invalid update payload extent')
    result=bytearray(base)
    result[START:START+len(payload)]=payload
    # Bytes not carried by the file are intentionally preserved, not invented.
    return bytes(result)


