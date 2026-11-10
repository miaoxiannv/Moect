"""Account-scoped DAT keys and browser-compatible media conversion."""
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
import struct
import wave
import uuid
from itertools import islice
from pathlib import Path
from Cryptodome.Cipher import AES
from PIL import Image, UnidentifiedImageError
from .avatars import image_type
from .paths import app_data_root
from wx4.media import (SIG_V1,SIG_V2,V1_KEY,dat_layout,decode_dat,
                       image_prefix,infer_xor_key,find_dat_aes_key,decrypt_dat_prefix,read_dat_key_probe)


def raster_payload(blob):
    """Keep the complete raster, excluding cache metadata AFTER its end marker.

    JPEG APP/EXIF segments may themselves contain thumbnail end markers. Walk
    segments and entropy markers instead of taking the first/last FF D9 match.
    Decoder verification is still required after this structural boundary check.
    """
    size=len(blob)
    if blob.startswith(b'\xff\xd8'):
        pos=2;scan=False;saw_scan=False
        while pos<size:
            if scan:
                pos=blob.find(b'\xff',pos)
                if pos<0:return None
            elif blob[pos]!=0xff:return None
            while pos<size and blob[pos]==0xff:pos+=1
            if pos>=size:return None
            marker=blob[pos];pos+=1
            if scan and (marker==0 or 0xd0<=marker<=0xd7):continue
            if marker==0xd9:return blob[:pos] if saw_scan else None
            if marker in (0,0xd8) or 0xd0<=marker<=0xd7:return None
            if marker==1:continue
            if pos+2>size:return None
            length=int.from_bytes(blob[pos:pos+2],'big')
            if length<2 or pos+length>size:return None
            pos+=length;scan=marker==0xda or (scan and marker==0xdc)
            if scan:saw_scan=True
        return None
    if blob.startswith(b'\x89PNG\r\n\x1a\n'):
        pos=8
        while pos+12<=size:
            length=int.from_bytes(blob[pos:pos+4],'big');end=pos+12+length
            if end>size:return None
            if blob[pos+4:pos+8]==b'IEND':return blob[:end] if length==0 else None
            pos=end
        return None
    if blob.startswith(b'GIF8'):
        if size<13:return None
        pos=13+(3*(1<<((blob[10]&7)+1)) if blob[10]&128 else 0);frames=0
        while pos<size:
            marker=blob[pos];pos+=1
            if marker==0x3b:return blob[:pos] if frames else None
            if marker==0x21:
                if pos>=size:return None
                pos+=1  # Extension label; all extension payloads are subblocks.
            elif marker==0x2c:
                if pos+9>size:return None
                packed=blob[pos+8];pos+=9
                if packed&128:pos+=3*(1<<((packed&7)+1))
                pos+=1;frames+=1  # LZW minimum code size.
            else:return None
            while pos<size:
                length=blob[pos];pos+=1
                if not length:break
                pos+=length
            else:return None
        return None
    if blob.startswith(b'RIFF') and blob[8:12]==b'WEBP':
        end=int.from_bytes(blob[4:8],'little')+8
        return blob[:end] if 12<=end<=size else None
    return blob


def encrypted_media_variants(blob,values,cancel=lambda:None):
    """CDN AES and copied CDN caches use message keys, independently of DAT.

    Produce only strictly padded plaintexts; the caller must still verify and
    decode the entire image. Key bytes and raw metadata stay inside the backend.
    """
    if not blob or len(blob)%16:return
    seen=set()
    for value in list(values)[:32]:
        if not isinstance(value,str):continue
        candidates=[]
        if re.fullmatch(r'(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{48}|[a-fA-F0-9]{64})',value):
            candidates.append(bytes.fromhex(value))
        try:
            encoded=value.encode('ascii')
            if len(encoded) in (16,24,32):candidates.append(encoded)
        except UnicodeError:pass
        for key in candidates:
            if key in seen:continue
            seen.add(key)
            specs=[(AES.MODE_CBC,blob,b'\0'*16),(AES.MODE_CBC,blob,key[:16]),(AES.MODE_ECB,blob,None)]
            if len(blob)>16:specs.append((AES.MODE_CBC,blob[16:],blob[:16]))
            for mode,ciphertext,iv in specs:
                cancel()
                cipher=AES.new(key,mode,**({'iv':iv} if iv is not None else {}))
                plain=cipher.decrypt(ciphertext);pad=plain[-1]
                if 1<=pad<=16 and plain[-pad:]==bytes([pad])*pad:
                    plain=plain[:-pad]
                    if image_prefix(plain) or (len(plain)>12 and plain[4:8]==b'ftyp') or plain.startswith(b'\x1aE\xdf\xa3'):yield plain


def valid_image(blob):
    blob=raster_payload(blob)
    if blob is None:return False
    if not image_type(blob):return False
    try:
        with Image.open(io.BytesIO(blob)) as img:img.verify()
        # JPEG's verify() only checks its header. Decode the pixels as well so
        # an encrypted/corrupt entropy stream cannot be marked as viewable.
        with Image.open(io.BytesIO(blob)) as img:
            img.load()
            if img.format=='GIF':
                for frame in range(1,min(getattr(img,'n_frames',1),1000)):
                    img.seek(frame);img.load()
        return True
    except (OSError,ValueError,SyntaxError,EOFError,UnidentifiedImageError,Image.DecompressionBombError):return False


def media_type(blob):
    image=image_type(blob)
    if image:return image
    if len(blob)>12 and blob[4:8]==b'ftyp':return ('mp4','video/mp4')
    if blob.startswith(b'\x1aE\xdf\xa3'):return ('webm','video/webm')
    if blob.startswith(b'RIFF') and blob[8:12]==b'WAVE':return ('wav','audio/wav')
    return None


class ImageKeys:
    def __init__(self,data,cancel,codec=None,progress=None):
        self.cancel=cancel;self.keys=[];self.xor=None;self.scanned=set()
        self.codec=codec or MediaCodec(cancel);self.progress=progress or (lambda _:None)
        self.scan_remaining=70.;self.rendered=None;self.reason='';self.diagnostics={};self.xor_votes={}
        self.account=Path(data.account_dir());self.wxid=data.wxid;self.xor_search_keys=None
        identity=hashlib.sha256((str(Path(data.account_dir()).resolve())+'\0'+data.wxid).encode()).hexdigest()
        self.path=Path(getattr(data,'cache_root',None) or app_data_root())/'media-cache'/identity/'image-keys.json'
        try:
            state=json.loads(self.path.read_text(encoding='utf-8'))
            self.keys=[bytes.fromhex(k) for k in state.get('keys',[]) if re.fullmatch('[a-fA-F0-9]{32}',k)][:16]
            key=state.get('xor')
            if isinstance(key,int) and 0<=key<=255:self.xor=key
        except (OSError,ValueError,TypeError):pass
        legacy=Path(data.root).parent/'media-key.bin'
        if legacy.is_file():
            try:
                key=legacy.read_bytes()
                if len(key)==16 and key not in self.keys:self.keys.append(key)
            except OSError:pass

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                tmp.write_text(json.dumps({'schema':2,'keys':[k.hex() for k in self.keys[-16:]],'xor':self.xor}),encoding='utf-8')
                os.replace(tmp,self.path)
            finally:
                tmp.unlink(missing_ok=True)
        except OSError:
            # A read-only cache must not prevent exporting a decrypted image.
            pass

    def _xor_from_samples(self,paths):
        if self.xor is not None:return
        marker=tuple(self.keys)
        if marker==self.xor_search_keys:return
        self.xor_search_keys=marker
        # Some conversations contain only WXGF images. A same-account cached
        # JPEG/PNG thumbnail can supply the independent XOR key without
        # copying or exposing any messages from that other conversation.
        def samples():
            yield from paths
            deadline=time.monotonic()+4;count=0
            for directory,dirs,files in os.walk(self.account/'msg/attach',followlinks=False):
                self.cancel()
                dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
                if time.monotonic()>=deadline:return
                for name in files:
                    if not name.lower().endswith('.dat'):continue
                    candidate=Path(directory)/name
                    if candidate.is_symlink():continue
                    yield candidate;count+=1
                    if count>=256 or time.monotonic()>=deadline:return
        for path in samples():
            self.cancel()
            try:
                if path.stat().st_size>8*1024*1024:continue
                raw=path.read_bytes();lay=dat_layout(raw)
            except OSError:continue
            if not lay:continue
            keys=[V1_KEY] if raw.startswith(SIG_V1) else self.keys
            for key in keys:
                prefix=decrypt_dat_prefix(raw,key)
                if prefix is None:continue
                inferred=infer_xor_key(raw,prefix)
                decoded=decode_dat(raw,key,inferred) if inferred is not None else None
                if decoded is not None and valid_image(decoded):
                    self.xor=inferred;self._save();return

    def _remember(self,key,raw):
        if raw.startswith(SIG_V2) and key not in self.keys:
            self.keys.append(key);self.keys=self.keys[-16:];self._save()

    def _xor_candidates(self,raw,prefix):
        """Search the single-byte XOR space; never brute-force an AES key.

        JPEG/PNG provide exact trailers. WXGF uses HEVC emulation-prevention
        sequences to rank candidates, then strict decoding validates the result.
        No default XOR constant is assumed to be correct for every account.
        """
        lay=dat_layout(raw);tail=lay[2];seen=set()
        for value in (infer_xor_key(raw,prefix),self.xor):
            if value is not None and value not in seen:seen.add(value);yield value
        if prefix.startswith(b'\xff\xd8'):
            # WeChat may append cache metadata after JPEG EOI (observed: 24
            # bytes). A valid image need not end at the last byte of the DAT.
            sample=tail[-8192:]
            for pos in range(len(sample)-2,-1,-1):
                k=sample[pos]^0xff
                if sample[pos+1]^k==0xd9 and k not in seen:seen.add(k);yield k
            return
        if prefix.startswith(b'\x89PNG'):
            ending=b'IEND\xaeB`\x82';sample=tail[-8192:]
            for pos in range(len(sample)-len(ending),-1,-1):
                k=sample[pos]^ending[0]
                if k not in seen and all(a^k==b for a,b in zip(sample[pos:pos+len(ending)],ending)):
                    seen.add(k);yield k
            return
        if prefix.startswith(b'GIF8'):
            k=tail[-1]^0x3b
            if k not in seen:seen.add(k);yield k
        if prefix.startswith(b'wxgf'):
            sample=tail[:512*1024]
            scored=[]
            for k in range(256):
                self.cancel()
                score=sample.count(bytes((k,k,k^3)))*2+sample.count(bytes((k,k,k^1)))
                if score:scored.append((score,k))
            # Each shortlisted result must also pass strict FFmpeg decoding.
            # Keep a bounded number of subprocess attempts for large exports.
            for _score,k in sorted(scored,reverse=True)[:8]:
                if k not in seen:seen.add(k);yield k
        else:
            for k in range(256):
                self.cancel()
                if k not in seen:yield k

    def decode(self,raw,source,paths,extra_keys=()):
        self.rendered=None;self.reason='';self.diagnostics={}
        if image_prefix(raw):
            image=self.codec.image(raw)
            if image is not None:self.rendered=image;return raw
        if not raw.startswith((SIG_V1,SIG_V2)):
            legacy=decode_dat(raw)
            if legacy is not None:
                image=self.codec.image(legacy)
                if image is not None:self.rendered=image;return legacy
            for decoded in encrypted_media_variants(raw,extra_keys,self.cancel):
                image=self.codec.image(decoded,strict=True)
                if image is not None:
                    self.rendered=image;self.diagnostics={'format':'cdn-aes','aes_verified':True}
                    return decoded
            self.reason='encrypted_media_unresolved' if extra_keys and len(raw)%16==0 else 'image_invalid'
            self.diagnostics={'format':'non-dat','reason':self.reason}
            return None
        keys=list(self.keys)
        for value in extra_keys:
            if not isinstance(value,str):continue
            try:
                variants=[value.encode('ascii')[:16]]
                if re.fullmatch('[a-fA-F0-9]{32}',value):variants.append(bytes.fromhex(value))
                for key in variants:
                    if len(key)==16 and key not in keys:keys.insert(0,key)
            except (ValueError,UnicodeError):pass
        lay=dat_layout(raw)
        if not lay:self.reason='invalid_dat_layout';return None
        if raw.startswith(SIG_V1):keys=[V1_KEY]
        matching=[key for key in keys if decrypt_dat_prefix(raw,key) is not None]
        if raw.startswith(SIG_V2) and not matching:
            signature=lay[0][:16]
            if signature not in self.scanned and len(self.scanned)<3 and self.scan_remaining>0:
                self.scanned.add(signature)
                self.progress('正在重新获取并严格校验微信图片密钥…')
                started=time.monotonic()
                paths = tuple(islice(paths, 40))
                recovered=find_dat_aes_key(str(source),cancel=self.cancel,timeout=min(35,self.scan_remaining),sample_paths=paths,wxid=self.wxid)
                self.scan_remaining-=time.monotonic()-started
                if recovered:
                    if decrypt_dat_prefix(raw, recovered) is not None:
                        matching = [recovered]
                        self._remember(recovered, raw)
                    else:
                        # A recent cache may use the current key while this
                        # historical original needs an older, unavailable key.
                        for path in paths:
                            self.cancel()
                            probe = read_dat_key_probe(path)
                            if probe is not None and decrypt_dat_prefix(probe, recovered) is not None:
                                self._remember(recovered, probe)
                                break
        self.diagnostics={'format':'v2' if raw.startswith(SIG_V2) else 'v1',
            'aes_verified':bool(matching),'xor_required':bool(lay[2])}
        if not matching:self.reason='aes_key_unavailable';return None
        for key in matching:self._remember(key,raw)
        # Determine the account XOR independently. WXGF and WebP files cannot
        # reliably yield it themselves, whereas cached JPEG/PNG samples can.
        if lay[2] and infer_xor_key(raw,decrypt_dat_prefix(raw,matching[0])) is None:
            self._xor_from_samples(paths)
        for key in matching:
            prefix=decrypt_dat_prefix(raw,key)
            if not lay[2]:return decode_dat(raw,key)
            self.progress('正在恢复图片尾段并校验完整图像…')
            # Only the XOR suffix changes. Avoid decrypting AES 256 times and
            # translate in C rather than allocating Python integers per byte.
            base=prefix+lay[1]
            if not prefix.startswith(b'wxgf'):
                complete=raster_payload(base)
                if complete is not None and valid_image(complete):
                    self.rendered=complete;self.diagnostics['xor_required']=False
                    self.diagnostics['cache_trailer_ignored']=True
                    return complete
            deadline=time.monotonic()+25
            for xk in self._xor_candidates(raw,prefix):
                self.cancel()
                if time.monotonic()>=deadline:break
                decoded=base+lay[2].translate(bytes.maketrans(bytes(range(256)),bytes(i^xk for i in range(256))))
                rendered=self.codec.image(decoded,strict=True)
                if rendered is not None:
                    self.rendered=rendered;self.diagnostics['xor_verified']=True
                    votes=self.xor_votes.setdefault(xk,set())
                    if len(votes)<2:votes.add(hashlib.sha256(raw).digest())
                    # WXGF has no image checksum. Require a second independent
                    # file before persisting a stream-derived XOR account hint.
                    if self.xor!=xk and (not prefix.startswith(b'wxgf') or len(votes)>=2):
                        self.xor=xk;self._save()
                    return decoded
        self.reason='xor_unresolved';self.diagnostics['xor_verified']=False
        return None


def silk_stream(blob, cancel):
    """Normalize complete SILK packets before passing them to the native SDK."""
    raw = blob[1:] if blob.startswith(b'\x02#!SILK_V3') else blob
    if not raw.startswith(b'#!SILK_V3'):
        raise ValueError('Missing SILK header')
    starts = [9]
    if raw[9:11] == b'\r\n':
        starts.append(11)
    elif raw[9:10] == b'\n':
        starts.append(10)
    for start in starts:
        offset = start
        packets = 0
        last_packet = b''
        valid = True
        while offset < len(raw):
            cancel()
            if offset + 2 > len(raw):
                valid = False
                break
            length = struct.unpack_from('<h', raw, offset)[0]
            offset += 2
            if length == -1 and offset == len(raw):
                offset -= 2
                break
            # pysilk's SDK has a 1250-byte per-packet buffer. Zero-length
            # loss packets also need a decoder API this wrapper does not expose.
            if not 0 < length <= 1250 or offset + length > len(raw):
                valid = False
                break
            last_packet = raw[offset - 2:offset + length]
            offset += length
            packets += 1
            if packets > 30000:
                valid = False
                break
        if not valid or not packets:
            continue
        stream = b'\x02#!SILK_V3' + raw[start:offset]
        # The wrapper preloads two packets without checking their boundaries.
        # Duplicate a lone packet for that buffer, then remove its decoded PCM.
        if packets == 1:
            stream += last_packet
        return stream + b'\xff\xff', packets
    raise ValueError('Incomplete or unsupported SILK packet framing')


class MediaCodec:
    def __init__(self, cancel):
        self.cancel = cancel
        self.audio_reason = ''

    def audio(self,blob,destination):
        """Decode WeChat's SILK packets to portable, mono PCM WAV audio.

        FFmpeg does not decode Tencent SILK. Validate packet boundaries before
        calling the native SILK decoder; never label the original as playable.
        """
        self.cancel()
        destination=Path(destination)
        self.audio_reason=''
        if not blob or len(blob)>16*1024*1024:
            self.audio_reason='invalid_audio_size'
            return None
        raw=blob[1:] if blob.startswith(b'\x02#!SILK_V3') else blob
        if raw.startswith(b'#!SILK_V3'):
            try:
                stream,packets=silk_stream(blob,self.cancel)
            except ValueError:
                self.audio_reason='invalid_silk_packets'
                return None
            try:
                import pysilk
            except (ImportError,RuntimeError,OSError):
                self.audio_reason='silk_decoder_unavailable'
                return None
            try:
                pcm=pysilk.decode(stream,sample_rate=24000)
                self.cancel()
                if not pcm or len(pcm)%2 or len(pcm)>144*1024*1024:
                    self.audio_reason='silk_decode_failed'
                    return None
                if packets==1:
                    if len(pcm)%4:
                        self.audio_reason='silk_decode_failed'
                        return None
                    pcm=pcm[:len(pcm)//2]
                with wave.open(str(destination),'wb') as output:
                    output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000)
                    output.writeframes(pcm)
                return destination
            except (RuntimeError,ValueError,OSError,struct.error,wave.Error):
                self.audio_reason='silk_decode_failed'
                destination.unlink(missing_ok=True)
                return None
        # Some imported archives contain an ordinary audio stream instead.
        with tempfile.TemporaryDirectory(prefix='moect-audio-') as tmp:
            source=Path(tmp)/'voice';source.write_bytes(blob)
            try:
                code,_=self._run(['-loglevel','error','-protocol_whitelist','file,pipe',
                    '-i',str(source),'-vn','-ac','1','-ar','24000','-c:a','pcm_s16le',str(destination)],45)
                if code==0 and destination.is_file():
                    with wave.open(str(destination),'rb') as result:
                        if result.getnframes()>0:return destination
            except (OSError,TimeoutError,wave.Error):pass
        destination.unlink(missing_ok=True)
        self.audio_reason='audio_conversion_failed'
        return None

    def _run(self,args,timeout=45):
        import imageio_ffmpeg
        exe=imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryFile() as errors:
            process=subprocess.Popen([exe,'-hide_banner','-nostdin','-y',*args],
                stdout=subprocess.DEVNULL,stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            deadline=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    self.cancel()
                    if time.monotonic()>=deadline:raise TimeoutError('媒体转换超时')
                    time.sleep(.08)
                errors.seek(0)
                return process.returncode,errors.read(65536).decode('utf-8','replace')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:process.kill();process.wait()

    def image(self,blob,strict=False):
        blob=raster_payload(blob)
        if blob is None:return None
        if valid_image(blob):return blob
        if not blob.startswith(b'wxgf'):return None
        # Some containers embed a JPEG/PNG thumbnail. Prefer a complete image
        # before decoding the HEVC payload.
        for signature in (() if strict else (b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n')):
            start=blob.find(signature,4,4096)
            embedded=raster_payload(blob[start:]) if start>=0 else None
            if embedded is not None and valid_image(embedded):return embedded
        starts=list(re.finditer(b'\x00\x00(?:\x00)?\x01',blob[4:]))
        units=[]
        for i,match in enumerate(starts):
            begin=4+match.end();end=4+starts[i+1].start() if i+1<len(starts) else len(blob)
            unit=blob[begin:end]
            if len(unit)>=2 and not unit[0]&128 and unit[1]&7:units.append(unit)
        groups=[];vps=[i for i,u in enumerate(units) if (u[0]>>1)&63==32]
        for i,begin in enumerate(vps):
            group=units[begin:vps[i+1] if i+1<len(vps) else len(units)]
            if any((u[0]>>1)&63<32 for u in group):groups.append(b''.join(b'\0\0\0\1'+u for u in group))
        candidates=sorted(groups,key=len,reverse=True)[:3]
        if units:candidates.append(b''.join(b'\0\0\0\1'+u for u in units))
        if not candidates:candidates=[blob[4:]]
        if strict:candidates=candidates[:1]
        with tempfile.TemporaryDirectory(prefix='wce-wxgf-') as tmp:
            src=Path(tmp)/'image.hevc';dst=Path(tmp)/'image.png'
            for candidate in candidates:
                self.cancel();src.write_bytes(candidate)
                try:
                    dst.unlink(missing_ok=True)
                    checks=['-xerror','-err_detect','explode'] if strict else []
                    if strict:
                        code,_=self._run(['-loglevel','error',*checks,'-protocol_whitelist','file,pipe',
                            '-f','hevc','-i',str(src),'-map','0:v:0','-f','null','-'],5)
                        if code!=0:continue
                    code,_=self._run(['-loglevel','error',*checks,'-protocol_whitelist','file,pipe','-f','hevc','-i',str(src),'-frames:v','1',str(dst)],5 if strict else 30)
                    if code==0 and dst.exists():
                        decoded=dst.read_bytes()
                        if valid_image(decoded):return decoded
                except (OSError,TimeoutError):continue
        return None

    def video(self,source,destination):
        """Keep supported MP4 bytes; transcode HEVC/other codecs to H.264/AAC."""
        try:
            _,info=self._run(['-protocol_whitelist','file,pipe','-i',str(source)],15)
            with open(source,'rb') as stream:head=stream.read(32)
            supported=bool(re.search(r'Video:\s*h264\b[^\r\n]*\byuv420p(?:\(|,|\s)',info)) and (not re.search(r'Audio:',info) or bool(re.search(r'Audio:\s*aac\b',info)))
            if supported and head[4:8]==b'ftyp':return source
            code,_=self._run(['-protocol_whitelist','file,pipe','-i',str(source),'-map','0:v:0','-map','0:a?',
                '-sn','-dn','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
                '-c:a','aac','-movflags','+faststart',str(destination)],600)
            return destination if code==0 and destination.is_file() else None
        except (OSError,TimeoutError):return None

    def sticker(self,blob):
        """Keep original GIF/WebP animation; convert WXGF HEVC frames to GIF."""
        if valid_image(blob):return raster_payload(blob)
        if not blob.startswith(b'wxgf'):return None
        starts=list(re.finditer(b'\x00\x00(?:\x00)?\x01',blob[4:]))
        units=[]
        for i,match in enumerate(starts):
            begin=4+match.end();end=4+starts[i+1].start() if i+1<len(starts) else len(blob)
            unit=blob[begin:end]
            if len(unit)>=2 and not unit[0]&128 and unit[1]&7:units.append(unit)
        boundaries=[i for i,u in enumerate(units) if (u[0]>>1)&63==32]
        candidates=[]
        for i,start in enumerate(boundaries):
            group=units[start:boundaries[i+1] if i+1<len(boundaries) else len(units)]
            if any((u[0]>>1)&63<32 for u in group):candidates.append(b''.join(b'\0\0\0\1'+u for u in group))
        if not candidates:return None
        with tempfile.TemporaryDirectory(prefix='moect-sticker-') as tmp:
            source=Path(tmp)/'animation.hevc';target=Path(tmp)/'animation.gif'
            for stream in sorted(candidates,key=len,reverse=True)[:3]:
                self.cancel();source.write_bytes(stream);target.unlink(missing_ok=True)
                try:
                    code,_=self._run(['-loglevel','error','-protocol_whitelist','file,pipe','-f','hevc','-i',str(source),
                        '-filter_complex','[0:v]scale=w=min(1024\\,iw):h=-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse',
                        '-loop','0',str(target)],60)
                    if code==0 and target.is_file():
                        result=target.read_bytes()
                        if valid_image(result):return result
                except (OSError,TimeoutError):continue
        return None
