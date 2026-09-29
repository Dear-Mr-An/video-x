# -*- coding: utf-8 -*-
"""视频帧混合处理引擎 —— 核心算法实现

算法规格（黑盒 + 逆向结果）:
- 输出 60fps, 总帧数 = 素材时长 * 60
- 画布为指定分辨率, 源视频按画布宽度等比缩放, 垂直居中(黑边)
- 帧序列:
    * 前 intro 帧(默认23): 素材帧 i//2 (30fps->60fps 2x 上采样)
    * 之后每 cycle=(live+mat) 帧循环: 前 live 帧为实拍, 后 mat 帧为素材
      - 实拍帧号 = floor(n * rate + 0.25) mod N_head, rate = cycle/2/live
      - 素材帧号 = i//2 (与输出时间 1:1)
- 音频: 素材音轨 -> AAC 44100 stereo (素材无音轨则静音)
- 视频编码: h264_nvenc cbr, Main 3.2, GOP 18, B帧3
- 时间轴伪装(与目标程序一致):
    * stts: (N, 512)  30fps 声明
    * ctts: v1, ctts[i] = display_pts[i] - 512*i + 768
    * elst: media_time = 768
    * video mdhd duration = last_pts + 768
    * SPS: HRD bit_rate 改写为 10M, cbr_flag=0
    * btrt: buffer/avg 清零
    * 编码器标记 -> avc1 compressor name
    * 封装时间   -> mvhd/tkhd/mdhd duration 伪装
    * 魔法大小   -> mdat size 字段 = 值 * 1_000_000
    * 清除 SEI   -> 移除全部 SEI NAL
"""

import json
import os
import re
import struct
import subprocess
import sys

VIDEO_EXTS = ('.mp4', '.mov', '.mkv', '.avi', '.m4v', '.webm', '.flv', '.ts', '.mts', '.m2ts', '.mpeg', '.mpg', '.wmv')

DEFAULT_COMPRESSOR = 'Lavc h264_nvenc'
MODE_SUFFIX = '_X'
HRD_BITRATE_MINUS1 = 156249   # 10 Mbps (与目标程序 VUI 一致)
BTRT_MAX_BITRATE = 10000000
VIDEO_BITRATE = '6890k'
TIMELINE_STTS_DELTA = 512
TIMELINE_MEDIA_TIME = 768


def _find_tool(name):
    bases = []
    if getattr(sys, 'frozen', False):
        bases.append(os.path.dirname(os.path.abspath(sys.executable)))
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            bases.append(meipass)
    base = os.path.dirname(os.path.abspath(__file__))
    bases.append(base)
    bases.append(os.path.dirname(base))
    for b in bases:
        for rel in (os.path.join('tools', name + '.exe'), os.path.join('tools', name),
                    os.path.join('123', name + '.exe')):
            cand = os.path.join(b, rel)
            if os.path.exists(cand):
                return cand
    return name


FFMPEG = _find_tool('ffmpeg')
FFPROBE = _find_tool('ffprobe')

# Windows 下隐藏子进程控制台窗口(打包为无控制台程序时避免黑屏弹窗)
_NO_WINDOW_KW = {'creationflags': 0x08000000} if os.name == 'nt' else {}

_NVENC_OK = None


def detect_nvenc():
    """检测本机 NVENC 是否可用(结果缓存)"""
    global _NVENC_OK
    if _NVENC_OK is None:
        try:
            r = _run([FFMPEG, '-v', 'error', '-f', 'lavfi',
                      '-i', 'color=black:s=256x256:d=0.05',
                      '-c:v', 'h264_nvenc', '-f', 'null', '-'])
            _NVENC_OK = (r.returncode == 0)
        except Exception:
            _NVENC_OK = False
    return _NVENC_OK


def resolve_encoder(mode):
    """auto -> 按可用性选择; 返回 ffmpeg 编码器名"""
    if mode == 'nvenc':
        return 'h264_nvenc'
    if mode == 'cpu':
        return 'libx264'
    return 'h264_nvenc' if detect_nvenc() else 'libx264'


def _run(cmd, **kw):
    kw.setdefault('stdout', subprocess.PIPE)
    kw.setdefault('stderr', subprocess.PIPE)
    kw.update(_NO_WINDOW_KW)
    return subprocess.run(cmd, **kw)


def probe_media(path):
    r = _run([FFPROBE, '-v', 'error', '-print_format', 'json',
              '-show_streams', '-show_format', path])
    if r.returncode != 0:
        raise RuntimeError('ffprobe failed: ' + r.stderr.decode('utf-8', 'replace')[:300])
    return json.loads(r.stdout.decode('utf-8', 'replace'))


def media_info(path):
    info = probe_media(path)
    v = None
    for s in info.get('streams', []):
        if s.get('codec_type') == 'video':
            v = s
            break
    if v is None:
        raise RuntimeError('no video stream: ' + path)
    dur = 0.0
    for src in (v.get('duration'), info.get('format', {}).get('duration')):
        try:
            if src is not None:
                dur = float(src)
                break
        except (TypeError, ValueError):
            continue
    fps = 30.0
    for key in ('avg_frame_rate', 'r_frame_rate'):
        fr = v.get(key) or ''
        if '/' in fr:
            a, b = fr.split('/')
            try:
                a, b = float(a), float(b)
                if b > 0 and a > 0:
                    fps = a / b
                    break
            except ValueError:
                pass
    has_audio = any(s.get('codec_type') == 'audio' for s in info.get('streams', []))
    return {
        'duration': dur,
        'fps': fps,
        'width': int(v.get('width') or 0),
        'height': int(v.get('height') or 0),
        'has_audio': has_audio,
    }


class FrameReader(object):
    """流式解码器: 按帧号顺序读取, 回退时重启解码进程"""

    def __init__(self, path, W, H):
        self.path = path
        self.W = W
        self.H = H
        self.frame_size = W * H * 3 // 2
        self.proc = None
        self.next_idx = 0

    def _start(self):
        vf = ('scale=%d:%d:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:'
              'out_color_matrix=bt709:out_range=tv,'
              'pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,'
              'setsar=1,format=yuv420p,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'
              ) % (self.W, self.H, self.W, self.H)
        cmd = [FFMPEG, '-v', 'error', '-i', self.path, '-vf', vf,
               '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-']
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_NO_WINDOW_KW)
        self.next_idx = 0

    def _read_frame(self):
        buf = self.proc.stdout.read(self.frame_size)
        if buf is None or len(buf) < self.frame_size:
            return None
        return buf

    def frame(self, idx):
        if self.proc is None or idx < self.next_idx:
            self.stop()
            self._start()
        last = None
        while self.next_idx <= idx:
            f = self._read_frame()
            if f is None:
                if last is None:
                    self.stop()
                    self._start()
                    f = self._read_frame()
                    if f is None:
                        raise RuntimeError('decode failed: ' + self.path)
                else:
                    f = last
                self.next_idx = idx + 1
                return f
            last = f
            self.next_idx += 1
        return last

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.stdout.close()
            except Exception:
                pass
            self.proc = None


def frame_plan(total, intro, live, mat, n_head, n_mat):
    """生成 (源, 帧号) 序列; 源 'H'=实拍 'M'=素材"""
    cycle = max(1, live + mat)
    rate = cycle / 2.0 / max(1, live)
    for i in range(total):
        if i < intro:
            yield ('M', min(i // 2, n_mat - 1))
            continue
        k = i - intro
        q, pos = divmod(k, cycle)
        if pos < live:
            n = q * live + pos
            h = int(n * rate + 0.25)
            yield ('H', h % max(1, n_head))
        else:
            yield ('M', min(i // 2, n_mat - 1))


# ================================================================ box 工具

def _iter_boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size = struct.unpack('>I', data[pos:pos + 4])[0]
        typ = bytes(data[pos + 4:pos + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack('>Q', data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            break
        yield pos, size, hdr, typ
        pos += size


def _find_box(data, path_types, start=0, end=None):
    if end is None:
        end = len(data)
    for pos, size, hdr, typ in _iter_boxes(data, start, end):
        if typ == path_types[0]:
            if len(path_types) == 1:
                return pos, size, hdr
            inner = pos + hdr
            if typ == b'meta':
                inner += 4
            return _find_box(data, path_types[1:], inner, pos + size)
    return None


def _traks(data):
    moov = _find_box(data, [b'moov'])
    if not moov:
        return [], None
    mpos, msize, mhdr = moov
    traks = [(p, s, h) for p, s, h, t in _iter_boxes(data, mpos + mhdr, mpos + msize) if t == b'trak']
    return traks, (mpos, msize, mhdr)


def _extract_avcc(data):
    """返回 (avcc_pos, avcc_size, sps_bytes, avcc_hdr)"""
    traks, moov = _traks(data)
    if not traks:
        return None
    tpos, tsize, thdr = traks[0]
    mdia = _find_box(data, [b'mdia'], tpos + thdr, tpos + tsize)
    if not mdia:
        return None
    minf = _find_box(data, [b'minf'], mdia[0] + mdia[2], mdia[0] + mdia[1])
    if not minf:
        return None
    stbl = _find_box(data, [b'stbl'], minf[0] + minf[2], minf[0] + minf[1])
    if not stbl:
        return None
    stsd = _find_box(data, [b'stsd'], stbl[0] + stbl[2], stbl[0] + stbl[1])
    if not stsd:
        return None
    spos, ssize, shdr = stsd
    for epos, esize, ehdr, etyp in _iter_boxes(data, spos + shdr + 8, spos + ssize):
        if etyp in (b'avc1', b'hvc1', b'hev1'):
            for cpos, csize, chdr, ctyp in _iter_boxes(data, epos + ehdr + 78, epos + esize):
                if ctyp == b'avcC':
                    body = data[cpos + chdr:cpos + csize]
                    sps_len = struct.unpack('>H', body[6:8])[0]
                    sps = bytes(body[8:8 + sps_len])
                    return cpos, csize, sps, chdr
    return None


# ================================================================ SPS 位级补丁

def _bits_from_bytes(b):
    bits = []
    for byte in b:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _bytes_from_bits(bits):
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | (bits[i + j] if i + j < len(bits) else 0)
        out.append(byte)
    return bytes(out)


def _read_bits(bits, pos, n):
    val = 0
    for _ in range(n):
        val = (val << 1) | bits[pos]
        pos += 1
    return val, pos


def _read_ue(bits, pos):
    zeros = 0
    while bits[pos] == 0:
        zeros += 1
        pos += 1
    pos += 1
    val = 0
    for _ in range(zeros):
        val = (val << 1) | bits[pos]
        pos += 1
    return (1 << zeros) - 1 + val, pos


def _write_ue(val):
    v = val + 1
    n = v.bit_length() - 1
    return [0] * n + [int(c) for c in bin(v)[2:]]


def _remove_emulation(b):
    return b.replace(b'\x00\x00\x03', b'\x00\x00')


def _add_emulation(b):
    out = bytearray()
    zeros = 0
    for byte in b:
        if zeros >= 2 and byte <= 3:
            out.append(3)
            zeros = 0
        out.append(byte)
        if byte == 0:
            zeros += 1
        else:
            zeros = 0
    return bytes(out)


def _find_sps_hrd(sps_eb):
    rbsp = _remove_emulation(sps_eb)
    body = rbsp[1:]
    bits = _bits_from_bytes(body)
    pos = 0
    profile, pos = _read_bits(bits, pos, 8)
    _, pos = _read_bits(bits, pos, 8)
    _, pos = _read_bits(bits, pos, 8)
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        return None
    _, pos = _read_ue(bits, pos)
    _, pos = _read_ue(bits, pos)
    poc, pos = _read_ue(bits, pos)
    if poc == 0:
        _, pos = _read_ue(bits, pos)
    elif poc == 1:
        _, pos = _read_bits(bits, pos, 1)
        _, pos = _read_ue(bits, pos)
        _, pos = _read_ue(bits, pos)
        n, pos = _read_ue(bits, pos)
        for _ in range(n):
            _, pos = _read_ue(bits, pos)
    _, pos = _read_ue(bits, pos)
    _, pos = _read_bits(bits, pos, 1)
    _, pos = _read_ue(bits, pos)
    _, pos = _read_ue(bits, pos)
    fm, pos = _read_bits(bits, pos, 1)
    if fm == 0:
        _, pos = _read_bits(bits, pos, 1)
    _, pos = _read_bits(bits, pos, 1)
    cr, pos = _read_bits(bits, pos, 1)
    if cr:
        for _ in range(4):
            _, pos = _read_ue(bits, pos)
    vui, pos = _read_bits(bits, pos, 1)
    if not vui:
        return None
    ari, pos = _read_bits(bits, pos, 1)
    if ari:
        idc, pos = _read_bits(bits, pos, 8)
        if idc == 255:
            _, pos = _read_bits(bits, pos, 16)
            _, pos = _read_bits(bits, pos, 16)
    ovs, pos = _read_bits(bits, pos, 1)
    if ovs:
        _, pos = _read_bits(bits, pos, 1)
    vst, pos = _read_bits(bits, pos, 1)
    if vst:
        _, pos = _read_bits(bits, pos, 3)
        _, pos = _read_bits(bits, pos, 1)
        cd, pos = _read_bits(bits, pos, 1)
        if cd:
            _, pos = _read_bits(bits, pos, 24)
    cl, pos = _read_bits(bits, pos, 1)
    if cl:
        _, pos = _read_ue(bits, pos)
        _, pos = _read_ue(bits, pos)
    ti, pos = _read_bits(bits, pos, 1)
    if ti:
        _, pos = _read_bits(bits, pos, 32)
        _, pos = _read_bits(bits, pos, 32)
        _, pos = _read_bits(bits, pos, 1)
    nh, pos = _read_bits(bits, pos, 1)
    if nh:
        cnt, pos = _read_ue(bits, pos)
        _, pos = _read_bits(bits, pos, 4)
        _, pos = _read_bits(bits, pos, 4)
        s = pos
        _, pos = _read_ue(bits, pos)
        e = pos
        _, cbr_pos = _read_ue(bits, pos)
        return bits, s, e, cbr_pos
    return None


def patch_sps_bytes(sps_eb, bitrate_minus1=HRD_BITRATE_MINUS1, cbr_flag=0):
    found = _find_sps_hrd(sps_eb)
    if not found:
        return None
    bits, s, e, cbr_pos = found
    new_bits = bits[:s] + _write_ue(bitrate_minus1) + bits[e:cbr_pos] + [cbr_flag] + bits[cbr_pos + 1:]
    if len(new_bits) % 8:
        new_bits += [0] * (8 - len(new_bits) % 8)
    rbsp = _bytes_from_bits(new_bits)
    return _add_emulation(b'\x67' + rbsp)


def _fix_chunk_offsets(data, moov, delta):
    """所有 trak 的 stco/co64 条目 +delta"""
    if not moov or delta == 0:
        return
    mpos, msize, mhdr = moov
    for tpos, tsize, thdr, ttyp in _iter_boxes(data, mpos + mhdr, mpos + msize):
        if ttyp != b'trak':
            continue
        for spath in ([b'mdia', b'minf', b'stbl', b'stco'], [b'mdia', b'minf', b'stbl', b'co64']):
            r = _find_box(data, spath, tpos + thdr, tpos + tsize)
            if not r:
                continue
            p2, s2, h2 = r
            cnt = struct.unpack('>I', data[p2 + h2 + 4:p2 + h2 + 8])[0]
            width = 4 if spath[-1] == b'stco' else 8
            fmt = '>I' if width == 4 else '>Q'
            base = p2 + h2 + 8
            for k in range(cnt):
                pk = base + k * width
                cur = struct.unpack(fmt, data[pk:pk + width])[0]
                struct.pack_into(fmt, data, pk, cur + delta)


def patch_sps_in_file(path):
    """改写 avcC 中 SPS 的 HRD 值; 处理 box 长度与 stco 偏移"""
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    info = _extract_avcc(data)
    if not info:
        return False
    avcc_pos, avcc_size, sps, hdr = info
    new_sps = patch_sps_bytes(sps)
    if not new_sps or new_sps == sps:
        return False
    delta = len(new_sps) - len(sps)
    if delta == 0:
        body_start = avcc_pos + hdr
        struct.pack_into('>H', data, body_start + 6, len(new_sps))
        data[body_start + 8:body_start + 8 + len(sps)] = new_sps
        with open(path, 'wb') as f:
            f.write(data)
        return True

    old = bytes(data[avcc_pos:avcc_pos + avcc_size])
    body = bytearray(old[hdr:])
    struct.pack_into('>H', body, 6, len(new_sps))
    body = body[:8] + new_sps + body[8 + len(sps):]
    new_avcc = struct.pack('>I', hdr + len(body)) + b'avcC' + bytes(body)

    # ancestors before avcc_pos (unaffected by splice)
    ancestors = []
    for path_types in ([b'moov'], [b'moov', b'trak'], [b'moov', b'trak', b'mdia'],
                       [b'moov', b'trak', b'mdia', b'minf'], [b'moov', b'trak', b'mdia', b'minf', b'stbl'],
                       [b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd']):
        r = _find_box(data, path_types)
        if r:
            ancestors.append(r[0])
    stsd = _find_box(data, [b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'])
    if stsd:
        spos, ssize, shdr = stsd
        for epos, esize, ehdr, etyp in _iter_boxes(data, spos + shdr + 8, spos + ssize):
            if etyp in (b'avc1', b'hvc1', b'hev1'):
                ancestors.append(epos)
                break

    data = data[:avcc_pos] + new_avcc + data[avcc_pos + avcc_size:]
    for apos in ancestors:
        cur = struct.unpack('>I', data[apos:apos + 4])[0]
        struct.pack_into('>I', data, apos, cur + delta)
    traks, moov = _traks(data)
    _fix_chunk_offsets(data, moov, delta)
    with open(path, 'wb') as f:
        f.write(data)
    return True


# ================================================================ 时间轴补丁

def patch_timeline(path, duration_sec=None):
    """时间轴伪装补丁 (stts/ctts/elst/mdhd + 可选时长伪装)"""
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    traks, moov = _traks(data)
    if not traks:
        return False
    mpos, msize, mhdr = moov
    tpos, tsize, thdr = traks[0]
    mdia = _find_box(data, [b'mdia'], tpos + thdr, tpos + tsize)
    minf = _find_box(data, [b'minf'], mdia[0] + mdia[2], mdia[0] + mdia[1])
    stbl = _find_box(data, [b'stbl'], minf[0] + minf[2], minf[0] + minf[1])
    sp, ss, sh = stbl
    stts = _find_box(data, [b'stts'], sp + sh, sp + ss)
    ctts = _find_box(data, [b'ctts'], sp + sh, sp + ss)
    if not stts or not ctts:
        return False

    spos, ssize, shdr = stts
    cnt = struct.unpack('>I', data[spos + 12:spos + 16])[0]
    deltas = []
    for i in range(cnt):
        c, d = struct.unpack('>II', data[spos + 16 + i * 8:spos + 24 + i * 8])
        deltas.extend([d] * c)
    n = len(deltas)

    cpos, csize, chdr = ctts
    cver = data[cpos + 8]
    ccnt = struct.unpack('>I', data[cpos + 12:cpos + 16])[0]
    offsets = []
    fmt = '>Ii' if cver == 1 else '>II'
    for i in range(ccnt):
        c, o = struct.unpack(fmt, data[cpos + 16 + i * 8:cpos + 24 + i * 8])
        offsets.extend([o] * c)
    if len(offsets) != n:
        return False

    pts = []
    t = 0
    for i in range(n):
        pts.append(t + offsets[i])
        t += deltas[i]

    new_stts_body = struct.pack('>II', 0, 1) + struct.pack('>II', n, TIMELINE_STTS_DELTA)
    new_ctts_body = struct.pack('>II', 0x01000000, n)
    for i in range(n):
        v = pts[i] - TIMELINE_STTS_DELTA * i + TIMELINE_MEDIA_TIME
        new_ctts_body += struct.pack('>Ii', 1, v)

    total_delta = 0
    # ctts first (later in stbl)
    new_ctts_box = struct.pack('>I', chdr + len(new_ctts_body)) + b'ctts' + new_ctts_body
    d1 = len(new_ctts_box) - csize
    data = data[:cpos] + new_ctts_box + data[cpos + csize:]
    total_delta += d1
    # stts (before ctts, position unchanged)
    if len(new_stts_body) + shdr == ssize:
        data[spos + shdr:spos + ssize] = new_stts_body
    else:
        new_stts_box = struct.pack('>I', shdr + len(new_stts_body)) + b'stts' + new_stts_body
        d2 = len(new_stts_box) - ssize
        data = data[:spos] + new_stts_box + data[spos + ssize:]
        total_delta += d2

    if total_delta != 0:
        for apos in (sp, minf[0], mdia[0], tpos, mpos):
            cur = struct.unpack('>I', data[apos:apos + 4])[0]
            struct.pack_into('>I', data, apos, cur + total_delta)
        traks2, moov2 = _traks(data)
        _fix_chunk_offsets(data, moov2, total_delta)

    # elst media_time (in place)
    edts = _find_box(data, [b'edts'], tpos + thdr, tpos + tsize)
    if edts:
        ep, es, eh = edts
        elst = _find_box(data, [b'elst'], ep + eh, ep + es)
        if elst and data[elst[0] + 8] == 0:
            struct.pack_into('>I', data, elst[0] + 20, TIMELINE_MEDIA_TIME)
    # video mdhd duration
    mdhd = _find_box(data, [b'mdhd'], mdia[0] + mdia[2], mdia[0] + mdia[1])
    if mdhd and data[mdhd[0] + 8] == 0:
        struct.pack_into('>I', data, mdhd[0] + 24, pts[-1] + TIMELINE_MEDIA_TIME)

    # duration spoof
    if duration_sec:
        mvhd = _find_box(data, [b'mvhd'], mpos + mhdr, mpos + msize)
        if mvhd and data[mvhd[0] + 8] == 0:
            struct.pack_into('>I', data, mvhd[0] + 24, duration_sec * 1000)
        for t2pos, t2size, t2hdr, t2typ in _iter_boxes(data, mpos + mhdr, mpos + msize):
            if t2typ != b'trak':
                continue
            tkhd = _find_box(data, [b'tkhd'], t2pos + t2hdr, t2pos + t2size)
            if tkhd and data[tkhd[0] + 8] == 0:
                struct.pack_into('>I', data, tkhd[0] + 28, duration_sec * 1000)
            mdia2 = _find_box(data, [b'mdia'], t2pos + t2hdr, t2pos + t2size)
            if not mdia2:
                continue
            mdhd2 = _find_box(data, [b'mdhd'], mdia2[0] + mdia2[2], mdia2[0] + mdia2[1])
            if mdhd2 and data[mdhd2[0] + 8] == 0:
                ts = struct.unpack('>I', data[mdhd2[0] + 20:mdhd2[0] + 24])[0]
                struct.pack_into('>I', data, mdhd2[0] + 24, duration_sec * ts)

    with open(path, 'wb') as f:
        f.write(data)
    return True


# ================================================================ 其它补丁

def patch_btrt(path):
    """btrt: buffer_size=0, max_bitrate=10M, avg_bitrate=0"""
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    i = data.find(b'btrt')
    if i < 8:
        return False
    struct.pack_into('>I', data, i + 4, 0)
    struct.pack_into('>I', data, i + 8, BTRT_MAX_BITRATE)
    struct.pack_into('>I', data, i + 12, 0)
    with open(path, 'wb') as f:
        f.write(data)
    return True


def patch_compressor(path, compressor):
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    info = _extract_avcc(data)
    if not info:
        return False
    avcc_pos = info[0]
    i = data.rfind(b'avc1', 0, avcc_pos)
    if i < 4:
        return False
    epos = i - 4
    name_off = epos + 8 + 42
    raw = compressor.encode('ascii', 'replace')[:31]
    data[name_off] = len(raw)
    data[name_off + 1:name_off + 32] = raw + b'\x00' * (31 - len(raw))
    with open(path, 'wb') as f:
        f.write(data)
    return True


def patch_magic(path, magic_size):
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    i = data.find(b'mdat')
    if i < 4:
        return False
    struct.pack_into('>I', data, i - 4, int(magic_size * 1000000))
    with open(path, 'wb') as f:
        f.write(data)
    return True


# ================================================================ job

class AbOptions(object):
    def __init__(self):
        self.head = ''
        self.material = ''
        self.out_dir = ''
        self.width = 720
        self.height = 1256
        self.intro = 23
        self.live = 1
        self.mat = 1
        self.batch = False
        self.repeat = False
        self.repeats = 1
        self.duration_enabled = False
        self.duration_mode = '固定'
        self.duration_sec = 11880
        self.strip_sei = False
        self.sar_enabled = False
        self.sar_preset = ''
        self.sar_w = 1
        self.sar_h = 1
        self.tool_tag = ''
        self.compressor = DEFAULT_COMPRESSOR
        self.magic_enabled = False
        self.magic_mode = '固定'
        self.magic_size = 670.0
        self.encoder = 'auto'


class AbJob(object):
    def __init__(self, opts, log=None, progress=None, cancel=None):
        self.o = opts
        self.log = log or (lambda m: None)
        self.progress = progress or (lambda p, s='': None)
        self.cancel = cancel or (lambda: False)

    def validate(self):
        if not os.path.isfile(self.o.head):
            return '请选择有效的实拍视频和素材视频'
        if not os.path.isfile(self.o.material):
            return '请选择有效的实拍视频和素材视频'
        if not os.path.isdir(self.o.out_dir):
            return '请选择有效的输出目录'
        return None

    def collect_materials(self):
        if not self.o.batch:
            return [self.o.material]
        d = os.path.dirname(os.path.abspath(self.o.material))
        names = sorted(os.listdir(d))
        out = []
        for n in names:
            if os.path.splitext(n)[1].lower() in VIDEO_EXTS:
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    out.append(p)
        return out or [self.o.material]

    def output_path(self, material_path):
        stem = os.path.splitext(os.path.basename(material_path))[0]
        base = stem + MODE_SUFFIX
        cand = os.path.join(self.o.out_dir, base + '.mp4')
        n = 2
        while os.path.exists(cand):
            cand = os.path.join(self.o.out_dir, '%s_%d.mp4' % (base, n))
            n += 1
        return cand

    def run_once(self, material_path, out_path, progress_base=0.0, progress_span=1.0):
        W, H = self.o.width, self.o.height
        mi = media_info(material_path)
        total = int(mi['duration'] * 60)
        if total <= 0:
            raise RuntimeError('素材时长无效: ' + material_path)
        hi = media_info(self.o.head)
        n_head = max(1, int(hi['duration'] * hi['fps']))
        n_mat = max(1, int(mi['duration'] * mi['fps']))

        reader_h = FrameReader(self.o.head, W, H)
        reader_m = FrameReader(material_path, W, H)

        enc = [FFMPEG, '-y', '-v', 'error',
               '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-s', '%dx%d' % (W, H), '-r', '60', '-i', 'pipe:0']
        if mi['has_audio']:
            enc += ['-i', material_path]
        else:
            enc += ['-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo']
        codec = resolve_encoder(getattr(self.o, 'encoder', 'auto'))
        self.log('编码器: %s' % ('NVENC 硬件加速' if codec == 'h264_nvenc' else 'CPU 软编码'))
        if codec == 'h264_nvenc':
            enc += ['-map', '0:v', '-map', '1:a',
                    '-c:v', 'h264_nvenc', '-rc', 'cbr', '-b:v', VIDEO_BITRATE,
                    '-maxrate', '10M', '-bufsize', '20M',
                    '-profile:v', 'main', '-level', '3.2', '-g', '18', '-bf', '3',
                    '-pix_fmt', 'yuv420p']
        else:
            enc += ['-map', '0:v', '-map', '1:a',
                    '-c:v', 'libx264', '-preset', 'medium', '-b:v', '10M',
                    '-maxrate', '10M', '-bufsize', '20M',
                    '-profile:v', 'main', '-level', '3.2', '-g', '18', '-bf', '3',
                    '-pix_fmt', 'yuv420p']
        enc += ['-vf', 'setsar=1,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709',
                '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-ar', '44100',
                '-video_track_timescale', '15360',
                '-t', '%.6f' % (total / 60.0)]
        if self.o.strip_sei:
            enc += ['-bsf:v', 'filter_units=remove_types=6']
        enc += ['-movflags', '+faststart+negative_cts_offsets', out_path]

        proc = subprocess.Popen(enc, stdin=subprocess.PIPE, stderr=subprocess.PIPE, **_NO_WINDOW_KW)

        try:
            written = 0
            for src, idx in frame_plan(total, self.o.intro, self.o.live, self.o.mat, n_head, n_mat):
                if self.cancel():
                    raise KeyboardInterrupt('cancelled')
                f = (reader_h if src == 'H' else reader_m).frame(idx)
                if f is None:
                    continue
                proc.stdin.write(f)
                written += 1
                if written % 60 == 0:
                    self.progress(progress_base + progress_span * written / total, '')
            proc.stdin.close()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            reader_h.stop()
            reader_m.stop()
            raise
        finally:
            reader_h.stop()
            reader_m.stop()

        err = proc.stderr.read().decode('utf-8', 'replace')
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError('编码失败: ' + err.strip()[:300])

        # post patches
        try:
            patch_sps_in_file(out_path)
        except Exception:
            pass
        try:
            patch_timeline(out_path, self.o.duration_sec if self.o.duration_enabled else None)
        except Exception:
            pass
        try:
            patch_btrt(out_path)
        except Exception:
            pass
        if self.o.compressor and self.o.compressor != DEFAULT_COMPRESSOR:
            try:
                patch_compressor(out_path, self.o.compressor)
            except Exception:
                pass
        if self.o.magic_enabled:
            try:
                patch_magic(out_path, self.o.magic_size)
            except Exception:
                pass
        return written

    def run(self):
        err = self.validate()
        if err:
            raise RuntimeError(err)
        mats = self.collect_materials()
        repeats = self.o.repeats if self.o.repeat else 1
        total_tasks = len(mats) * repeats
        done = 0
        ok = 0
        fail = 0
        for m in mats:
            for _ in range(repeats):
                if self.cancel():
                    break
                out = self.output_path(m)
                try:
                    self.log('开始处理: %s -> %s' % (os.path.basename(m), os.path.basename(out)))
                    self.run_once(m, out, done / total_tasks, 1.0 / total_tasks)
                    ok += 1
                    self.log('完成: %s' % os.path.basename(out))
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    fail += 1
                    self.log('失败: %s (%s)' % (os.path.basename(m), e))
                done += 1
                self.progress(done / total_tasks, '已完成：成功 %d，失败 %d' % (ok, fail))
        return ok, fail


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('head')
    ap.add_argument('material')
    ap.add_argument('outdir')
    ap.add_argument('--w', type=int, default=720)
    ap.add_argument('--h', type=int, default=1256)
    a = ap.parse_args()
    o = AbOptions()
    o.head, o.material, o.out_dir = a.head, a.material, a.outdir
    o.width, o.height = a.w, a.h
    job = AbJob(o, log=lambda m: print(m), progress=lambda p, s: None)
    print(job.run())
