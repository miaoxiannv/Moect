"""Image recovery regressions using synthetic DAT files and fake process memory."""
import ctypes
import hashlib
import itertools
import json
import os
import struct
import tempfile
import unittest
from itertools import islice
from pathlib import Path
from unittest.mock import patch

from Cryptodome.Cipher import AES

from core.data import Archive
from core.image_recovery import recent_image_samples
from core.exporting import export_session
from core.media_codec import ImageKeys
from core.resources import ResourceStore
from core.service import Service
from tests.test_core import SESSION
from tests.test_resources import ASSETS, PNG, media_fixture
from wx4 import key as memory
from wx4 import media


def encrypted_image(key):
    padding = 16 - len(PNG) % 16
    ciphertext = AES.new(key, AES.MODE_ECB).encrypt(PNG + bytes([padding]) * padding)
    return media.SIG_V2 + struct.pack('<LL', len(PNG), 0) + b'\0' + ciphertext


class ImageRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = media_fixture(self.root)
        self.library['storage_root'] = str(self.root / 'state')
        self.archive = Archive(self.library)
        self.addCleanup(self.archive.close)
        self.images = self.root / 'account/msg/attach' / hashlib.md5(SESSION.encode()).hexdigest() / '2026-10/Img'
        self.original = self.images / ('a' * 32 + '.dat')
        self.key = b'SyntheticKey1234'
        scanner = patch('core.media_codec.find_dat_aes_key', return_value=None)
        self.scanner = scanner.start()
        self.addCleanup(scanner.stop)

    def test_manual_retry_recovers_keys_while_automatic_preview_stays_local(self):
        self.original.write_bytes(encrypted_image(self.key))
        service = Service(ASSETS, self.root / 'service')
        service.library = self.library
        self.addCleanup(service.shutdown)
        hint = {'session': SESSION, 'shard': 0, 'local_id': 10, 'server_id': '10'}
        self.assertNotIn('url', service.preview_media(hint))
        self.scanner.assert_not_called()
        self.scanner.return_value = self.key
        result = service.preview_media({**hint, 'retry': True})
        self.assertTrue(result.get('url', '').startswith('data:image/png;base64,'))
        self.assertLessEqual(self.scanner.call_args.kwargs['timeout'], 8)
        self.assertEqual(service._cache_readers, 0)

    def test_thumbnail_naming_variants_are_exported_with_the_correct_role(self):
        self.original.unlink()
        names = ['_t.dat', '.t.dat', '_thumb.dat', '.thumb.dat', '.thumb']
        for index, suffix in enumerate(names):
            with self.subTest(suffix=suffix):
                thumbnail = self.images / ('a' * 32 + suffix)
                thumbnail.write_bytes(PNG)
                self.addCleanup(thumbnail.unlink, missing_ok=True)
                output = self.root / ('thumbnail-' + str(index))
                output.mkdir()
                result = export_session(self.archive, self.archive.sessions()[0], {'format': 'json'}, output,
                                        ASSETS, lambda _: None, lambda: None)
                messages = json.loads(Path(result['path']).read_text(encoding='utf-8'))['messages']
                message = next(row for row in messages if row['server_id'] == '10')
                images = [item for item in message['resources'] if item['kind'] == 'image' and item['status'] == 'available']
                self.assertEqual(len(images), 1)
                self.assertEqual(images[0]['role'], 'thumbnail')
                self.assertEqual((output / images[0]['path']).read_bytes(), PNG)
                thumbnail.unlink()

    def test_encrypted_original_does_not_hide_a_viewable_thumbnail(self):
        encrypted = encrypted_image(self.key)
        self.original.write_bytes(encrypted)
        (self.images / ('a' * 32 + '.t.dat')).write_bytes(PNG)
        output = self.root / 'fallback'
        output.mkdir()
        result = export_session(self.archive, self.archive.sessions()[0], {'format': 'json'}, output,
                                ASSETS, lambda _: None, lambda: None)
        messages = json.loads(Path(result['path']).read_text(encoding='utf-8'))['messages']
        message = next(row for row in messages if row['server_id'] == '10')
        thumbnail = next(item for item in message['resources'] if item['role'] == 'thumbnail')
        self.assertEqual((output / thumbnail['path']).read_bytes(), PNG)
        original = next(item for item in message['resources'] if item['status'] == 'encrypted')
        self.assertEqual((output / original['path']).read_bytes(), encrypted)

    def test_recent_sample_key_is_cached_without_claiming_the_old_image_decrypted(self):
        old = encrypted_image(b'OldSyntheticKey1')
        self.original.write_bytes(old)
        recent = self.images / ('b' * 32 + '_t.dat')
        recent.write_bytes(encrypted_image(self.key))
        keys = ImageKeys(self.archive.data, lambda: None)
        self.scanner.return_value = self.key
        self.assertIsNone(keys.decode(old, self.original, iter([recent])))
        self.assertFalse(keys.diagnostics['aes_verified'])
        self.assertIn(self.key, keys.keys)
        self.assertEqual(keys.decode(recent.read_bytes(), recent, [recent]), PNG)
        self.scanner.assert_called_once()
        self.assertIn(recent, self.scanner.call_args.kwargs['sample_paths'])

    def test_unverified_scanner_result_is_not_cached(self):
        raw = encrypted_image(self.key)
        self.original.write_bytes(raw)
        keys = ImageKeys(self.archive.data, lambda: None)
        self.scanner.return_value = b'InvalidImageKey1'
        self.assertIsNone(keys.decode(raw, self.original, []))
        self.assertFalse(keys.keys)
        self.assertFalse(keys.diagnostics['aes_verified'])

    def test_candidate_priority_reaches_a_key_after_a_long_text_run(self):
        payload = b'A' * 200000 + b'\0' + self.key * 2 + b'\0'
        candidates = list(islice(media._dat_key_candidates(payload), 20))
        self.assertIn(self.key, candidates)

    def test_limited_recovery_reaches_a_new_image_in_a_different_conversation(self):
        self.original.write_bytes(encrypted_image(self.key))
        os.utime(self.original, (100, 100))
        for index in range(80):
            old = self.images / ('old-%03d.dat' % index)
            old.write_bytes(encrypted_image(self.key))
            os.utime(old, (100, 100))
        os.utime(self.images, (100, 100))
        current = self.images.parents[2] / ('z' * 32) / '2026-11/Img/recent.dat'
        current.parent.mkdir(parents=True)
        current.write_bytes(encrypted_image(self.key))
        os.utime(current, (2000, 2000))
        os.utime(current.parent, (2000, 2000))
        ticks = itertools.count()
        with patch('core.image_recovery.time.monotonic', side_effect=lambda: next(ticks)):
            samples = recent_image_samples(self.archive.data, seconds=50)
        self.assertIn(current, samples)
        self.assertEqual(samples[0], current)

    def test_recovery_keeps_samples_verified_before_a_directory_timeout(self):
        first = self.images / 'first.dat'
        second = self.images / 'second.dat'
        for path in (first, second):
            path.write_bytes(encrypted_image(self.key))
        clock = [0.]

        def files():
            yield first.name
            yield second.name
            clock[0] = 9.
            yield 'after-deadline.dat'

        def walk(*_args, **_kwargs):
            return iter([(str(self.images), [], files())])

        with patch('core.image_recovery.os.walk', side_effect=walk), \
                patch('core.image_recovery.time.monotonic', side_effect=lambda: clock[0]):
            samples = recent_image_samples(self.archive.data, seconds=8)
        self.assertEqual(set(samples), {first, second})

    def test_recovery_keeps_samples_verified_before_the_file_visit_limit(self):
        first = self.images / 'first.dat'
        second = self.images / 'second.dat'
        for path in (first, second):
            path.write_bytes(encrypted_image(self.key))

        def walk(*_args, **_kwargs):
            files = itertools.chain([first.name, second.name], itertools.repeat(first.name, 50000))
            return iter([(str(self.images), [], files)])

        with patch('core.image_recovery.os.walk', side_effect=walk), \
                patch('core.image_recovery.time.monotonic', return_value=0.):
            samples = recent_image_samples(self.archive.data)
        self.assertEqual(set(samples), {first, second})

    def test_full_sample_set_still_compares_newer_files_in_other_directories(self):
        self.original.write_bytes(encrypted_image(self.key))
        os.utime(self.original, (100, 100))
        for index in range(39):
            old = self.images / ('old-%03d.dat' % index)
            old.write_bytes(encrypted_image(self.key))
            os.utime(old, (100, 100))
        current = self.images.parents[2] / ('z' * 32) / '2026-11/Img/current.dat'
        current.parent.mkdir(parents=True)
        current.write_bytes(encrypted_image(self.key))
        os.utime(current, (3000, 3000))
        # An overwrite can make a file newer without changing its directory time.
        os.utime(current.parent, (500, 500))
        os.utime(self.images, (2000, 2000))
        samples = recent_image_samples(self.archive.data, limit=40)
        self.assertEqual(len(samples), 40)
        self.assertEqual(samples[0], current)

    def test_recovery_samples_known_images_before_later_directory_metadata_times_out(self):
        self.original.write_bytes(encrypted_image(self.key))
        second = self.images / 'second.dat'
        second.write_bytes(encrypted_image(self.key))
        attach = self.images.parents[2]
        slow_month = attach / ('z' * 32) / '2026-11'
        slow_month.mkdir(parents=True)
        clock = [0.]
        is_dir = Path.is_dir
        iterdir = Path.iterdir

        def ordered_children(path):
            if path == attach:
                return iter([self.images.parents[1], slow_month.parent])
            return iterdir(path)

        def read_metadata(path):
            if path == slow_month:
                clock[0] = 9.
            return is_dir(path)

        with patch.object(Path, 'iterdir', ordered_children), \
                patch.object(Path, 'is_dir', read_metadata), \
                patch('core.image_recovery.time.monotonic', side_effect=lambda: clock[0]):
            samples = recent_image_samples(self.archive.data, seconds=8)
        self.assertEqual(clock[0], 9.)
        self.assertEqual(set(samples), {self.original, second})

    def test_directory_discovery_reserves_time_for_reading_images(self):
        self.original.write_bytes(encrypted_image(self.key))
        session = self.images.parents[1]
        for index in range(20):
            (session / ('slow-%02d' % index)).mkdir()
        clock = [0.]
        is_dir = Path.is_dir
        iterdir = Path.iterdir

        def ordered_children(path):
            return iter(sorted(iterdir(path)))

        def read_metadata(path):
            if path.parent == session and path.name.startswith('slow-'):
                clock[0] += 1.
            return is_dir(path)

        with patch.object(Path, 'iterdir', ordered_children), \
                patch.object(Path, 'is_dir', read_metadata), \
                patch('core.image_recovery.time.monotonic', side_effect=lambda: clock[0]):
            samples = recent_image_samples(self.archive.data, seconds=8)
        self.assertIn(self.original, samples)
        self.assertLess(clock[0], 8.)

    def test_account_config_recovers_a_key_absent_from_plaintext_process_memory(self):
        wxid = 'wxid_synthetic_account'
        seed = 0x12345678
        expected = hashlib.md5((str(seed) + wxid).encode()).hexdigest()[:16].encode()
        self.original.write_bytes(encrypted_image(expected))
        payload = bytearray(512)
        struct.pack_into('<I', payload, 56, seed)
        struct.pack_into('<Q', payload, 64, 0x10000 + 256)
        struct.pack_into('<QQ', payload, 80, len(wxid), 31)
        payload[256:256 + len(wxid)] = wxid.encode()
        diagnostics = {}
        self.assertNotIn(expected, payload)
        self.assertEqual(self.search_memory(bytes(payload), wxid, diagnostics), expected)
        self.assertEqual(diagnostics['key_source'], 'account_config')
        self.assertIsNone(self.search_memory(bytes(payload), 'wxid_another_account', {}))

    def test_empty_or_non_v2_first_directory_does_not_prevent_early_sampling(self):
        self.original.write_bytes(encrypted_image(self.key))
        second = self.images / 'second.dat'
        second.write_bytes(encrypted_image(self.key))
        attach = self.images.parents[2]
        first = attach / ('0' * 32) / '2026-11/Img'
        first.mkdir(parents=True)
        slow_month = attach / ('z' * 32) / '2026-11'
        slow_month.mkdir(parents=True)
        is_dir, iterdir = Path.is_dir, Path.iterdir

        def ordered_children(path):
            if path == attach:
                return iter([first.parents[1], self.images.parents[1], slow_month.parent])
            return iterdir(path)

        for kind in ('empty', 'non-v2'):
            with self.subTest(first_directory=kind):
                if kind == 'non-v2':
                    (first / 'plain.dat').write_bytes(PNG)
                clock = [0.]

                def read_metadata(path):
                    if path == slow_month:
                        clock[0] = 9.
                    return is_dir(path)

                with patch.object(Path, 'iterdir', ordered_children), \
                        patch.object(Path, 'is_dir', read_metadata), \
                        patch('core.image_recovery.time.monotonic', side_effect=lambda: clock[0]):
                    samples = recent_image_samples(self.archive.data, seconds=8)
                self.assertEqual(set(samples), {self.original, second})

    def test_account_config_checks_current_chunk_before_later_slow_reads(self):
        wxid = 'wxid_synthetic_account'
        seed = 0x12345678
        expected = hashlib.md5((str(seed) + wxid).encode()).hexdigest()[:16].encode()
        self.original.write_bytes(encrypted_image(expected))
        chunk = 4 * 1024 * 1024
        payload = bytearray(chunk + 1024)
        struct.pack_into('<I', payload, 56, seed)
        struct.pack_into('<Q', payload, 64, 0x10000 + 256)
        struct.pack_into('<QQ', payload, 80, len(wxid), 31)
        payload[256:256 + len(wxid)] = wxid.encode()
        clock = [0.]

        def slow_later_read(offset):
            if offset >= chunk:
                clock[0] = 11.

        diagnostics = {}
        with patch.object(media.time, 'monotonic', side_effect=lambda: clock[0]):
            found = self.search_memory(bytes(payload), wxid, diagnostics, on_read=slow_later_read)
        self.assertEqual(found, expected)
        self.assertEqual(diagnostics['key_source'], 'account_config')

    def test_account_config_reserves_time_to_find_objects_in_earlier_chunks(self):
        wxid = 'wxid_synthetic_account'
        seed = 0x12345678
        expected = hashlib.md5((str(seed) + wxid).encode()).hexdigest()[:16].encode()
        self.original.write_bytes(encrypted_image(expected))
        chunk = 4 * 1024 * 1024
        string_offset = chunk + 512
        payload = bytearray(3 * chunk + 1024)
        struct.pack_into('<I', payload, 56, seed)
        struct.pack_into('<Q', payload, 64, 0x10000 + string_offset)
        struct.pack_into('<QQ', payload, 80, len(wxid), 31)
        payload[string_offset:string_offset + len(wxid)] = wxid.encode()
        clock = [0.]

        def slow_later_read(offset):
            if offset >= 3 * chunk:
                clock[0] = 11.
            elif offset >= 2 * chunk:
                clock[0] = max(clock[0], 6.)

        diagnostics = {}
        with patch.object(media.time, 'monotonic', side_effect=lambda: clock[0]):
            found = self.search_memory(bytes(payload), wxid, diagnostics, on_read=slow_later_read)
        self.assertEqual(found, expected)
        self.assertEqual(diagnostics['key_source'], 'account_config')

    def test_cancel_during_real_config_scan_releases_the_handle(self):
        self.original.write_bytes(encrypted_image(self.key))
        was_read = [False]

        def on_read(_offset):
            was_read[0] = True

        def cancel():
            if was_read[0]:
                raise RuntimeError('cancelled')

        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.search_memory(b'wxid_synthetic_account\0' + bytes(512),
                               'wxid_synthetic_account', {}, on_read=on_read, cancel=cancel)

    def test_inline_account_config_and_full_image_validation(self):
        wxid = 'wxid_short'
        seed = 12345
        expected = hashlib.md5((str(seed) + wxid).encode()).hexdigest()[:16].encode()
        payload = bytearray(512)
        struct.pack_into('<I', payload, 56, seed)
        payload[64:64 + len(wxid)] = wxid.encode()
        struct.pack_into('<QQ', payload, 80, len(wxid), 15)
        valid = encrypted_image(expected)
        self.original.write_bytes(valid)
        self.assertEqual(self.search_memory(bytes(payload), wxid, {}), expected)
        # A valid first block with broken later padding must not accept the seed.
        self.original.write_bytes(valid[:-1] + bytes([valid[-1] ^ 1]))
        self.assertIsNone(self.search_memory(bytes(payload), wxid, {}))

    def test_expired_config_search_preserves_time_for_string_fallback(self):
        self.original.write_bytes(encrypted_image(self.key))
        clock = [0.]

        def consume_config_budget(_handle, _regions, _wxid, deadline, *_args):
            clock[0] = deadline
            return iter(())

        with patch.object(media, '_account_image_key_candidates', side_effect=consume_config_budget), \
                patch.object(media.time, 'monotonic', side_effect=lambda: clock[0]):
            diagnostics = {}
            self.assertEqual(self.search_memory(b'\0' + self.key * 2 + b'\0', 'wxid_synthetic', diagnostics), self.key)
        self.assertEqual(diagnostics['key_source'], 'process_string')

    def test_cancelled_account_search_closes_the_process_and_does_not_save_keys(self):
        self.original.write_bytes(encrypted_image(self.key))
        with patch.object(media, '_account_image_key_candidates', side_effect=RuntimeError('cancelled')):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                self.search_memory(b'\0' + self.key * 2 + b'\0', 'wxid_synthetic', {})
        self.assertFalse(list(self.root.rglob('image-keys.json')))

    def search_memory(self, payload, wxid, diagnostics, *, on_read=None, cancel=None):
        def query(_handle, address, information, _size):
            if address.value:
                return 0
            region = ctypes.cast(information, ctypes.POINTER(memory._MBI)).contents
            region.BaseAddress = 0x10000
            region.RegionSize = len(payload)
            region.State = memory.MEM_COMMIT
            region.Protect = 0x04
            region.Type = 0x20000
            return ctypes.sizeof(region)

        def read(_handle, address, buffer, size, count):
            offset = (address if isinstance(address, int) else address.value) - 0x10000
            if on_read:
                on_read(offset)
            data = payload[offset:offset + size] if offset >= 0 else b''
            ctypes.memmove(buffer, data, len(data))
            ctypes.cast(count, ctypes.POINTER(ctypes.c_size_t))[0] = len(data)
            return bool(data)

        with patch.object(memory, 'enable_debug_privilege'), \
                patch.object(memory.kernel32, 'OpenProcess', return_value=1), \
                patch.object(memory.kernel32, 'CloseHandle') as close, \
                patch.object(memory.kernel32, 'VirtualQueryEx', side_effect=query), \
                patch.object(memory.kernel32, 'ReadProcessMemory', side_effect=read):
            try:
                return media.find_dat_aes_key(str(self.original), pids=[999999], wxid=wxid,
                                              diagnostics=diagnostics, cancel=cancel)
            finally:
                close.assert_called_once_with(1)

    def test_recovery_samples_prefer_recent_files_over_alphabetical_names(self):
        self.original.write_bytes(encrypted_image(self.key))
        os.utime(self.original, (1, 1))
        for index in range(45):
            path = self.images / ('%032x.dat' % index)
            path.write_bytes(encrypted_image(self.key))
            os.utime(path, (10 + index, 10 + index))
        recent = self.images / ('f' * 32 + '_t.dat')
        recent.write_bytes(encrypted_image(self.key))
        os.utime(recent, (100, 100))
        output = self.root / 'recency'
        output.mkdir()
        store = ResourceStore(self.archive.data, SESSION, output)
        self.addCleanup(store.close)
        message = next(row for row in self.archive.messages(SESSION) if row['server_id'] == '10')
        store.enrich(message)
        samples = self.scanner.call_args.kwargs['sample_paths']
        self.assertEqual(len(samples), 40)
        self.assertEqual(samples[0], recent)

    def test_probe_keeps_full_aes_segment_without_loading_the_image_body(self):
        encrypted = encrypted_image(self.key)
        raw = encrypted[:10] + struct.pack('<L', 1000) + encrypted[14:] + b'clear' * 100000 + b'X' * 1000
        self.original.write_bytes(raw)
        probe = media.read_dat_key_probe(self.original)
        self.assertEqual(len(probe), len(encrypted))
        self.assertEqual(media.decrypt_dat_prefix(probe, self.key), PNG)
        self.assertIsNone(media.read_dat_key_probe(self.original, len(encrypted) - 1))
        for truncated in (b'', encrypted[:14], encrypted[:-1]):
            with self.subTest(length=len(truncated)):
                self.original.write_bytes(truncated)
                self.assertIsNone(media.read_dat_key_probe(self.original))

    def test_process_search_requires_full_padding_and_declared_size_validation(self):
        encrypted = encrypted_image(self.key)
        padding = 16 - len(PNG) % 16
        invalid_padding = encrypted[:15] + AES.new(self.key, AES.MODE_ECB).encrypt(PNG + b'\0' * padding)
        invalid_size = encrypted[:6] + struct.pack('<L', len(PNG) + 1) + encrypted[10:]
        for raw in (invalid_padding, invalid_size):
            with self.subTest(header=raw[:15]):
                self.original.write_bytes(raw)
                self.assertIsNone(media.decrypt_dat_prefix(raw, self.key))
                self.assertIsNone(self.fake_process_search([]))

    def test_damaged_original_does_not_hide_valid_sample_with_the_same_first_block(self):
        encrypted = encrypted_image(self.key)
        padding = 16 - len(PNG) % 16
        invalid = encrypted[:15] + AES.new(self.key, AES.MODE_ECB).encrypt(PNG + b'\0' * padding)
        self.original.write_bytes(invalid)
        recent = self.images / 'recent.dat'
        recent.write_bytes(encrypted)
        self.assertEqual(invalid[15:31], encrypted[15:31])
        self.assertEqual(self.fake_process_search([recent]), self.key)

    def test_process_search_checks_recent_templates_with_full_aes_validation(self):
        self.original.write_bytes(encrypted_image(b'OldSyntheticKey1'))
        recent = self.images / 'recent.dat'
        recent.write_bytes(encrypted_image(self.key))
        self.assertEqual(self.fake_process_search([recent]), self.key)

    def fake_process_search(self, samples):
        payload = b'\0' + self.key * 2 + b'\0'

        def query(_handle, address, information, _size):
            if address.value:
                return 0
            region = ctypes.cast(information, ctypes.POINTER(memory._MBI)).contents
            region.BaseAddress = 0x10000
            region.RegionSize = len(payload)
            region.State = memory.MEM_COMMIT
            region.Protect = 0x04
            region.Type = 0x20000
            return ctypes.sizeof(region)

        def read(_handle, _address, buffer, _size, count):
            ctypes.memmove(buffer, payload, len(payload))
            ctypes.cast(count, ctypes.POINTER(ctypes.c_size_t))[0] = len(payload)
            return True

        with patch.object(memory, 'enable_debug_privilege'), \
                patch.object(memory.kernel32, 'OpenProcess', return_value=1), \
                patch.object(memory.kernel32, 'CloseHandle'), \
                patch.object(memory.kernel32, 'VirtualQueryEx', side_effect=query), \
                patch.object(memory.kernel32, 'ReadProcessMemory', side_effect=read):
            return media.find_dat_aes_key(str(self.original), pids=[999999], sample_paths=samples)


if __name__ == '__main__':
    unittest.main()
